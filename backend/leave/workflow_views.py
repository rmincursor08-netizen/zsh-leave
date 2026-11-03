from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import Http404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from zsh_core import settings_schema, workflow as wf
from zsh_core.permissions import EMPLOYEE, HR, MANAGER, RequestFacts, Viewer, serialize_request
from .authorization import (
    HR as HR_ROLE, authorization_context, can_decide_leave_request,
    can_submit_for_employee, scoped_leave_requests,
)
from .calendar_services import CalendarConfigurationError
from .models import (
    EmployeeProfile,
    LeaveRequest,
    LeaveRequestComment,
    LeaveType,
    SystemSetting,
)
from .services import (
    add_comment,
    apply_event,
    correct_request,
    create_amendment,
    run_idempotent,
    submit_request,
)


def _failure(code: str, message: str, status: int, fields: dict | None = None) -> Response:
    body = {"code": code, "message_fa": message}
    if fields:
        body["fields"] = fields
    return Response(body, status=status)


def _date(value, key: str) -> dt.date:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} الزامی است")
    parsed = dt.date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError(f"{key} باید YYYY-MM-DD باشد")
    return parsed


def _positive_id(value, key: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{key} باید شناسه معتبر باشد")
    parsed = int(value)
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{key} باید شناسه معتبر باشد")
    if not 1 <= parsed <= 9_223_372_036_854_775_807:
        raise ValueError(f"{key} باید شناسه معتبر باشد")
    return parsed


def _request_body(data, *, amend: bool = False, defaults: dict | None = None) -> dict:
    allowed = {"start", "end", "reason", "replacementId"}
    if not amend:
        allowed.update({"leaveTypeKey", "workingDays"})
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("بدنه درخواست یا فیلدهای آن معتبر نیست")
    values = {**(defaults or {}), **data}
    start = _date(values.get("start"), "start")
    end = _date(values.get("end"), "end")
    reason = values.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 4000:
        raise ValueError("reason باید متن معتبر و حداکثر ۴۰۰۰ نویسه باشد")
    replacement_id = values.get("replacementId")
    if replacement_id is not None:
        replacement_id = _positive_id(replacement_id, "replacementId")
    working_days = values.get("workingDays")
    if working_days is not None:
        working_days = Decimal(str(working_days))
    return {
        "start": start,
        "end": end,
        "reason": reason,
        "replacement_id": replacement_id,
        "working_days": working_days,
    }


def _hr_for(context, req: LeaveRequest) -> bool:
    return context.has_scope(
        HR_ROLE,
        company_id=req.employee.company_id,
        site_id=req.employee.site_id,
        department_id=req.employee.department_id,
        employee_id=req.employee_id,
    )


def _privacy_setting():
    return SystemSetting.objects.filter(
        key="privacy.employee_sees_own_confidential_type",
    ).values_list("value", flat=True).first()


_UNSET_PRIVACY_SETTING = object()


def _payload(
    req: LeaveRequest,
    context,
    *,
    privacy_setting=_UNSET_PRIVACY_SETTING,
) -> dict:
    is_hr = _hr_for(context, req)
    roles = set()
    if context.employee_id is not None:
        roles.add(EMPLOYEE)
    if context.has_role("manager"):
        roles.add(MANAGER)
    if is_hr:
        roles.add(HR)
    viewer = Viewer(
        user_id=context.user.pk,
        roles=frozenset(roles),
        employee_id=context.employee_id,
        hr_company_ids=frozenset({req.employee.company_id}) if is_hr else frozenset(),
    )
    facts = RequestFacts(
        id=req.pk,
        employee_id=req.employee_id,
        company_id=req.employee.company_id,
        manager_snapshot_id=req.manager_snapshot_id,
        confidential=req.leave_type.confidential,
        status=req.status,
        start=req.start_date,
        end=req.end_date,
        type_key=req.leave_type.key,
        type_label=req.leave_type.label_fa,
        reason=req.reason,
        decision_comment=req.decision_comment,
        replacement_id=req.replacement_id,
        working_days=str(req.working_days),
    )
    settings = settings_schema.defaults()
    if privacy_setting is _UNSET_PRIVACY_SETTING:
        privacy_setting = _privacy_setting()
    if privacy_setting is not None:
        settings["privacy.employee_sees_own_confidential_type"] = privacy_setting
    result = serialize_request(viewer, facts, settings)
    if result is None:
        raise Http404
    result.update({
        "employeeName": req.employee.user.get_full_name() or req.employee.user.get_username(),
        "departmentName": req.employee.department.name if req.employee.department_id else "",
        "replacementName": (
            req.replacement.user.get_full_name() or req.replacement.user.get_username()
            if req.replacement_id and (not req.leave_type.confidential or is_hr)
            else None
        ),
        "supersedesId": req.supersedes_id,
        "createdAt": req.created_at.isoformat(),
        "updatedAt": req.updated_at.isoformat(),
        "confidential": req.leave_type.confidential,
    })
    return result


def _visible_request(request_id: int, context) -> LeaveRequest:
    return scoped_leave_requests(context).select_related(
        "employee__user", "employee__company", "employee__site", "employee__department",
        "leave_type", "replacement__user",
    ).get(pk=request_id)


def _submit_permission(context, employee: EmployeeProfile) -> bool:
    return can_submit_for_employee(context, employee)


def _event_role(context, req: LeaveRequest) -> str:
    if req.employee_id == context.employee_id:
        return wf.EMPLOYEE
    if _hr_for(context, req):
        return wf.HR
    if req.manager_snapshot_id == context.employee_id:
        return wf.MANAGER
    raise Http404


def _comment_allowed(context, req: LeaveRequest) -> bool:
    return not req.leave_type.confidential or _hr_for(context, req)


def _workflow_error(exc: Exception) -> Response:
    if isinstance(exc, wf.PermissionDenied):
        return _failure(exc.code, str(exc), 403)
    if isinstance(exc, (wf.InvalidTransition, wf.ConflictError)):
        return _failure(exc.code, str(exc), 409)
    if isinstance(exc, wf.ValidationError):
        return _failure(exc.code, str(exc), 422)
    if isinstance(exc, CalendarConfigurationError):
        return _failure("calendar_configuration", str(exc), 409)
    if isinstance(exc, DjangoValidationError):
        fields = getattr(exc, "message_dict", {"non_field_errors": exc.messages})
        return _failure("invalid_request", "اطلاعات درخواست معتبر نیست", 400, fields)
    if isinstance(exc, (ValueError, TypeError, InvalidOperation)):
        return _failure("invalid_payload", str(exc), 400)
    raise exc


@api_view(["GET", "POST"])
def leave_requests(request):
    context = authorization_context(request.user)
    if request.method == "GET":
        rows = scoped_leave_requests(context).select_related(
            "employee__user", "employee__company", "employee__site", "employee__department",
            "leave_type", "replacement__user",
        )
        scope = request.query_params.get("scope")
        if scope == "me":
            rows = rows.filter(employee_id=context.employee_id)
        elif scope == "team":
            if context.employee_id is None:
                return _failure("permission_denied", "پرونده کارمندی لازم است", 403)
            rows = rows.filter(manager_snapshot_id=context.employee_id)
        elif scope not in (None, ""):
            return _failure("invalid_scope", "دامنه باید me یا team باشد", 400)
        status = request.query_params.get("status")
        if status:
            rows = rows.filter(status=status)
        query = request.query_params.get("q", "").strip()
        if query:
            rows = rows.filter(
                Q(employee__user__username__icontains=query)
                | Q(employee__user__first_name__icontains=query)
                | Q(employee__user__last_name__icontains=query)
            )
        try:
            page = max(1, int(request.query_params.get("page", "1")))
            page_size = min(100, max(1, int(request.query_params.get("page_size", "20"))))
        except ValueError:
            return _failure("invalid_pagination", "صفحه‌بندی معتبر نیست", 400)
        count = rows.count()
        start = (page - 1) * page_size
        results = [_payload(row, context) for row in rows.order_by("-created_at", "-pk")[start:start + page_size]]
        return Response({"results": results, "count": count, "page": page, "pageSize": page_size})

    data = request.data
    allowed = {
        "employeeId", "leaveTypeKey", "start", "end", "workingDays", "reason", "replacementId",
    }
    if not isinstance(data, dict) or set(data) - allowed:
        return _failure("invalid_payload", "بدنه درخواست یا فیلدهای آن معتبر نیست", 400)
    try:
        fields = _request_body({key: data[key] for key in allowed - {"employeeId"}
                                if key in data})
        if not isinstance(data.get("leaveTypeKey"), str) or not data["leaveTypeKey"]:
            raise ValueError("leaveTypeKey الزامی است")
        employee_id = (
            _positive_id(data["employeeId"], "employeeId")
            if data.get("employeeId") is not None else context.employee_id
        )
        if employee_id is None:
            raise ValueError("پرونده کارمندی برای ثبت درخواست لازم است")
        employee = EmployeeProfile.objects.select_related(
            "company", "site", "department", "manager",
        ).get(pk=employee_id, is_employee=True)
        if not _submit_permission(context, employee):
            raise wf.PermissionDenied("ثبت درخواست برای این کارمند مجاز نیست")
        leave_type = LeaveType.objects.get(key=data["leaveTypeKey"])
        replacement = (
            EmployeeProfile.objects.get(pk=fields["replacement_id"], is_employee=True)
            if fields["replacement_id"] is not None else None
        )
        payload = {
            "employeeId": employee.pk,
            "leaveTypeKey": leave_type.key,
            "start": fields["start"],
            "end": fields["end"],
            "reason": fields["reason"],
            "replacementId": fields["replacement_id"],
            "workingDays": fields["working_days"],
        }
        req = run_idempotent(
            user=request.user,
            key=request.headers.get("Idempotency-Key", ""),
            operation="leave.submit",
            payload=payload,
            model=LeaveRequest,
            callback=lambda: submit_request(
                user=request.user,
                employee=employee,
                leave_type=leave_type,
                start=fields["start"],
                end=fields["end"],
                reason=fields["reason"],
                replacement=replacement,
                working_days=fields["working_days"],
            ),
        )
    except (EmployeeProfile.DoesNotExist, LeaveType.DoesNotExist):
        raise Http404
    except Exception as exc:
        return _workflow_error(exc)
    return Response(_payload(req, context), status=201)


@api_view(["GET", "PATCH"])
def leave_request_detail(request, request_id: int):
    context = authorization_context(request.user)
    try:
        req = _visible_request(request_id, context)
    except LeaveRequest.DoesNotExist:
        raise Http404

    if request.method == "GET":
        return Response(_payload(req, context))
    if req.employee_id != context.employee_id and not _hr_for(context, req):
        return _failure("permission_denied", "فقط کارمند صاحب درخواست یا HR می‌تواند اصلاح کند", 403)
    data = request.data
    allowed = {"leaveTypeKey", "start", "end", "workingDays", "reason", "replacementId"}
    if not isinstance(data, dict) or not data or set(data) - allowed:
        return _failure("invalid_payload", "بدنه درخواست یا فیلدهای آن معتبر نیست", 400)
    try:
        current = {
            "start": req.start_date.isoformat(),
            "end": req.end_date.isoformat(),
            "reason": req.reason,
            "replacementId": req.replacement_id,
        }
        current.update(data)
        fields = _request_body(current)
        leave_type = (
            LeaveType.objects.get(key=data["leaveTypeKey"])
            if "leaveTypeKey" in data else req.leave_type
        )
        replacement = (
            EmployeeProfile.objects.get(pk=fields["replacement_id"], is_employee=True)
            if fields["replacement_id"] is not None else None
        )
        payload = {
            "requestId": req.pk,
            "leaveTypeKey": leave_type.key,
            "start": fields["start"],
            "end": fields["end"],
            "reason": fields["reason"],
            "replacementId": fields["replacement_id"],
            "workingDays": fields["working_days"],
        }
        corrected = run_idempotent(
            user=request.user,
            key=request.headers.get("Idempotency-Key", ""),
            operation="leave.correct",
            payload=payload,
            model=LeaveRequest,
            callback=lambda: correct_request(
                request_id=req.pk,
                user=request.user,
                leave_type=leave_type,
                start=fields["start"],
                end=fields["end"],
                reason=fields["reason"],
                replacement=replacement,
                working_days=fields["working_days"],
            ),
        )
    except LeaveType.DoesNotExist:
        raise Http404
    except EmployeeProfile.DoesNotExist:
        return _failure("invalid_replacement", "جانشین معتبر نیست", 400)
    except Exception as exc:
        return _workflow_error(exc)
    return Response(_payload(corrected, context))


@api_view(["POST"])
def leave_request_action(request, request_id: int, action: str):
    if action == "amend":
        context = authorization_context(request.user)
        try:
            original = _visible_request(request_id, context)
            role = _event_role(context, original)
            if role not in (wf.EMPLOYEE, wf.HR):
                raise wf.PermissionDenied("فقط کارمند صاحب درخواست یا HR می‌تواند اصلاح کند")
            fields = _request_body(request.data, amend=True, defaults={
                "start": original.start_date.isoformat(),
                "end": original.end_date.isoformat(),
                "reason": original.reason,
                "replacementId": original.replacement_id,
            })
            replacement = (
                EmployeeProfile.objects.get(pk=fields["replacement_id"], is_employee=True)
                if fields["replacement_id"] is not None else None
            )
            amendment = run_idempotent(
                user=request.user,
                key=request.headers.get("Idempotency-Key", ""),
                operation="leave.amend",
                payload={
                    "requestId": request_id,
                    "start": fields["start"],
                    "end": fields["end"],
                    "reason": fields["reason"],
                    "replacementId": fields["replacement_id"],
                },
                model=LeaveRequest,
                callback=lambda: create_amendment(
                    request_id=request_id,
                    user=request.user,
                    start=fields["start"],
                    end=fields["end"],
                    reason=fields["reason"],
                    replacement=replacement,
                ),
            )
        except LeaveRequest.DoesNotExist:
            raise Http404
        except EmployeeProfile.DoesNotExist:
            return _failure("invalid_replacement", "جانشین معتبر نیست", 400)
        except Exception as exc:
            return _workflow_error(exc)
        return Response(_payload(amendment, context), status=201)

    event_map = {
        "approve": "approve",
        "reject": "reject",
        "cancel": "cancel",
        "withdraw": "request_withdraw",
        "request_withdraw": "request_withdraw",
        "approve_withdraw": "approve_withdraw",
        "reject_withdraw": "reject_withdraw",
        "hr_cancel": "hr_cancel",
    }
    if action not in event_map:
        return _failure("unknown_action", "عملیات درخواست شناخته‌شده نیست", 404)
    data = request.data
    if not isinstance(data, dict) or set(data) - {"reason"}:
        return _failure("invalid_payload", "بدنه درخواست یا فیلدهای آن معتبر نیست", 400)
    reason = data.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 4000:
        return _failure("invalid_payload", "reason باید متن معتبر و حداکثر ۴۰۰۰ نویسه باشد", 400)
    event = event_map[action]
    context = authorization_context(request.user)
    try:
        req = _visible_request(request_id, context)
        role = _event_role(context, req)
        if role == wf.MANAGER and not can_decide_leave_request(context, req):
            raise wf.PermissionDenied("مدیر مجوز تصمیم‌گیری برای این درخواست را ندارد")
        if role == wf.EMPLOYEE and event not in ("cancel", "request_withdraw"):
            raise wf.PermissionDenied("کارمند مجاز به این رویداد نیست")
        result = run_idempotent(
            user=request.user,
            key=request.headers.get("Idempotency-Key", ""),
            operation=f"leave.{event}",
            payload={"requestId": request_id, "event": event, "reason": reason},
            model=LeaveRequest,
            callback=lambda: apply_event(
                request_id=request_id, user=request.user, event=event, reason=reason,
            ),
        )
    except LeaveRequest.DoesNotExist:
        raise Http404
    except Exception as exc:
        return _workflow_error(exc)
    return Response(_payload(result, context))


@api_view(["POST"])
def leave_request_amend(request, request_id: int):
    try:
        context = authorization_context(request.user)
        original = _visible_request(request_id, context)
        role = _event_role(context, original)
        if role not in (wf.EMPLOYEE, wf.HR):
            raise wf.PermissionDenied("فقط کارمند صاحب درخواست یا HR می‌تواند اصلاح کند")
        fields = _request_body(request.data, amend=True, defaults={
            "start": original.start_date.isoformat(),
            "end": original.end_date.isoformat(),
            "reason": original.reason,
            "replacementId": original.replacement_id,
        })
        replacement = (
            EmployeeProfile.objects.get(pk=fields["replacement_id"], is_employee=True)
            if fields["replacement_id"] is not None else None
        )
        payload = {
            "requestId": request_id,
            "start": fields["start"],
            "end": fields["end"],
            "reason": fields["reason"],
            "replacementId": fields["replacement_id"],
        }
        amendment = run_idempotent(
            user=request.user,
            key=request.headers.get("Idempotency-Key", ""),
            operation="leave.amend",
            payload=payload,
            model=LeaveRequest,
            callback=lambda: create_amendment(
                request_id=request_id,
                user=request.user,
                start=fields["start"],
                end=fields["end"],
                reason=fields["reason"],
                replacement=replacement,
            ),
        )
    except LeaveRequest.DoesNotExist:
        raise Http404
    except EmployeeProfile.DoesNotExist:
        return _failure("invalid_replacement", "جانشین معتبر نیست", 400)
    except Exception as exc:
        return _workflow_error(exc)
    return Response(_payload(amendment, context), status=201)


@api_view(["GET", "POST"])
def leave_request_comments(request, request_id: int):
    context = authorization_context(request.user)
    try:
        req = _visible_request(request_id, context)
    except LeaveRequest.DoesNotExist:
        raise Http404
    if not _comment_allowed(context, req):
        return _failure("permission_denied", "مکاتبه درباره نوع محرمانه فقط با HR مجاز است", 403)
    if request.method == "GET":
        comments = req.comments.select_related("actor").order_by("created_at", "pk")
        return Response([{
            "id": comment.pk,
            "authorName": comment.actor.get_full_name() or comment.actor.get_username(),
            "body": comment.body,
            "createdAt": comment.created_at.isoformat(),
        } for comment in comments])

    data = request.data
    body = data.get("body") if isinstance(data, dict) and set(data) == {"body"} else None
    if not isinstance(body, str) or not body.strip() or len(body) > 4000:
        return _failure("invalid_payload", "متن نظر الزامی و حداکثر ۴۰۰۰ نویسه است", 400)
    try:
        comment = run_idempotent(
            user=request.user,
            key=request.headers.get("Idempotency-Key", ""),
            operation="leave.comment",
            payload={"requestId": request_id, "body": body.strip()},
            model=LeaveRequestComment,
            callback=lambda: add_comment(
                request_id=request_id, user=request.user, body=body,
            ),
        )
    except LeaveRequest.DoesNotExist:
        raise Http404
    except Exception as exc:
        return _workflow_error(exc)
    return Response({
        "id": comment.pk,
        "authorName": comment.actor.get_full_name() or comment.actor.get_username(),
        "body": comment.body,
        "createdAt": comment.created_at.isoformat(),
    }, status=201)


@api_view(["GET"])
def leave_request_events(request, request_id: int):
    context = authorization_context(request.user)
    try:
        req = _visible_request(request_id, context)
    except LeaveRequest.DoesNotExist:
        raise Http404
    reveal_reason = not req.leave_type.confidential or _hr_for(context, req)
    events = req.events.select_related("actor").order_by("created_at", "pk")
    return Response([{
        "id": event.pk,
        "event": event.event,
        "fromStatus": event.from_status,
        "toStatus": event.to_status,
        "reason": event.reason if reveal_reason else "",
        "actorName": (
            event.actor.get_full_name() or event.actor.get_username() if event.actor_id else None
        ),
        "createdAt": event.created_at.isoformat(),
    } for event in events])
