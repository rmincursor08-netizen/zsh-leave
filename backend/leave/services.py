"""لایه سرویس: تنها نقطه تغییر وضعیت درخواست‌ها. تراکنش + قفل ردیف؛ منطق از zsh_core."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from zsh_core import workflow as wf
from . import audit_services
from .authorization import (
    HR, authorization_context, can_decide_leave_request, can_submit_for_employee,
    scoped_leave_requests,
)
from .calendar_services import calculate_working_days, resolve_policy_version
from .employment_services import is_employed_on, organization_localdate
from .entitlement_services import (
    internal_idempotency_key,
    release_request,
    reserve_request,
    transition_request_ledger,
)
from .models import (
    EmployeeProfile,
    LeaveRequest,
    LeaveRequestComment,
    LeaveRequestEvent,
    LeaveType,
    WorkflowIdempotency,
)
from .notification_services import queue_request_event_delivery


class ServiceError(Exception):
    pass


ACTIVE = [wf.PENDING, wf.ESCALATED, wf.APPROVED, wf.WITHDRAWAL_PENDING]


def _origin(actor_role: str) -> str:
    return actor_role  # employee | manager | hr | system


def _log(req, user, role, event, from_s, to_s, reason="", *, before=None, after=None):
    event_row = LeaveRequestEvent.objects.create(
        request=req, actor=user, origin=_origin(role), event=event,
        from_status=from_s, to_status=to_s, reason=reason,
    )
    queue_request_event_delivery(event_row)
    audit_services.record_audit(
        actor=user, origin=_origin(role), action=f"leave.{event}",
        entity="LeaveRequest", entity_id=req.pk,
        before=before if before is not None else {"status": from_s},
        after=after if after is not None else {
            "status": to_s,
            "employee_id": req.employee_id,
            "leave_type_id": req.leave_type_id,
            "policy_version_id": req.policy_version_id,
            "start_date": req.start_date,
            "end_date": req.end_date,
            "working_days": req.working_days,
            "replacement_id": req.replacement_id,
            "supersedes_id": req.supersedes_id,
        },
    )


def run_idempotent(*, user, key: str, operation: str, payload: dict, model, callback):
    """Run one API mutation once for an actor/key pair, replaying its original resource."""
    if not isinstance(key, str) or not key or len(key) > 128:
        raise wf.ValidationError("هدر Idempotency-Key الزامی و حداکثر ۱۲۸ نویسه است")
    fingerprint = hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()

    with transaction.atomic():
        try:
            with transaction.atomic():
                record = WorkflowIdempotency.objects.create(
                    actor=user,
                    key=key,
                    operation=operation,
                    fingerprint=fingerprint,
                    resource_type=model._meta.label_lower,
                    resource_id=1,
                )
        except IntegrityError:
            record = WorkflowIdempotency.objects.select_for_update().get(actor=user, key=key)
            if record.operation != operation or record.fingerprint != fingerprint:
                raise wf.ConflictError("این Idempotency-Key قبلاً برای عملیات دیگری استفاده شده است")
            if record.resource_type != model._meta.label_lower:
                raise wf.ConflictError("منبع ثبت‌شده برای این Idempotency-Key معتبر نیست")
            if record.resource_id is None:
                raise wf.ConflictError("عملیات دارای Idempotency-Key هنوز کامل نشده است")
            return model.objects.get(pk=record.resource_id)

        resource = callback()
        record.resource_type = model._meta.label_lower
        record.resource_id = resource.pk
        record.save(update_fields=["resource_type", "resource_id"])
        return resource


def _validate_new(employee: EmployeeProfile, ltype: LeaveType, start, end, days: Decimal,
                  replacement, role: str, policy_allow_past: bool, reason: str = ""):
    if not ltype.enabled:
        raise wf.ValidationError("این نوع مرخصی فعال نیست")
    if role == wf.EMPLOYEE and not ltype.employee_requestable:
        raise wf.PermissionDenied("این نوع مرخصی را فقط HR ثبت می‌کند")
    if not is_employed_on(employee, start) or not is_employed_on(employee, end):
        raise wf.ValidationError("کارمند فعال نیست")
    if start > end:
        raise wf.ValidationError("تاریخ شروع نباید بعد از پایان باشد")
    if days <= 0:
        raise wf.ValidationError("تعداد روز باید مثبت باشد")
    if ltype.requires_note and not reason.strip():
        raise wf.ValidationError("درج توضیح برای این نوع مرخصی الزامی است")
    if (end - start).days + 1 < days:
        raise wf.ValidationError("تعداد روز از طول بازه بیشتر است")
    if end < organization_localdate() and role != wf.HR and not policy_allow_past:
        raise wf.ValidationError("ثبت مرخصی گذشته مجاز نیست")
    if ltype.requires_replacement:
        if replacement is None:
            raise wf.ValidationError("انتخاب جانشین الزامی است")
        if (replacement.pk == employee.pk
                or not is_employed_on(replacement, start)
                or not is_employed_on(replacement, end)
                or replacement.company_id != employee.company_id):
            raise wf.ValidationError("جانشین نامعتبر است")


def _role_for_employee(context, employee: EmployeeProfile) -> str:
    if context.employee_id == employee.pk:
        return wf.EMPLOYEE
    if can_submit_for_employee(context, employee):
        return wf.HR
    raise wf.PermissionDenied("کاربر مجوز ثبت درخواست برای این کارمند را ندارد")


def _locked_scoped_requests(context):
    return LeaveRequest.objects.filter(
        pk__in=scoped_leave_requests(context).values("pk"),
    )


@transaction.atomic
def submit_request(*, user, employee: EmployeeProfile, leave_type: LeaveType,
                   start, end, working_days: Decimal | None = None, reason: str = "",
                   replacement: EmployeeProfile | None = None,
                   manager_away: bool = False, policy_allow_past: bool = False) -> LeaveRequest:
    # قفل ردیف کارمند: ثبت‌های هم‌زمان یک کارمند سریالی می‌شوند (جلوگیری از هم‌پوشانی)
    employee = EmployeeProfile.objects.select_for_update(of=("self",)).select_related(
        "company", "site", "department", "manager",
    ).get(pk=employee.pk)
    leave_type = LeaveType.objects.select_for_update().get(pk=leave_type.pk)
    context = authorization_context(user)
    role = _role_for_employee(context, employee)

    policy_version = resolve_policy_version(employee, start)
    calculation = calculate_working_days(
        employee=employee, start=start, end=end, policy_version=policy_version,
    )
    if working_days is not None and Decimal(working_days) != calculation.days:
        raise wf.ValidationError("تعداد روز مرخصی با تقویم و الگوی کاری معتبر یکسان نیست")
    working_days = calculation.days
    _validate_new(
        employee, leave_type, start, end, working_days, replacement, role,
        policy_allow_past, reason,
    )
    overlap = LeaveRequest.objects.filter(employee=employee, status__in=ACTIVE,
                                          start_date__lte=end, end_date__gte=start)
    if overlap.exists():
        raise wf.ConflictError("با درخواست دیگری برای همین کارمند هم‌پوشانی دارد")
    ctx = wf.Context(actor=role, requires_approval=leave_type.requires_approval,
                     confidential=leave_type.confidential,
                     has_manager=employee.manager_id is not None, manager_away=manager_away)
    status = wf.initial_status(ctx)
    req = LeaveRequest.objects.create(
        employee=employee, manager_snapshot=employee.manager, leave_type=leave_type,
        policy_version=policy_version, start_date=start, end_date=end,
        working_days=working_days, status=status,
        reason=reason, replacement=replacement,
        decided_by=user if status == wf.APPROVED else None,
        decided_at=timezone.now() if status == wf.APPROVED else None)
    _log(req, user, role, "submit", "", status, "")
    transition_request_ledger(
        request=req,
        old_status="",
        new_status=status,
        actor=user,
        reason=reason,
        idempotency_key=f"request:{req.pk}:submit",
    )
    return req


@transaction.atomic
def correct_request(*, request_id: int, user, leave_type: LeaveType, start, end,
                    reason: str = "", replacement: EmployeeProfile | None = None,
                    working_days: Decimal | None = None) -> LeaveRequest:
    context = authorization_context(user)
    req = _locked_scoped_requests(context).select_for_update(of=("self",)).select_related(
        "employee__company", "employee__site", "employee__department", "leave_type",
    ).get(pk=request_id)
    leave_type = LeaveType.objects.select_for_update().get(pk=leave_type.pk)
    role = _role_for_employee(context, req.employee)
    if role not in (wf.EMPLOYEE, wf.HR):
        raise wf.PermissionDenied("فقط کارمند صاحب درخواست یا HR می‌تواند اصلاح کند")
    if req.status != wf.PENDING:
        raise wf.InvalidTransition("فقط درخواست در انتظار قابل اصلاح است")

    policy_version = resolve_policy_version(req.employee, start)
    calculation = calculate_working_days(
        employee=req.employee, start=start, end=end, policy_version=policy_version,
    )
    if working_days is not None and Decimal(working_days) != calculation.days:
        raise wf.ValidationError("تعداد روز مرخصی با تقویم و الگوی کاری معتبر یکسان نیست")
    _validate_new(
        req.employee, leave_type, start, end, calculation.days, replacement, role, False, reason,
    )
    overlap = LeaveRequest.objects.filter(
        employee=req.employee,
        status__in=ACTIVE,
        start_date__lte=end,
        end_date__gte=start,
    ).exclude(pk=req.pk)
    if overlap.exists():
        raise wf.ConflictError("با درخواست دیگری برای همین کارمند هم‌پوشانی دارد")

    release_request(
        request=req,
        actor=user,
        reason="اصلاح درخواست",
        idempotency_key=internal_idempotency_key(f"request:{req.pk}:correction-release"),
    )
    before = {
        "leave_type_id": req.leave_type_id,
        "start_date": req.start_date.isoformat(),
        "end_date": req.end_date.isoformat(),
        "working_days": str(req.working_days),
        "replacement_id": req.replacement_id,
    }
    reason_changed = req.reason != reason
    req.leave_type = leave_type
    req.policy_version = policy_version
    req.start_date = start
    req.end_date = end
    req.working_days = calculation.days
    req.reason = reason
    req.replacement = replacement
    req.save()
    _log(
        req, user, role, "correct", req.status, req.status, before=before, after={
            "leave_type_id": req.leave_type_id,
            "start_date": req.start_date.isoformat(),
            "end_date": req.end_date.isoformat(),
            "working_days": str(req.working_days),
            "replacement_id": req.replacement_id,
            "reason_changed": reason_changed,
        },
    )
    reserve_request(
        request=req,
        actor=user,
        reason="اصلاح درخواست",
        idempotency_key=internal_idempotency_key(f"request:{req.pk}:correction-reserve"),
    )
    return req


@transaction.atomic
def create_amendment(*, request_id: int, user, start, end, reason: str = "",
                     replacement: EmployeeProfile | None = None) -> LeaveRequest:
    context = authorization_context(user)
    original = _locked_scoped_requests(context).select_for_update(of=("self",)).select_related(
        "employee__company", "employee__site", "employee__department", "leave_type",
    ).get(pk=request_id)
    role = _role_for_employee(context, original.employee)
    in_flight = original.amendments.exclude(status__in=[wf.REJECTED, wf.CANCELLED]).exists()
    amend_context = wf.Context(
        actor=role, confidential=original.leave_type.confidential,
        amendment_in_flight=in_flight,
    )
    wf.can_start_amendment(original.status, amend_context)

    employee = EmployeeProfile.objects.select_for_update(of=("self",)).select_related(
        "company", "site", "department", "manager",
    ).get(pk=original.employee_id)
    replacement = replacement if replacement is not None else original.replacement
    policy_version = resolve_policy_version(employee, start)
    calculation = calculate_working_days(
        employee=employee, start=start, end=end, policy_version=policy_version,
    )
    _validate_new(
        employee, original.leave_type, start, end, calculation.days, replacement, role, False,
        reason,
    )
    overlap = LeaveRequest.objects.filter(
        employee=employee,
        status__in=ACTIVE,
        start_date__lte=end,
        end_date__gte=start,
    ).exclude(pk=original.pk)
    if overlap.exists():
        raise wf.ConflictError("با درخواست دیگری برای همین کارمند هم‌پوشانی دارد")

    ctx = wf.Context(
        actor=role,
        requires_approval=original.leave_type.requires_approval,
        confidential=original.leave_type.confidential,
        has_manager=original.manager_snapshot_id is not None,
    )
    status = wf.initial_status(ctx)
    amendment = LeaveRequest.objects.create(
        employee=employee,
        manager_snapshot_id=original.manager_snapshot_id,
        leave_type=original.leave_type,
        policy_version=policy_version,
        start_date=start,
        end_date=end,
        working_days=calculation.days,
        status=status,
        reason=reason,
        replacement=replacement,
        supersedes=original,
        decided_by=user if status == wf.APPROVED else None,
        decided_at=timezone.now() if status == wf.APPROVED else None,
    )
    _log(amendment, user, role, "amend", "", status)
    transition_request_ledger(
        request=amendment,
        old_status="",
        new_status=status,
        actor=user,
        reason=reason,
        idempotency_key=f"request:{amendment.pk}:amend",
    )
    if status == wf.APPROVED:
        original_old_status = original.status
        original.status = wf.CANCELLED
        original.decided_by = user
        original.decided_at = timezone.now()
        original.decision_comment = reason
        original.save()
        _log(original, user, role, "amend_approve", wf.APPROVED, wf.CANCELLED, reason)
        transition_request_ledger(
            request=original,
            old_status=original_old_status,
            new_status=original.status,
            actor=user,
            reason=reason,
            idempotency_key=f"request:{original.pk}:amend-reversal",
        )
    return amendment


@transaction.atomic
def apply_event(*, request_id: int, user, event: str, reason: str = "") -> LeaveRequest:
    context = authorization_context(user)
    req = _locked_scoped_requests(context).select_for_update(of=("self",)).select_related(
        "employee__company", "employee__site", "employee__department", "leave_type",
    ).get(pk=request_id)
    if req.employee_id == context.employee_id:
        role = wf.EMPLOYEE
    elif context.has_scope(
        HR,
        company_id=req.employee.company_id,
        site_id=req.employee.site_id,
        department_id=req.employee.department_id,
        employee_id=req.employee_id,
    ):
        role = wf.HR
    elif req.manager_snapshot_id == context.employee_id:
        role = wf.MANAGER
    else:
        raise LeaveRequest.DoesNotExist
    if role == wf.MANAGER and not can_decide_leave_request(context, req):
        raise wf.PermissionDenied("مدیر مجوز تصمیم‌گیری برای این درخواست را ندارد")
    if role == wf.EMPLOYEE and event not in ("cancel", "request_withdraw"):
        raise wf.PermissionDenied("کارمند مجاز به این رویداد نیست")
    in_flight = req.amendments.exclude(status__in=[wf.REJECTED, wf.CANCELLED]).exists()
    ctx = wf.Context(actor=role, reason=reason, confidential=req.leave_type.confidential,
                     amendment_in_flight=in_flight)
    old = req.status
    new = wf.transition(old, event, ctx)       # استثنا → تراکنش برمی‌گردد

    original = None
    original_new_status = None
    if req.supersedes_id and event in ("approve", "reject"):
        original = LeaveRequest.objects.select_for_update().get(pk=req.supersedes_id)
        original_new_status, amendment_status = wf.resolve_amendment(
            original.status, event == "approve",
        )
        if amendment_status != new:
            raise wf.InvalidTransition("وضعیت اصلاحیه با نتیجه گردش کار سازگار نیست")

    req.status = new
    if event in ("approve", "reject", "approve_withdraw", "reject_withdraw", "hr_cancel"):
        req.decided_by, req.decided_at, req.decision_comment = user, timezone.now(), reason
    req.save()
    _log(req, user, role, event, old, new, reason)
    transition_request_ledger(
        request=req,
        old_status=old,
        new_status=new,
        actor=user,
        reason=reason,
        idempotency_key=f"request:{req.pk}:{event}",
    )
    if original is not None and original_new_status is not None:
        original_old_status = original.status
        original.status = original_new_status
        if event == "approve":
            original.decided_by, original.decided_at = user, timezone.now()
            original.decision_comment = reason
        original.save()
        _log(
            original, user, role, f"amendment_{event}", original_old_status,
            original_new_status, reason,
        )
        transition_request_ledger(
            request=original,
            old_status=original_old_status,
            new_status=original_new_status,
            actor=user,
            reason=reason,
            idempotency_key=f"request:{original.pk}:amendment-{event}",
        )
    return req


@transaction.atomic
def add_comment(*, request_id: int, user, body: str) -> LeaveRequestComment:
    context = authorization_context(user)
    req = _locked_scoped_requests(context).select_for_update(of=("self",)).select_related(
        "employee__company", "employee__site", "employee__department", "leave_type",
    ).get(pk=request_id)
    is_hr = context.has_scope(
        HR,
        company_id=req.employee.company_id,
        site_id=req.employee.site_id,
        department_id=req.employee.department_id,
        employee_id=req.employee_id,
    )
    if req.leave_type.confidential and not is_hr:
        raise wf.PermissionDenied("مکاتبه درباره نوع محرمانه فقط با HR مجاز است")
    role = wf.HR if is_hr else (
        wf.EMPLOYEE if req.employee_id == context.employee_id else wf.MANAGER
    )
    comment = LeaveRequestComment.objects.create(
        request=req, actor=user, body=body.strip(),
    )
    audit_services.record_model_change(
        actor=user,
        origin=_origin(role),
        action="leave_request.comment_added",
        instance=comment,
    )
    _log(
        req, user, role, "comment", req.status, req.status,
        before={"status": req.status},
        after={"status": req.status, "comment_id": comment.pk},
    )
    return comment


def escalate_overdue(request_ids: list[int]) -> int:
    """Job ارجاع: بروزرسانی شرطی تا تصمیم هم‌زمان مدیر بازنویسی نشود."""
    n = 0
    for rid in request_ids:
        with transaction.atomic():
            changed = LeaveRequest.objects.filter(pk=rid, status=wf.PENDING).update(status=wf.ESCALATED)
            if changed:
                req = LeaveRequest.objects.select_related("leave_type").get(pk=rid)
                _log(req, None, wf.SYSTEM, "escalate", wf.PENDING, wf.ESCALATED, "")
                n += 1
    return n
