from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, SuspiciousFileOperation, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import CharField, Count, ProtectedError, Q
from django.db.models.functions import Cast
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from zsh_core import settings_schema
from zsh_core.permissions import HR as POLICY_HR, Viewer, visible_balance_types
from . import admin_services
from . import audit_services
from . import bradford_services
from .attachment_security import (
    InvalidAttachment,
    validate_attachment,
)
from .attachment_scanning import scan_stored_attachment
from .authorization import (
    MANAGE_GLOBAL_LEAVE_TYPES, READ_LEAVE_TYPES, authorization_context, can_access_request_attachments,
    has_permission, hr_operation_employees, scoped_employees, scoped_hr_employees,
    scoped_exec_employees, scoped_hr_leave_requests, scoped_leave_requests,
)
from .entitlement_services import (
    EntitlementConflict,
    EntitlementLedgerError,
    adjust_entitlement,
    create_entitlement,
    create_entitlement_for_import,
    entitlement_balance,
)
from .entitlement_import_services import validate_entitlement_import
from .employment_services import (
    employees_employed_on,
    employment_status_history,
    is_employed_on,
    organization_localdate,
)
from .invitation_services import (
    InvitationError,
    invite_employee,
    normalize_email,
    synchronize_employment_access,
)
from .hierarchy_services import lock_organizational_hierarchy
from .serializers import LeaveTypeMutationSerializer
from .models import (
    AuditEvent,
    BradfordNote,
    Company,
    ContractPeriod,
    Department,
    EmployeeInvitation,
    EntitlementEvent,
    EmployeeProfile,
    LeaveEntitlement,
    LeaveRequestAttachment,
    LeaveRequest,
    LeaveRequestComment,
    LeaveRequestEvent,
    LeaveType,
    NotificationRead,
    OrganizationSite,
    RoleAssignment,
    SystemSetting,
    attachment_upload_path,
)
from .throttling import rate_limited

User = get_user_model()
logger = logging.getLogger(__name__)


def _failure(code: str, message: str, status: int, fields: dict | None = None) -> Response:
    body = {"code": code, "message_fa": message}
    if fields:
        body["fields"] = fields
    return Response(body, status=status)


def _context(request):
    return authorization_context(request.user)


def _admin(context) -> bool:
    return context.has_role("admin")


def _hr(context, employee: EmployeeProfile) -> bool:
    return context.has_scope(
        "hr", company_id=employee.company_id, site_id=employee.site_id,
        department_id=employee.department_id, employee_id=employee.pk,
    )


def _employee_rows(context):
    rows = EmployeeProfile.objects.select_related(
        "user", "company", "site", "department", "manager__user",
    ).filter(is_employee=True)
    if _admin(context):
        return rows
    return rows.filter(pk__in=scoped_employees(context).values("pk"))


def _site_rows(context):
    if _admin(context):
        return OrganizationSite.objects.all()

    filters = Q(pk__in=_employee_rows(context).exclude(site_id=None).values("site_id"))
    for assignment in context.assignments:
        if assignment.role != "hr":
            continue
        if assignment.scope == RoleAssignment.SCOPE_COMPANY:
            filters |= Q(company_id=assignment.company_id)
        elif assignment.scope == RoleAssignment.SCOPE_SITE:
            filters |= Q(pk=assignment.site_id)
    return OrganizationSite.objects.filter(filters)


def _can_manage_company_sites(context, company_id: int) -> bool:
    return _admin(context) or context.has_scope(
        "hr", company_id=company_id, site_id=None, department_id=None, employee_id=None,
    )


def _employee_json(employee: EmployeeProfile, *, include_contact: bool = False) -> dict:
    result = {
        "id": employee.pk,
        "userId": employee.user_id,
        "displayName": employee.user.get_full_name().strip() or employee.user.get_username(),
        "firstName": employee.user.first_name,
        "lastName": employee.user.last_name,
        "personnelCode": employee.personnel_code,
        "companyId": employee.company_id,
        "companyName": employee.company.name,
        "siteId": employee.site_id,
        "siteName": employee.site.name if employee.site_id else None,
        "departmentId": employee.department_id,
        "departmentName": employee.department.name if employee.department_id else "",
        "managerId": employee.manager_id,
        "positionName": "",
        "managerName": (
            employee.manager.user.get_full_name().strip() or employee.manager.user.get_username()
            if employee.manager_id else None
        ),
        "startDate": employee.start_date.isoformat(),
        "endDate": employee.end_date.isoformat() if employee.end_date else None,
        "isActive": is_employed_on(employee),
        "accountStatus": employee.account_status,
    }
    if include_contact:
        result["email"] = employee.user.email
    return result


def _leave_type_json(item: LeaveType) -> dict:
    return {
        "id": item.pk, "key": item.key, "labelFa": item.label_fa, "color": item.color,
        "countsAgainstBalance": item.counts_against_balance, "requiresApproval": item.requires_approval,
        "requiresNote": item.requires_note, "requiresReplacement": item.requires_replacement,
        "employeeRequestable": item.employee_requestable, "confidential": item.confidential,
        "enabled": item.enabled, "sortOrder": item.sort_order,
    }


def _entitlement_json(item: LeaveEntitlement) -> dict:
    return {
        "id": item.pk, "employeeId": item.employee_id,
        "employeeName": item.employee.user.get_full_name().strip() or item.employee.user.get_username(),
        "leaveTypeKey": item.leave_type.key, "leaveTypeLabel": item.leave_type.label_fa,
        "year": item.year, "base": str(item.base_days), "carryOver": str(item.carry_over_days),
        "adjustment": str(item.manual_adjustment),
    }


def _balance_json(item: LeaveEntitlement) -> dict:
    balance = entitlement_balance(item)
    return {
        **_entitlement_json(item),
        "total": str(balance["total"]), "used": str(balance["used"]),
        "pending": str(balance["reserved"]), "remaining": str(balance["remaining"]),
        "usable": str(balance["available"]),
    }


def _visible_entitlement_rows(context, rows) -> list[LeaveEntitlement]:
    visible = []
    for item in rows:
        employee = item.employee
        is_scoped_hr = _hr(context, employee)
        viewer = Viewer(
            user_id=context.user.pk,
            roles=frozenset({POLICY_HR}) if is_scoped_hr else frozenset(),
            hr_company_ids=frozenset({employee.company_id}) if is_scoped_hr else frozenset(),
        )
        if visible_balance_types(
            viewer,
            employee.company_id,
            [{"id": item.leave_type_id, "confidential": item.leave_type.confidential}],
        ):
            visible.append(item)
    return visible


@api_view(["GET", "POST", "PATCH"])
def leave_types(request, type_id: int | None = None):
    context = _context(request)
    if request.method == "GET":
        if not has_permission(context, READ_LEAVE_TYPES):
            return _failure("permission_denied", "مشاهده انواع مرخصی مجاز نیست", 403)
        rows = LeaveType.objects.all()
        if not has_permission(context, MANAGE_GLOBAL_LEAVE_TYPES):
            rows = rows.filter(enabled=True)
            if not context.has_role("hr"):
                rows = rows.filter(employee_requestable=True)
        return Response([_leave_type_json(item) for item in rows.order_by("sort_order", "pk")])

    if not has_permission(context, MANAGE_GLOBAL_LEAVE_TYPES):
        return _failure("permission_denied", "مدیریت انواع مرخصی مجاز نیست", 403)
    data = request.data
    allowed = {
        "key", "labelFa", "color", "countsAgainstBalance", "requiresApproval", "requiresNote",
        "requiresReplacement", "employeeRequestable", "confidential", "enabled", "sortOrder",
        "reason",
    }
    if not isinstance(data, dict) or not data or set(data) - allowed:
        return _failure("invalid_payload", "فیلدهای نوع مرخصی معتبر نیست", 400)
    serializer = LeaveTypeMutationSerializer(data=data)
    if not serializer.is_valid():
        fields = {
            key: str(messages[0])
            for key, messages in serializer.errors.items()
            if messages
        }
        return _failure("invalid_leave_type", "اطلاعات نوع مرخصی معتبر نیست", 400, fields)
    validated = dict(serializer.validated_data)
    reason = validated.pop("reason")
    try:
        item = admin_services.save_leave_type(
            actor=request.user,
            leave_type_id=type_id,
            data=validated,
            reason=reason,
            correlation_id=request.headers.get("X-Correlation-ID", ""),
        )
    except LeaveType.DoesNotExist:
        raise Http404
    except PermissionDenied:
        return _failure("permission_denied", "مدیریت انواع مرخصی مجاز نیست", 403)
    except admin_services.LeaveTypeConflict as exc:
        return _failure("leave_type_conflict", str(exc), 409)
    except IntegrityError:
        return _failure("leave_type_conflict", "تغییر نوع مرخصی با داده‌های موجود تعارض دارد", 409)
    except ValidationError as exc:
        if hasattr(exc, "message_dict"):
            fields = {
                key: str(messages[0])
                for key, messages in exc.message_dict.items()
                if messages
            }
        else:
            fields = {}
        return _failure("invalid_leave_type", "اطلاعات نوع مرخصی معتبر نیست", 400, fields)
    except (TypeError, ValueError):
        return _failure("invalid_leave_type", "اطلاعات نوع مرخصی معتبر نیست", 400)
    return Response(_leave_type_json(item), status=201 if type_id is None else 200)


@api_view(["GET"])
def balances(request):
    context = _context(request)
    try:
        year = int(request.query_params.get("year", ""))
        employee_id = int(request.query_params["employee"]) if request.query_params.get("employee") else context.employee_id
    except ValueError:
        return _failure("invalid_filter", "سال یا شناسه کارمند معتبر نیست", 400)
    if employee_id is None:
        return _failure("employee_profile_required", "پرونده کارمندی لازم است", 403)
    employee = scoped_employees(context).select_related(
        "company", "site", "department",
    ).filter(pk=employee_id).first()
    if employee is None:
        raise Http404
    entries = LeaveEntitlement.objects.select_related("employee__user", "leave_type").filter(
        employee=employee, year=year, leave_type__counts_against_balance=True,
    ).order_by("leave_type__sort_order", "pk")
    from .entitlement_services import EntitlementReconciliationError

    try:
        visible_entries = _visible_entitlement_rows(context, entries)
        return Response([_balance_json(item) for item in visible_entries])
    except EntitlementReconciliationError:
        return _failure("entitlement_reconciliation", "دفترکل سهمیه نیازمند بررسی است", 409)


@api_view(["GET"])
def entitlements(request):
    context = _context(request)
    if not context.has_role("hr"):
        return _failure("permission_denied", "مشاهده سهمیه‌های کارکنان مجاز نیست", 403)
    try:
        year = int(request.query_params.get("year", ""))
    except ValueError:
        return _failure("invalid_year", "سال معتبر نیست", 400)
    employee_ids = hr_operation_employees(context).values("pk")
    rows = LeaveEntitlement.objects.select_related(
        "employee__user", "employee__company", "employee__site", "employee__department", "leave_type",
    ).filter(employee_id__in=employee_ids, year=year).order_by(
        "employee__user__last_name", "employee__user__first_name", "leave_type__sort_order",
    )
    return Response([
        _entitlement_json(item) for item in _visible_entitlement_rows(context, rows)
    ])


@api_view(["POST"])
def entitlement_adjustment(request, entitlement_id: int):
    context = _context(request)
    if not (context.has_role("hr") or _admin(context)):
        return _failure("permission_denied", "اصلاح سهمیه مجاز نیست", 403)
    data = request.data
    if not isinstance(data, dict) or set(data) != {"days", "reason"}:
        return _failure("invalid_payload", "روز و دلیل الزامی است", 400)
    item = LeaveEntitlement.objects.select_related("employee").filter(pk=entitlement_id).first()
    if item is None or not hr_operation_employees(context).filter(pk=item.employee_id).exists():
        raise Http404
    try:
        event = adjust_entitlement(
            entitlement_id=item.pk, component="manual_adjustment", delta=data["days"],
            actor=request.user, reason=data["reason"],
            idempotency_key=request.headers.get("Idempotency-Key") or str(uuid4()),
        )
    except EntitlementLedgerError as exc:
        return _failure("invalid_adjustment", str(exc), 400)
    item.refresh_from_db()
    return Response(_entitlement_json(item))


@rate_limited("bulk_import")
@api_view(["POST"])
def entitlement_import(request):
    context = _context(request)
    if not (context.has_role("hr") or _admin(context)):
        return _failure("permission_denied", "ورود گروهی سهمیه مجاز نیست", 403)
    data = request.data
    if not isinstance(data, dict) or set(data) != {"csv"} or not isinstance(data["csv"], str):
        return _failure("invalid_payload", "متن CSV الزامی است", 400)
    dry_run = request.query_params.get("dry_run") == "1"
    validation = validate_entitlement_import(data["csv"], context)
    if dry_run:
        validation.preview["dryRun"] = True
        return Response(validation.preview)

    conflict_line = None
    try:
        with transaction.atomic():
            current_context = _context(request)
            if not (current_context.has_role("hr") or _admin(current_context)):
                return _failure("permission_denied", "ورود گروهی سهمیه مجاز نیست", 403)

            employee_ids = {employee.pk for _, employee, _, _, _ in validation.valid_rows}
            leave_type_ids = {leave_type.pk for _, _, leave_type, _, _ in validation.valid_rows}
            locked_employee_ids = set(
                hr_operation_employees(current_context).filter(
                    pk__in=employee_ids,
                ).select_for_update().order_by("pk").values_list("pk", flat=True)
            )
            locked_leave_type_ids = set(
                LeaveType.objects.filter(
                    pk__in=leave_type_ids, enabled=True,
                ).select_for_update().order_by("pk").values_list("pk", flat=True)
            )

            current_validation = validate_entitlement_import(data["csv"], current_context)
            preview = current_validation.preview
            valid = current_validation.valid_rows
            fresh_employee_ids = {employee.pk for _, employee, _, _, _ in valid}
            fresh_leave_type_ids = {leave_type.pk for _, _, leave_type, _, _ in valid}
            if not fresh_employee_ids.issubset(locked_employee_ids):
                preview["rows"].append({
                    "line": 1, "employee": "", "type": "", "year": 0, "days": "",
                    "ok": False, "errorCode": "stale_employee_scope",
                    "error": "محدوده دسترسی کارمند هنگام ثبت تغییر کرده است",
                })
                preview["rejected"] += 1
            if not fresh_leave_type_ids.issubset(locked_leave_type_ids):
                preview["rows"].append({
                    "line": 1, "employee": "", "type": "", "year": 0, "days": "",
                    "ok": False, "errorCode": "stale_leave_type",
                    "error": "وضعیت نوع مرخصی هنگام ثبت تغییر کرده است",
                })
                preview["rejected"] += 1
            if preview["rejected"]:
                preview["dryRun"] = False
                return Response(preview)

            for line, employee, leave_type, year, days in valid:
                try:
                    create_entitlement_for_import(
                        employee=employee,
                        leave_type=leave_type,
                        year=year,
                        base_days=days,
                        actor=request.user,
                        reason="ورود گروهی سهمیه",
                        idempotency_key=f"import:{request.user.pk}:{uuid4()}:{line}",
                    )
                except EntitlementConflict:
                    conflict_line = line
                    raise
    except EntitlementConflict:
        return Response({
            "dryRun": False,
            "rows": [{
                "line": conflict_line,
                "employee": "",
                "type": "",
                "year": 0,
                "days": "",
                "ok": False,
                "errorCode": "duplicate_existing",
                "error": "سهمیه این کارمند، نوع مرخصی و سال هم‌زمان ثبت شده است",
            }],
            "accepted": 0,
            "rejected": 1,
        }, status=409)
    except (EntitlementLedgerError, IntegrityError) as exc:
        return _failure("entitlement_import_failed", str(exc), 409)
    preview["dryRun"] = False
    return Response(preview)


@rate_limited("employee_search")
@api_view(["GET", "POST", "PATCH"])
def employees(request, employee_id: int | None = None):
    context = _context(request)
    rows = _employee_rows(context)
    if request.method == "GET":
        if employee_id is not None:
            employee = rows.filter(pk=employee_id).first()
            if employee is None:
                raise Http404
            return Response(
                _employee_json(
                    employee,
                    include_contact=context.has_role("hr") or _admin(context),
                ),
            )
        query = request.query_params.get("q", "").strip()
        if query:
            rows = rows.filter(
                Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query)
                | Q(personnel_code__icontains=query) | Q(user__username__icontains=query),
            )
        return Response([
            _employee_json(
                item,
                include_contact=context.has_role("hr") or _admin(context),
            )
            for item in rows.order_by("user__last_name", "user__first_name")
        ])
    if not (context.has_role("hr") or _admin(context)):
        return _failure("permission_denied", "مدیریت پرونده کارکنان مجاز نیست", 403)
    data = request.data
    if not isinstance(data, dict):
        return _failure("invalid_payload", "بدنه باید یک شیء JSON باشد", 400)
    try:
        employee = hr_operation_employees(context).get(pk=employee_id) if employee_id is not None else None
    except EmployeeProfile.DoesNotExist:
        raise Http404
    allowed = {
        "displayName", "firstName", "lastName", "personnelCode", "companyId", "siteId",
        "departmentId", "managerId", "startDate", "endDate", "email",
    }
    has_display_name = isinstance(data.get("displayName"), str) and bool(data["displayName"].strip())
    has_first_and_last = (
        isinstance(data.get("firstName"), str) and bool(data["firstName"].strip())
        and isinstance(data.get("lastName"), str) and bool(data["lastName"].strip())
    )
    if (
        set(data) - allowed
        or not {"personnelCode", "companyId"}.issubset(data)
        or not (has_display_name or has_first_and_last)
    ):
        return _failure(
            "invalid_payload",
            "نام و نام خانوادگی یا نام کامل، کد پرسنلی و شرکت الزامی است",
            400,
        )
    if employee is not None and "email" in data:
        return _failure("invalid_payload", "برای تغییر ایمیل از فرایند دعوت استفاده کنید", 400)
    invitation_email = None
    if "email" in data:
        try:
            invitation_email = normalize_email(data["email"])
        except InvitationError:
            return _failure("invalid_employee", "نشانی ایمیل معتبر نیست", 400)
        if User.objects.filter(email__iexact=invitation_email).exists():
            return _failure("duplicate_email", "این ایمیل قبلاً برای حساب دیگری ثبت شده است", 409)
    if (
        ("displayName" in data and not isinstance(data["displayName"], str))
        or ("firstName" in data and not isinstance(data["firstName"], str))
        or ("lastName" in data and not isinstance(data["lastName"], str))
        or not isinstance(data["personnelCode"], str)
    ):
        return _failure("invalid_payload", "نام و کد پرسنلی باید متن باشند", 400)
    try:
        company = Company.objects.get(pk=data["companyId"])
        department_id = (
            int(data["departmentId"]) if data.get("departmentId")
            else employee.department_id if employee is not None and "departmentId" not in data
            else None
        )
        department = (
            Department.objects.get(pk=department_id) if department_id is not None else None
        )
        if data.get("siteId") is not None:
            if isinstance(data["siteId"], bool):
                raise ValueError("siteId باید شناسه معتبر باشد")
            site = OrganizationSite.objects.get(pk=int(data["siteId"]))
        else:
            site = employee.site if employee is not None and "siteId" not in data else None
        manager_id = data.get(
            "managerId", employee.manager_id if employee is not None else None,
        )
        if manager_id is not None:
            if isinstance(manager_id, bool):
                raise ValueError("managerId باید شناسه معتبر باشد")
            manager_id = int(manager_id)
        hr_destination_scope = context.has_scope(
            "hr", company_id=company.pk, site_id=site.pk if site else None,
            department_id=department_id, employee_id=employee.pk if employee else None,
        )
        manager_destination_scope = bool(
            context.employee_id is not None
            and company.pk == context.employee.company_id
            and manager_id == context.employee_id
        )
        if not _admin(context) and not (
            hr_destination_scope or manager_destination_scope
        ):
            return _failure("permission_denied", "ثبت در این شرکت یا محدوده سازمانی مجاز نیست", 403)
        code = data["personnelCode"].strip()
        duplicate_code = EmployeeProfile.objects.filter(
            company=company,
            personnel_code=code,
        )
        if employee is not None:
            duplicate_code = duplicate_code.exclude(pk=employee.pk)
        if duplicate_code.exists():
            return _failure("duplicate_employee", "کد پرسنلی قبلاً در این شرکت ثبت شده است", 409)
        manager = (
            hr_operation_employees(context).get(pk=manager_id)
            if manager_id else None
        )
        start = dt.date.fromisoformat(data["startDate"]) if data.get("startDate") else (
            employee.start_date if employee else organization_localdate()
        )
        end = dt.date.fromisoformat(data["endDate"]) if data.get("endDate") else (
            employee.end_date if employee else None
        )
        first_name = (
            data["firstName"].strip() if isinstance(data.get("firstName"), str)
            else data.get("displayName", "").strip()
        )
        last_name = data.get("lastName", "").strip() if isinstance(data.get("lastName"), str) else ""
        display_name = data.get("displayName", "").strip() if isinstance(data.get("displayName"), str) else ""
        if not first_name or not code:
            raise ValueError("نام و کد پرسنلی الزامی است")
        if "firstName" in data and not last_name:
            raise ValueError("نام خانوادگی الزامی است")
        created = employee is None
        with transaction.atomic():
            if employee is not None:
                employee = hr_operation_employees(context).select_for_update().get(pk=employee.pk)
                before = audit_services.snapshot(employee)
            else:
                before = None
            if employee is None:
                username = f"emp-{company.pk}-{code}"
                if User.objects.filter(username=username).exists():
                    return _failure("duplicate_user", "حساب کاربری با این کد وجود دارد", 409)
                user = User(
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    email=invitation_email or "",
                    is_active=False,
                )
                user.set_unusable_password()
                user.save()
                employee = EmployeeProfile(
                    user=user, company=company, personnel_code=code, start_date=start,
                    account_status=EmployeeProfile.ACCOUNT_CREATED,
                )
            else:
                user = employee.user
                display_name_changed = (
                    user.first_name != first_name or user.last_name != last_name
                )
                user.first_name = first_name
                user.last_name = last_name
                user.save(update_fields=["first_name", "last_name"])
                employee.company, employee.personnel_code = company, code
                employee.start_date = start
            employee.site, employee.department, employee.manager, employee.end_date = (
                site, department, manager, end,
            )
            if department and department.company_id != company.pk:
                raise ValueError("واحد باید متعلق به شرکت انتخاب‌شده باشد")
            if manager and manager.company_id != company.pk:
                raise ValueError("مدیر باید متعلق به شرکت انتخاب‌شده باشد")
            employee.full_clean()
            employee.save()
            synchronize_employment_access(employee=employee, actor=request.user)
            after = audit_services.snapshot(employee)
            if not created:
                after["displayNameChanged"] = display_name_changed
            audit_services.record_audit(
                actor=request.user,
                origin="admin" if _admin(context) else "hr",
                action="employee.create" if created else "employee.update",
                entity="EmployeeProfile",
                entity_id=employee.pk,
                before=before,
                after=after,
            )
    except (
        Company.DoesNotExist, Department.DoesNotExist, OrganizationSite.DoesNotExist,
        EmployeeProfile.DoesNotExist,
    ):
        return _failure("related_object_not_found", "شرکت، سایت، واحد یا مدیر پیدا نشد", 404)
    except (ValueError, TypeError, ValidationError) as exc:
        return _failure("invalid_employee", str(exc), 400)
    except IntegrityError:
        return _failure("duplicate_employee", "کد پرسنلی قبلاً در این شرکت ثبت شده است", 409)
    invitation_result = None
    if (
        created
        and invitation_email
        and employee.account_status != EmployeeProfile.ACCOUNT_OFFBOARDED
    ):
        try:
            invitation_result = invite_employee(
                employee_id=employee.pk,
                actor=request.user,
            )
        except InvitationError:
            return _failure("invitation_failed", "دعوت کاربر ایجاد نشد", 500)
        employee.refresh_from_db()
    result = _employee_json(employee, include_contact=True)
    if invitation_result:
        result["invitationStatus"] = invitation_result.status
        result["invitationDelivery"] = (
            "sent" if invitation_result.status == "sent" else "unavailable"
        )
        result["invitationMessageFa"] = (
            "دعوت ارسال شد"
            if invitation_result.status == "sent"
            else "دعوت ذخیره شد اما سرویس ایمیل در دسترس نیست"
        )
        if invitation_result.development_url:
            result["developmentActivationUrl"] = invitation_result.development_url
    return Response(result, status=201 if employee_id is None else 200)


def _company_json(item: Company) -> dict:
    return {"id": item.pk, "code": item.code, "name": item.name, "isActive": item.is_active}


def _site_json(item: OrganizationSite) -> dict:
    return {
        "id": item.pk, "companyId": item.company_id, "code": item.code,
        "name": item.name, "isActive": item.is_active,
    }


def _department_json(item: Department) -> dict:
    return {"id": item.pk, "companyId": item.company_id, "code": item.code, "name": item.name,
            "parentId": item.parent_id}


@api_view(["GET", "POST", "PATCH"])
def companies(request, company_id: int | None = None):
    context = _context(request)
    if request.method == "GET":
        if _admin(context):
            rows = Company.objects.all()
        else:
            rows = Company.objects.filter(pk__in=_employee_rows(context).values("company_id"))
        return Response([_company_json(item) for item in rows.order_by("name")])
    if not _admin(context):
        return _failure("permission_denied", "مدیریت شرکت‌ها فقط برای مدیر سامانه مجاز است", 403)
    data = request.data
    if not isinstance(data, dict) or set(data) - {"code", "name", "isActive"} or not {"code", "name"} <= set(data):
        return _failure("invalid_payload", "کد و نام شرکت الزامی است", 400)
    if (not isinstance(data["code"], str) or not isinstance(data["name"], str)
            or ("isActive" in data and not isinstance(data["isActive"], bool))):
        return _failure("invalid_payload", "کد و نام باید متن و وضعیت باید بولی باشد", 400)
    try:
        with transaction.atomic():
            item = (
                Company.objects.select_for_update().filter(pk=company_id).first()
                if company_id else Company()
            )
            if item is None:
                raise Http404
            before = audit_services.snapshot(item) if item.pk is not None else None
            item.code, item.name = data["code"].strip(), data["name"].strip()
            if "isActive" in data:
                item.is_active = data["isActive"]
            item.full_clean()
            item.save()
            audit_services.record_model_change(
                actor=request.user,
                origin="admin",
                action="company.create" if before is None else "company.update",
                instance=item,
                before=before,
            )
    except Http404:
        raise
    except (ValidationError, IntegrityError):
        return _failure("invalid_company", "اطلاعات شرکت معتبر نیست", 400)
    return Response(_company_json(item), status=201 if company_id is None else 200)


@api_view(["GET", "POST", "PATCH", "DELETE"])
def sites(request, site_id: int | None = None):
    context = _context(request)
    if request.method == "GET":
        rows = _site_rows(context)
        if site_id is not None:
            item = rows.filter(pk=site_id).first()
            if item is None:
                raise Http404
            return Response(_site_json(item))
        return Response([_site_json(item) for item in rows.order_by("company_id", "name")])

    if request.method == "DELETE":
        if site_id is None:
            return _failure("invalid_site", "برای حذف باید شناسه سایت مشخص باشد", 400)
        try:
            with transaction.atomic():
                lock_organizational_hierarchy()
                item = _site_rows(context).select_for_update().filter(pk=site_id).first()
                if item is None:
                    raise Http404
                if not _can_manage_company_sites(context, item.company_id):
                    return _failure("permission_denied", "مدیریت سایت‌های این شرکت مجاز نیست", 403)
                before = audit_services.snapshot(item)
                deleted_site_id = item.pk
                item.delete()
                audit_services.record_audit(
                    actor=request.user,
                    origin="admin" if _admin(context) else "hr",
                    action="organization_site.delete",
                    entity="OrganizationSite",
                    entity_id=deleted_site_id,
                    before=before,
                )
        except Http404:
            raise
        except ProtectedError:
            return _failure(
                "site_in_use", "سایتی که سوابق سازمانی به آن ارجاع دارند قابل حذف نیست", 409,
            )
        return Response(status=204)

    data = request.data
    allowed = {"companyId", "code", "name", "isActive"}
    if not isinstance(data, dict) or set(data) - allowed or not {"companyId", "code", "name"} <= set(data):
        return _failure("invalid_payload", "شرکت، کد و نام سایت الزامی است", 400)
    if (
        not isinstance(data["code"], str) or not isinstance(data["name"], str)
        or ("isActive" in data and not isinstance(data["isActive"], bool))
    ):
        return _failure("invalid_payload", "کد و نام باید متن و وضعیت باید بولی باشد", 400)
    try:
        with transaction.atomic():
            lock_organizational_hierarchy()
            item = None
            if site_id is not None:
                item = _site_rows(context).select_for_update().filter(pk=site_id).first()
                if item is None:
                    raise Http404
                if not _can_manage_company_sites(context, item.company_id):
                    return _failure(
                        "permission_denied", "مدیریت سایت‌های این شرکت مجاز نیست", 403,
                    )

            company = Company.objects.get(pk=data["companyId"])
            if not _can_manage_company_sites(context, company.pk):
                return _failure(
                    "permission_denied", "ساخت یا انتقال سایت به این شرکت مجاز نیست", 403,
                )
            if item is not None and item.company_id != company.pk:
                if not _admin(context):
                    return _failure(
                        "permission_denied", "انتقال سایت به شرکت دیگر مجاز نیست", 403,
                    )

            item = item or OrganizationSite()
            before = audit_services.snapshot(item) if item.pk is not None else None
            item.company = company
            item.code = data["code"].strip()
            item.name = data["name"].strip()
            if "isActive" in data:
                item.is_active = data["isActive"]
            item.full_clean()
            item.save()
            audit_services.record_model_change(
                actor=request.user,
                origin="admin" if _admin(context) else "hr",
                action="organization_site.create" if before is None else "organization_site.update",
                instance=item,
                before=before,
            )
    except Http404:
        raise
    except Company.DoesNotExist:
        return _failure("related_object_not_found", "شرکت پیدا نشد", 404)
    except (ValidationError, IntegrityError):
        return _failure("invalid_site", "اطلاعات سایت معتبر نیست", 400)
    return Response(_site_json(item), status=201 if site_id is None else 200)


@api_view(["GET", "POST", "PATCH"])
def departments(request, department_id: int | None = None):
    context = _context(request)
    if request.method == "GET":
        rows = Department.objects.all() if _admin(context) else Department.objects.filter(
            company_id__in=_employee_rows(context).values("company_id"),
        )
        return Response([_department_json(item) for item in rows.order_by("company_id", "name")])
    if not _admin(context):
        return _failure("permission_denied", "مدیریت واحدها فقط برای مدیر سامانه مجاز است", 403)
    data = request.data
    if not isinstance(data, dict) or set(data) - {"code", "name", "companyId", "parentId"} or not {
        "code", "name", "companyId",
    } <= set(data):
        return _failure("invalid_payload", "کد، نام و شرکت الزامی است", 400)
    if not isinstance(data["code"], str) or not isinstance(data["name"], str):
        return _failure("invalid_payload", "کد و نام باید متن باشند", 400)
    try:
        with transaction.atomic():
            item = (
                Department.objects.select_for_update().filter(pk=department_id).first()
                if department_id else Department()
            )
            if item is None:
                raise Http404
            before = audit_services.snapshot(item) if item.pk is not None else None
            item.company = Company.objects.get(pk=data["companyId"])
            item.code, item.name = data["code"].strip(), data["name"].strip()
            item.parent = Department.objects.get(pk=data["parentId"]) if data.get("parentId") else None
            item.full_clean()
            item.save()
            audit_services.record_model_change(
                actor=request.user,
                origin="admin",
                action="department.create" if before is None else "department.update",
                instance=item,
                before=before,
            )
    except (Company.DoesNotExist, Department.DoesNotExist):
        return _failure("related_object_not_found", "شرکت یا واحد والد پیدا نشد", 404)
    except Http404:
        raise
    except (ValidationError, IntegrityError):
        return _failure("invalid_department", "اطلاعات واحد معتبر نیست", 400)
    return Response(_department_json(item), status=201 if department_id is None else 200)


@api_view(["GET", "POST"])
def settings_api(request):
    context = _context(request)
    if not _admin(context):
        return _failure("permission_denied", "تنظیمات فقط برای مدیر سامانه مجاز است", 403)
    definitions = settings_schema.DEFS
    if request.method == "GET":
        values = admin_services.load_settings()
        return Response([{
            "key": key, "value": settings_schema.to_storable(values[key]),
            "default": settings_schema.to_storable(item.default),
            "labelFa": item.label_fa, "group": item.group,
        } for key, item in definitions.items()])
    data = request.data
    if (not isinstance(data, dict) or set(data) != {"key", "value", "reason"}
            or not isinstance(data["key"], str) or not isinstance(data["reason"], str)):
        return _failure("invalid_payload", "کلید، مقدار و دلیل الزامی است", 400)
    try:
        admin_services.set_setting(
            user=request.user, key=data["key"], value=data["value"], reason=data["reason"],
        )
    except settings_schema.SettingError as exc:
        return _failure("invalid_setting", str(exc), 400)
    values = admin_services.load_settings()
    item = definitions[data["key"]]
    return Response({
        "key": data["key"], "value": settings_schema.to_storable(values[data["key"]]),
        "default": settings_schema.to_storable(item.default),
        "labelFa": item.label_fa, "group": item.group,
    })


@api_view(["GET"])
def audit_events(request):
    context = _context(request)
    is_admin = _admin(context)
    if is_admin:
        rows = AuditEvent.objects.all()
    elif context.has_role("hr"):
        employee_ids = hr_operation_employees(context).values("pk")

        def audit_ids(queryset):
            return queryset.order_by().annotate(
                audit_id=Cast("pk", output_field=CharField()),
            ).values("audit_id")

        request_ids = audit_ids(LeaveRequest.objects.filter(employee_id__in=employee_ids))
        contract_ids = audit_ids(ContractPeriod.objects.filter(employee_id__in=employee_ids))
        note_ids = audit_ids(BradfordNote.objects.filter(employee_id__in=employee_ids))
        comment_ids = audit_ids(
            LeaveRequestComment.objects.filter(request__employee_id__in=employee_ids),
        )
        attachment_ids = audit_ids(
            LeaveRequestAttachment.objects.filter(request__employee_id__in=employee_ids),
        )
        entitlement_event_ids = audit_ids(
            EntitlementEvent.objects.filter(entitlement__employee_id__in=employee_ids),
        )
        employee_id_strings = EmployeeProfile.objects.filter(
            pk__in=employee_ids,
        ).order_by().annotate(
            audit_id=Cast("pk", output_field=CharField()),
        ).values("audit_id")
        invitation_ids = audit_ids(
            EmployeeInvitation.objects.filter(employee_id__in=employee_ids),
        )
        rows = AuditEvent.objects.filter(
            Q(entity="LeaveRequest", entity_id__in=request_ids)
            | Q(entity="EmployeeProfile", entity_id__in=employee_id_strings)
            | Q(entity="EmployeeInvitation", entity_id__in=invitation_ids)
            | Q(entity="ContractPeriod", entity_id__in=contract_ids)
            | Q(entity="BradfordNote", entity_id__in=note_ids)
            | Q(entity="LeaveRequestComment", entity_id__in=comment_ids)
            | Q(entity="LeaveRequestAttachment", entity_id__in=attachment_ids)
            | Q(entity="EntitlementEvent", entity_id__in=entitlement_event_ids)
        )
    else:
        return _failure("permission_denied", "مشاهده حسابرسی مجاز نیست", 403)
    rows = rows.select_related("actor").order_by("-created_at", "-pk")[:500]
    return Response([{
        "id": item.pk, "actorId": item.actor_id, "actorName": (
            item.actor.get_full_name() or item.actor.get_username() if item.actor_id else None
        ), "action": item.action, "entity": item.entity, "entityId": item.entity_id,
        "reason": item.reason if is_admin else "",
        "result": item.result, "createdAt": item.created_at.isoformat(),
        "before": item.before if is_admin else None,
        "after": item.after if is_admin else None,
        "correlationId": item.correlation_id if is_admin else None,
        "source": item.source if is_admin else "",
    } for item in rows])


@api_view(["GET"])
def jobs(request):
    if not _admin(_context(request)):
        return _failure("permission_denied", "مشاهده Jobها فقط برای مدیر سامانه مجاز است", 403)
    from .job_services import job_status_payload

    return Response(job_status_payload())


@api_view(["GET"])
def client_policy(request):
    context = _context(request)
    if not context.employee:
        return _failure("employee_profile_required", "پرونده کارمندی برای دریافت سیاست لازم است", 403)
    today = organization_localdate()
    from .calendar_services import _patterns_for

    pattern = next(iter(_patterns_for(context.employee, today, today)), None)
    return Response({
        "hourlyLeaveEnabled": False,
        "workdayStart": pattern.start_time.strftime("%H:%M") if pattern else "",
        "workdayEnd": pattern.end_time.strftime("%H:%M") if pattern else "",
        "hourlyAllowOutsideWorkHours": False,
        "hourlyAllowOnHoliday": False,
        "hourlyMaxMinutesPerRequest": 0,
        "workdayMinutes": pattern.daily_work_minutes if pattern else 0,
        "autoCancelEnabled": False,
        "autoCancelDays": None,
        "timezoneLabel": timezone.get_default_timezone_name(),
    })


def _visible_events(context):
    if context.has_role("admin"):
        return LeaveRequestEvent.objects.none()
    request_scope = Q()
    if context.employee_id:
        request_scope |= Q(employee_id=context.employee_id)
        request_scope |= Q(
            manager_snapshot_id=context.employee_id,
            leave_type__confidential=False,
        )
    if context.has_role("hr"):
        request_scope |= Q(pk__in=scoped_hr_leave_requests(context).values("pk"))
    if not request_scope:
        return LeaveRequestEvent.objects.none()
    return LeaveRequestEvent.objects.filter(
        request_id__in=LeaveRequest.objects.filter(request_scope).values("pk"),
    ).exclude(actor_id=context.user.pk)


def _notification_json(event: LeaveRequestEvent, read: bool) -> dict:
    title_by_event = {
        "submit": "درخواست مرخصی جدید",
        "approve": "درخواست مرخصی تأیید شد",
        "reject": "درخواست مرخصی رد شد",
        "cancel": "درخواست مرخصی لغو شد",
        "request_withdraw": "درخواست لغو مرخصی ثبت شد",
        "approve_withdraw": "لغو مرخصی تأیید شد",
        "reject_withdraw": "لغو مرخصی رد شد",
        "reminder": "یادآوری درخواست معوق",
    }
    return {
        "id": event.pk,
        "title": title_by_event.get(event.event, "وضعیت درخواست تغییر کرد"),
        "body": f"وضعیت درخواست {event.request_id} به {event.to_status} تغییر کرد.",
        "read": read, "createdAt": event.created_at.isoformat(),
        "requestId": event.request_id, "kind": event.event, "category": "leave",
    }


@api_view(["GET"])
def notifications(request):
    context = _context(request)
    rows = _visible_events(context).select_related("request__leave_type").order_by("-created_at", "-pk")[:200]
    read_ids = set(NotificationRead.objects.filter(
        user=request.user, event_id__in=[row.pk for row in rows],
    ).values_list("event_id", flat=True))
    return Response([_notification_json(row, row.pk in read_ids) for row in rows])


@api_view(["POST"])
def notification_read(request, notification_id: int):
    context = _context(request)
    event = _visible_events(context).filter(pk=notification_id).first()
    if event is None:
        raise Http404
    NotificationRead.objects.get_or_create(user=request.user, event=event)
    return Response(status=204)


@rate_limited("expensive_operation")
@api_view(["GET"])
def coverage(request):
    context = _context(request)
    if not (context.has_role("manager") or context.has_role("hr") or _admin(context)):
        return _failure("permission_denied", "گزارش پوشش فقط برای مدیر یا HR مجاز است", 403)
    try:
        start = dt.date.fromisoformat(request.query_params["from"])
        end = dt.date.fromisoformat(request.query_params["to"])
        if start > end or (end - start).days > 92:
            raise ValueError
    except (KeyError, ValueError):
        return _failure("invalid_range", "بازه تاریخ معتبر و حداکثر ۹۳ روز باشد", 400)
    if context.has_role("hr"):
        eligible = scoped_leave_requests(context)
        employee_rows = scoped_employees(context)
    elif context.employee_id:
        eligible = LeaveRequest.objects.filter(manager_snapshot_id=context.employee_id)
        employee_rows = EmployeeProfile.objects.filter(manager_id=context.employee_id)
    else:
        return _failure("permission_denied", "دامنه گزارش پوشش مشخص نیست", 403)
    approved = eligible.filter(status="APPROVED", start_date__lte=end, end_date__gte=start)
    hr_request_ids = scoped_hr_leave_requests(context).values("pk")
    approved = approved.filter(
        Q(leave_type__confidential=False) | Q(pk__in=hr_request_ids),
    )
    department_ids = employee_rows.values_list("department_id", flat=True)
    departments = Department.objects.filter(pk__in=department_ids).distinct()
    population = list(
        employee_rows.filter(department_id__in=departments.values("pk")).values(
            "id", "department_id", "is_employee", "start_date", "end_date", "account_status",
        )
    )
    status_history = employment_status_history(
        [employee["id"] for employee in population],
        through_date=end,
    )
    employees_by_department: dict[int, list[dict]] = {}
    for employee in population:
        employees_by_department.setdefault(employee["department_id"], []).append(employee)
    days = [start + dt.timedelta(days=n) for n in range((end - start).days + 1)]
    employed_by_day: dict[dt.date, dict[int, set[int]]] = {}
    for day in days:
        employed_by_department = {}
        for department_id, employees in employees_by_department.items():
            employed_ids = {
                employee["id"]
                for employee in employees
                if is_employed_on(
                    employee,
                    day,
                    status_history=status_history.get(employee["id"], ()),
                )
            }
            employed_by_department[department_id] = employed_ids
        employed_by_day[day] = employed_by_department

    away_by_day: dict[dt.date, dict[int, set[int]]] = {
        day: {department.pk: set() for department in departments}
        for day in days
    }
    leave_spans = approved.values_list(
        "employee__department_id", "employee_id", "start_date", "end_date",
    ).distinct()
    for department_id, employee_id, leave_start, leave_end in leave_spans:
        for day in days:
            if leave_start <= day <= leave_end:
                away_by_day[day].setdefault(department_id, set()).add(employee_id)

    result = []
    for day in days:
        for department in departments:
            employed_ids = employed_by_day[day].get(department.pk, set())
            away = len(away_by_day[day].get(department.pk, set()) & employed_ids)
            result.append({
                "date": day.isoformat(), "departmentName": department.name,
                "awayCount": away, "headcount": len(employed_ids),
            })
    return Response(result)


def _bradford_scope(context) -> list[dict]:
    scope_keys = {
        RoleAssignment.SCOPE_COMPANY: {"companyId": "company_id"},
        RoleAssignment.SCOPE_SITE: {"companyId": "company_id", "siteId": "site_id"},
        RoleAssignment.SCOPE_DEPARTMENT: {
            "companyId": "company_id", "departmentId": "department_id",
        },
        RoleAssignment.SCOPE_EMPLOYEE: {
            "companyId": "company_id", "employeeId": "employee_id",
        },
    }
    result = [
        {
            "type": assignment.scope,
            **{
                key: getattr(assignment, field)
                for key, field in scope_keys[assignment.scope].items()
            },
        }
        for assignment in context.assignments
        if assignment.role == "hr"
    ]
    if context.employee_id is not None:
        result.append({
            "type": "self",
            "companyId": context.employee.company_id,
            "employeeId": context.employee_id,
        })
        if context.has_role("manager"):
            result.append({
                "type": "manager_team",
                "companyId": context.employee.company_id,
                "managerEmployeeId": context.employee_id,
            })
    return result


def _bradford_report(request, context) -> Response:
    scope = _bradford_scope(context)
    today = organization_localdate()
    try:
        start, end = bradford_services.report_period(request.query_params, today)
    except bradford_services.BradfordReportError as exc:
        return Response({
            "status": "invalid_request",
            "reportingPeriod": None,
            "policy": None,
            "authorizedScope": scope,
            "series": [],
            "warnings": [],
            "emptyStateReason": exc.code,
            "error": {"code": exc.code, "message_fa": exc.message_fa},
        }, status=exc.status)
    try:
        limit = int(request.query_params.get("limit", bradford_services.DEFAULT_PAGE_SIZE))
        offset = int(request.query_params.get("offset", 0))
        if not 1 <= limit <= bradford_services.MAX_PAGE_SIZE or offset < 0:
            raise ValueError
    except (TypeError, ValueError):
        return Response({
            "status": "invalid_request",
            "reportingPeriod": {"from": start.isoformat(), "to": end.isoformat()},
            "policy": None,
            "authorizedScope": scope,
            "series": [],
            "warnings": [],
            "emptyStateReason": "invalid_pagination",
            "error": {
                "code": "invalid_pagination",
                "message_fa": "حد صفحه باید بین ۱ تا ۲۰۰ و offset نامنفی باشد",
            },
        }, status=400)

    base = {
        "reportingPeriod": {"from": start.isoformat(), "to": end.isoformat()},
        "policy": {
            "version": bradford_services.POLICY_VERSION,
            "formula": "S^2 * D",
            "description_fa": (
                "S تعداد دوره‌های غیبت و D تعداد روزهای تقویمی غیبت در بازه است؛ "
                "دوره‌های هم‌پوشان یا متصل به هم ادغام می‌شوند."
            ),
            "status": "provisional",
        },
        "authorizedScope": scope,
    }
    try:
        result = bradford_services.calculate_bradford_report(
            employees=scoped_employees(context),
            start=start,
            end=end,
            offset=offset,
            limit=limit,
        )
    except bradford_services.BradfordReportError as exc:
        is_configuration_error = exc.code in {
            "bradford_policy_not_configured",
            "bradford_disabled",
            "invalid_bradford_configuration",
            "unsupported_bradford_configuration",
        }
        return Response({
            **base,
            "policy": None if is_configuration_error else base["policy"],
            "status": "configuration_error" if is_configuration_error else "unavailable",
            "series": [],
            "pagination": {"offset": offset, "limit": limit, "total": None},
            "warnings": [{"code": exc.code, "message_fa": exc.message_fa}],
            "emptyStateReason": exc.code,
            "error": {"code": exc.code, "message_fa": exc.message_fa},
        }, status=exc.status)

    return Response({
        **base,
        "status": result["status"],
        "series": result["series"],
        "pagination": result["pagination"],
        "warnings": result["warnings"],
        "emptyStateReason": result["empty_state_reason"],
    })


@rate_limited("expensive_operation")
@api_view(["GET"])
def reports(request, kind: str):
    context = _context(request)
    return _report_response(request, context, kind)


def _report_response(request, context, kind: str) -> Response:
    if kind == "bradford":
        if not context.has_role("hr"):
            return _failure("permission_denied", "گزارش Bradford فقط برای HR دارای دامنه مجاز است", 403)
        return _bradford_report(request, context)
    if not (context.has_role("hr") or context.has_role("exec")):
        return _failure("permission_denied", "مشاهده گزارش‌ها مجاز نیست", 403)
    if kind not in {"absences", "balances", "trends", "process-health"}:
        return _failure("unknown_report", "نوع گزارش شناخته‌شده نیست", 404)
    if context.has_role("hr"):
        rows = scoped_leave_requests(context)
        people = scoped_employees(context)
    else:
        people = scoped_exec_employees(context)
        rows = LeaveRequest.objects.filter(employee_id__in=people.values("pk"))
    people = employees_employed_on(people, organization_localdate())
    try:
        min_size = settings_schema.resolve({
            setting.key: setting.value for setting in SystemSetting.objects.all()
        })["privacy.min_group_size"]
    except settings_schema.SettingError as exc:
        return _failure("invalid_settings", str(exc), 409)
    report_rows = rows.filter(employee_id__in=people.values("pk"))
    if context.has_role("hr"):
        hr_request_ids = scoped_hr_leave_requests(context).values("pk")
        report_rows = report_rows.filter(
            Q(leave_type__confidential=False) | Q(pk__in=hr_request_ids),
        )
    else:
        report_rows = report_rows.filter(leave_type__confidential=False)
    approved = report_rows.filter(status="APPROVED")
    if kind == "absences":
        categories = approved.values("leave_type__label_fa").annotate(
            total=Count("pk"), people=Count("employee_id", distinct=True),
        )
        series = [
            {"label": item["leave_type__label_fa"], "value": item["total"]}
            for item in categories if item["people"] >= min_size
        ]
    elif kind == "balances":
        entitlements = LeaveEntitlement.objects.filter(employee_id__in=people.values("pk"))
        if not context.has_role("hr"):
            entitlements = entitlements.filter(leave_type__confidential=False)
        count = entitlements.values(
            "employee_id",
        ).distinct().count()
        series = [{"label": "دارای سهمیه", "value": count}] if count >= min_size else []
    elif kind == "trends":
        categories = approved.values("start_date__month").annotate(
            total=Count("pk"), people=Count("employee_id", distinct=True),
        ).order_by("start_date__month")
        series = [
            {"label": str(item["start_date__month"]), "value": item["total"]}
            for item in categories if item["people"] >= min_size
        ]
    elif kind == "process-health":
        categories = report_rows.values("status").annotate(
            total=Count("pk"), people=Count("employee_id", distinct=True),
        ).order_by("status")
        series = [
            {"label": item["status"], "value": item["total"]}
            for item in categories if item["people"] >= min_size
        ]
    else:
        series = []
    total_people = people.count()
    if total_people < min_size:
        return Response({"title": kind, "note": "برای رعایت حداقل گروه، داده این گزارش نمایش داده نمی‌شود.", "series": []})
    if kind == "bradford":
        return Response({
            "title": kind, "series": series,
            "note": "تحلیل Bradford در صفحه اختصاصی منابع انسانی و بر پایه قرارداد و تقویم کارکنان محاسبه می‌شود.",
        })
    return Response({"title": kind, "series": series})


@api_view(["GET", "POST"])
def bradford_notes(request):
    context = _context(request)
    if not (context.has_role("hr") or _admin(context)):
        return _failure("permission_denied", "دسترسی یادداشت Bradford مجاز نیست", 403)
    employee_rows = hr_operation_employees(context)
    if request.method == "GET":
        notes = BradfordNote.objects.select_related("employee__user", "updated_by").filter(
            employee_id__in=employee_rows.values("pk"),
        ).order_by("employee_id")
        return Response([{
            "employeeId": item.employee_id, "body": item.body,
            "updatedAt": item.updated_at.isoformat(),
            "authorName": item.updated_by.get_full_name() or item.updated_by.get_username(),
        } for item in notes])
    data = request.data
    if not isinstance(data, dict) or set(data) != {"employeeId", "body"}:
        return _failure("invalid_payload", "شناسه کارمند و متن یادداشت الزامی است", 400)
    try:
        employee = employee_rows.get(pk=data["employeeId"])
    except (EmployeeProfile.DoesNotExist, ValueError, TypeError):
        raise Http404
    body = data["body"]
    if not isinstance(body, str) or not body.strip() or len(body) > 4000:
        return _failure("invalid_note", "متن یادداشت الزامی و حداکثر ۴۰۰۰ نویسه است", 400)
    with transaction.atomic():
        note = BradfordNote.objects.select_for_update().filter(employee=employee).first()
        if note is None:
            before = None
            note = BradfordNote.objects.create(
                employee=employee, body=body.strip(), updated_by=request.user,
            )
            action = "bradford_note.create"
        else:
            before = audit_services.snapshot(note)
            note.body = body.strip()
            note.updated_by = request.user
            note.save(update_fields=["body", "updated_by", "updated_at"])
            action = "bradford_note.update"
        after = audit_services.snapshot(note)
        after["contentChanged"] = True
        audit_services.record_audit(
            actor=request.user,
            origin="admin" if _admin(context) else "hr",
            action=action,
            entity="BradfordNote",
            entity_id=note.pk,
            before=before,
            after=after,
        )
    return Response({
        "employeeId": note.employee_id, "body": note.body,
        "updatedAt": note.updated_at.isoformat(),
        "authorName": request.user.get_full_name() or request.user.get_username(),
    })


def _contract_json(item: ContractPeriod) -> dict:
    return {
        "id": item.pk, "employeeId": item.employee_id,
        "start": item.start_date.isoformat(), "end": item.end_date.isoformat() if item.end_date else None,
        "workDays": item.work_days, "contractPercent": float(item.contract_percent),
    }


@api_view(["GET", "POST"])
def contracts(request, employee_id: int):
    context = _context(request)
    if not (context.has_role("hr") or _admin(context)):
        return _failure("permission_denied", "مشاهده یا تغییر قرارداد مجاز نیست", 403)
    employee = hr_operation_employees(context).filter(pk=employee_id).first()
    if employee is None:
        raise Http404
    if request.method == "GET":
        rows = ContractPeriod.objects.filter(employee=employee)
        return Response([_contract_json(item) for item in rows])
    data = request.data
    if not isinstance(data, dict) or set(data) != {"employeeId", "start", "end", "workDays", "contractPercent"}:
        return _failure("invalid_payload", "اطلاعات قرارداد معتبر نیست", 400)
    try:
        if int(data["employeeId"]) != employee.pk:
            raise ValueError
        start = dt.date.fromisoformat(data["start"])
        end = dt.date.fromisoformat(data["end"]) if data["end"] else None
        days = data["workDays"]
        percent = Decimal(str(data["contractPercent"]))
        if (not isinstance(days, list) or not days or
                any(type(day) is not int or day < 0 or day > 6 for day in days) or
                len(set(days)) != len(days) or not percent.is_finite() or
                percent <= 0 or percent > 100 or (end and start > end)):
            raise ValueError
        overlaps = ContractPeriod.objects.filter(
            employee=employee, start_date__lte=end or dt.date.max,
        ).filter(Q(end_date__isnull=True) | Q(end_date__gte=start)).exists()
        if overlaps:
            return _failure("contract_period_conflict", "بازه قرارداد با قرارداد ثبت‌شده هم‌پوشانی دارد", 409)
        with transaction.atomic():
            item = ContractPeriod.objects.create(
                employee=employee, start_date=start, end_date=end,
                work_days=days, contract_percent=percent, created_by=request.user,
            )
            audit_services.record_model_change(
                actor=request.user,
                origin="admin" if _admin(context) else "hr",
                action="contract_period.create",
                instance=item,
            )
    except (ValueError, TypeError, InvalidOperation):
        return _failure("invalid_contract", "بازه، روزهای کاری یا درصد قرارداد معتبر نیست", 400)
    return Response(_contract_json(item), status=201)


@rate_limited("file_upload")
@api_view(["GET", "POST"])
def request_attachments(request, request_id: int):
    from .workflow_views import _visible_request

    context = _context(request)
    try:
        row = _visible_request(request_id, context)
    except LeaveRequest.DoesNotExist as exc:
        raise Http404 from exc
    if not can_access_request_attachments(context, row):
        return _failure("permission_denied", "دسترسی به پیوست این درخواست مجاز نیست", 403)
    if request.method == "GET":
        return Response([{
            "id": attachment.pk,
            "fileName": attachment.file_name,
            "sizeLabel": f"{max(1, round(attachment.file_size / 1024))} KB",
            "createdAt": attachment.created_at.isoformat(),
            "scanStatus": attachment.scan_status,
            "downloadUrl": f"/api/v1/leave-requests/{request_id}/attachments/{attachment.pk}/",
        } for attachment in row.attachments.filter(
            deletion_status=LeaveRequestAttachment.DELETION_ACTIVE,
        )])

    upload = request.FILES.get("file")
    if upload is None:
        return _failure("invalid_attachment", "فایل پیوست الزامی است", 400)
    try:
        validated = validate_attachment(upload)
    except InvalidAttachment:
        return _failure("invalid_attachment", "فایل معتبر یا مجاز نیست", 400)

    attachment = LeaveRequestAttachment(
        request=row,
        file_name=validated.filename,
        content_type=validated.content_type,
        file_size=len(validated.content),
        scan_status=LeaveRequestAttachment.SCAN_PENDING,
        uploaded_by=request.user,
    )
    storage = attachment.file.storage
    requested_storage_name = attachment_upload_path(attachment, validated.filename)
    try:
        stored_name = storage.save(requested_storage_name, ContentFile(validated.content))
    except (OSError, SuspiciousFileOperation):
        logger.error("Private attachment storage write failed.")
        return _failure("attachment_storage_unavailable", "ذخیره فایل در دسترس نیست", 503)

    try:
        with transaction.atomic():
            attachment.file = stored_name
            attachment.save(force_insert=True)
            audit_services.record_model_change(
                actor=request.user,
                origin="hr" if context.has_role("hr") else "employee",
                action="leave_request.attachment_added",
                instance=attachment,
            )
    except Exception:
        try:
            storage.delete(stored_name)
        except (OSError, SuspiciousFileOperation):
            logger.error("Failed to clean up an orphaned private attachment.")
        raise

    attachment = scan_stored_attachment(attachment.pk, actor=request.user)
    response_status = (
        201 if attachment.scan_status == LeaveRequestAttachment.SCAN_CLEAN else 202
    )
    return Response({
        "id": attachment.pk, "fileName": attachment.file_name,
        "sizeLabel": f"{max(1, round(attachment.file_size / 1024))} KB",
        "createdAt": attachment.created_at.isoformat(),
        "scanStatus": attachment.scan_status,
        "downloadUrl": f"/api/v1/leave-requests/{request_id}/attachments/{attachment.pk}/",
    }, status=response_status)


@rate_limited("file_access")
@api_view(["GET"])
def request_attachment_download(request, request_id: int, attachment_id: int):
    from .workflow_views import _visible_request

    context = _context(request)
    try:
        row = _visible_request(request_id, context)
    except LeaveRequest.DoesNotExist as exc:
        raise Http404 from exc
    if not can_access_request_attachments(context, row):
        return _failure("permission_denied", "دسترسی به پیوست این درخواست مجاز نیست", 403)
    attachment = row.attachments.filter(
        pk=attachment_id,
        deletion_status=LeaveRequestAttachment.DELETION_ACTIVE,
        scan_status=LeaveRequestAttachment.SCAN_CLEAN,
    ).first()
    if attachment is None:
        raise Http404
    try:
        if not attachment.file.storage.exists(attachment.file.name):
            raise Http404
        file_handle = attachment.file.open("rb")
    except (FileNotFoundError, SuspiciousFileOperation) as exc:
        raise Http404 from exc
    except OSError:
        logger.error("Private attachment storage read failed.")
        return _failure("attachment_storage_unavailable", "خواندن فایل در دسترس نیست", 503)
    response = FileResponse(
        file_handle, as_attachment=True, filename=attachment.file_name,
        content_type="application/octet-stream",
    )
    response["Cache-Control"] = "private, no-store"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "DENY"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response


@api_view(["POST"])
def hr_absence(request):
    from .services import run_idempotent, submit_request
    from .workflow_views import (
        _payload, _request_body, _submit_permission, _workflow_error,
    )

    context = _context(request)
    data = request.data
    allowed = {"employeeId", "leaveTypeKey", "start", "end", "workingDays", "reason", "replacementId"}
    if not context.has_role("hr"):
        return _failure("permission_denied", "ثبت غیبت فقط برای HR مجاز است", 403)
    if not isinstance(data, dict) or set(data) - allowed:
        return _failure("invalid_payload", "اطلاعات غیبت معتبر نیست", 400)
    try:
        employee = hr_operation_employees(context).get(pk=data["employeeId"])
        if not _submit_permission(context, employee):
            return _failure("permission_denied", "ثبت غیبت برای این کارمند مجاز نیست", 403)
        leave_type = LeaveType.objects.get(key=data["leaveTypeKey"])
        fields = _request_body({key: data[key] for key in allowed - {"employeeId"} if key in data})
        replacement = (
            hr_operation_employees(context).filter(pk=fields["replacement_id"]).first()
            if fields["replacement_id"] is not None else None
        )
        if fields["replacement_id"] is not None and replacement is None:
            raise ValueError("جانشین معتبر نیست")

        def create_absence():
            return submit_request(
                user=request.user, employee=employee, leave_type=leave_type,
                start=fields["start"], end=fields["end"], reason=fields["reason"],
                replacement=replacement, working_days=fields["working_days"],
            )

        with transaction.atomic():
            result = run_idempotent(
                user=request.user,
                key=request.headers.get("Idempotency-Key", ""),
                operation="hr.absence",
                payload={"employeeId": employee.pk, "leaveTypeKey": leave_type.key,
                         "start": fields["start"], "end": fields["end"], "reason": fields["reason"],
                         "workingDays": fields["working_days"],
                         "replacementId": fields["replacement_id"]},
                model=LeaveRequest, callback=create_absence,
            )
        return Response(_payload(result, context), status=201)
    except (EmployeeProfile.DoesNotExist, LeaveType.DoesNotExist):
        raise Http404
    except Exception as exc:
        return _workflow_error(exc)
