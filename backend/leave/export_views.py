from __future__ import annotations

import datetime as dt

from django.db.models import Q
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from zsh_core import exports as export_serializers
from . import audit_services
from .authorization import (
    authorization_context,
    manager_team_employees,
    scoped_employees,
    scoped_hr_employees,
    scoped_leave_requests,
)
from .frontend_api_views import (
    _balance_json,
    _bradford_report,
    _employee_json,
    _employee_rows,
    _report_response,
    _visible_entitlement_rows,
)
from .entitlement_services import EntitlementReconciliationError
from .models import LeaveEntitlement
from .throttling import rate_limited
from .workflow_views import _payload, _privacy_setting

MAX_EXPORT_ROWS = 10_000
MAX_XLSX_ROWS = 5_000
MAX_DATE_RANGE_DAYS = 366
CSV_CONTENT_TYPE = "text/csv; charset=utf-8"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_EMPLOYEE_HEADERS = (
    "employeeId", "displayName", "personnelCode", "companyId", "companyName",
    "siteId", "siteName", "departmentName", "managerName", "startDate", "endDate",
    "isActive", "accountStatus",
)
_REQUEST_HEADERS = (
    "id", "employeeId", "employeeName", "status", "start", "end", "workingDays",
    "type", "typeLabel", "reason", "decisionComment", "replacementId", "replacementName",
    "departmentName", "canDecide", "supersedesId", "createdAt", "updatedAt", "confidential",
)
_BALANCE_HEADERS = (
    "id", "employeeId", "employeeName", "leaveTypeKey", "leaveTypeLabel", "year",
    "base", "carryOver", "adjustment", "total", "used", "pending", "remaining", "usable",
)
_REPORT_HEADERS = ("label", "value")
_BRADFORD_HEADERS = (
    "employeeId", "personnelCode", "displayName", "spells", "days", "score",
)

_REPORT_DATASETS = {"absences", "balances", "trends", "process-health"}


def _failure(code: str, message: str, status: int, fields: dict | None = None) -> Response:
    body = {"code": code, "message_fa": message}
    if fields:
        body["fields"] = fields
    return Response(body, status=status)


def _query_values(request, allowed: set[str]):
    query = request.query_params
    if set(query.keys()) - allowed or any(len(query.getlist(key)) != 1 for key in query.keys()):
        return None, _failure(
            "invalid_filter", "فیلترهای خروجی معتبر نیست", 400,
        )
    return {key: query.get(key) for key in query.keys()}, None


def _positive_id(value: str, field: str):
    try:
        if not value.isascii() or not value.isdecimal():
            raise ValueError
        parsed = int(value)
        if not 1 <= parsed <= 9_223_372_036_854_775_807:
            raise ValueError
    except (TypeError, ValueError):
        return None, _failure("invalid_filter", f"{field} معتبر نیست", 400)
    return parsed, None


def _date_range(filters: dict):
    start_text, end_text = filters.get("from"), filters.get("to")
    if (start_text is None) != (end_text is None):
        return None, _failure("invalid_range", "هر دو تاریخ from و to الزامی است", 400)
    if start_text is None:
        return None, None
    try:
        start = dt.date.fromisoformat(start_text)
        end = dt.date.fromisoformat(end_text)
        if (
            start.isoformat() != start_text
            or end.isoformat() != end_text
            or start > end
            or (end - start).days + 1 > MAX_DATE_RANGE_DAYS
        ):
            raise ValueError
    except (TypeError, ValueError):
        return None, _failure(
            "invalid_range", "بازه تاریخ باید معتبر و حداکثر ۳۶۶ روز باشد", 400,
        )
    return (start, end), None


def _assignment_scope(context, role: str) -> list[dict]:
    result = []
    for assignment in context.assignments:
        if assignment.role != role:
            continue
        scope = {"type": assignment.scope}
        for field in ("company_id", "site_id", "department_id", "employee_id"):
            value = getattr(assignment, field)
            if value is not None:
                scope[field.removesuffix("_id") + "Id"] = value
        result.append(scope)
    return result


def _audit_scope(context, dataset: str) -> dict:
    if dataset == "employees" and context.has_role("admin"):
        return {"type": "global_admin"}
    if dataset in _REPORT_DATASETS or dataset == "bradford":
        if context.has_role("hr"):
            scope = {
                "type": "multi_role_union" if context.has_role("manager") else "hr",
                "assignments": _assignment_scope(context, "hr"),
                "employeeId": context.employee_id,
            }
            if context.has_role("manager"):
                scope["managerEmployeeId"] = context.employee_id
            return scope
        return {"type": "executive", "assignments": _assignment_scope(context, "exec")}
    if context.has_role("hr"):
        scope = {
            "type": "hr_authorized_records",
            "employeeId": context.employee_id,
            "roles": sorted(
                role for role in ("employee", "manager", "hr")
                if context.has_role(role)
            ),
            "assignments": _assignment_scope(context, "hr"),
        }
        if context.has_role("manager"):
            scope["managerEmployeeId"] = context.employee_id
        return scope
    if context.has_role("manager") and context.employee_id is not None:
        return {"type": "manager_team", "managerEmployeeId": context.employee_id}
    if context.employee_id is not None:
        return {"type": "employee_self", "employeeId": context.employee_id}
    return {"type": "authorized_read_scope"}


def _record_export(request, dataset: str, file_format: str, filters: dict,
                   scope: dict, row_count: int) -> None:
    audit_services.record_audit(
        actor=request.user,
        action="data.exported",
        entity="Export",
        entity_id=dataset,
        origin="export",
        after={
            "dataset": dataset,
            "format": file_format,
            "filters": filters,
            "scope": scope,
            "rowCount": row_count,
        },
        correlation_id=getattr(request, "correlation_id", ""),
    )


def _download_response(request, dataset: str, file_format: str, headers: tuple[str, ...],
                       rows, row_count: int, filters: dict, scope: dict):
    if row_count > MAX_EXPORT_ROWS:
        return _failure("export_too_large", "تعداد ردیف‌های خروجی از حد مجاز بیشتر است", 413)
    if file_format == "xlsx" and row_count > MAX_XLSX_ROWS:
        return _failure("export_too_large", "خروجی XLSX حداکثر ۵۰۰۰ ردیف دارد", 413)

    filename = f"zsh-leave_{dataset}_{timezone.localdate():%Y%m%d}.{file_format}"
    if file_format == "csv":
        _record_export(request, dataset, file_format, filters, scope, row_count)
        response = StreamingHttpResponse(
            export_serializers.iter_csv_bytes(
                headers,
                ([item.get(header) for header in headers] for item in rows),
            ),
            content_type=CSV_CONTENT_TYPE,
        )
    else:
        content = export_serializers.to_xlsx_bytes(
            headers,
            ([item.get(header) for header in headers] for item in rows),
        )
        _record_export(request, dataset, file_format, filters, scope, row_count)
        response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _bounded_ids(rows, file_format: str):
    maximum = min(MAX_EXPORT_ROWS, MAX_XLSX_ROWS) if file_format == "xlsx" else MAX_EXPORT_ROWS
    identifiers = list(rows.values_list("pk", flat=True)[:maximum + 1])
    if len(identifiers) > maximum:
        return None, _failure(
            "export_too_large",
            f"تعداد ردیف‌های خروجی از حد مجاز {maximum} بیشتر است",
            413,
        )
    return identifiers, None


def _employee_export(request, context, file_format: str):
    filters, error = _query_values(request, {"employee", "q", "scope"})
    if error:
        return error

    rows = _employee_rows(context).select_related(
        "user", "company", "site", "department", "manager__user",
    )
    scope_filter = filters.get("scope")
    if scope_filter == "me":
        rows = rows.filter(pk=context.employee_id)
    elif scope_filter == "team":
        if context.employee_id is None or not context.has_role("manager"):
            return _failure("permission_denied", "خروجی تیم فقط برای مدیر مجاز است", 403)
        rows = rows.filter(pk__in=manager_team_employees(context).values("pk"))
    elif scope_filter not in (None, ""):
        return _failure("invalid_scope", "دامنه باید me یا team باشد", 400)
    elif (
        context.has_role("manager")
        and not context.has_role("hr")
        and not context.has_role("admin")
    ):
        rows = rows.filter(pk__in=manager_team_employees(context).values("pk"))
    if filters.get("employee"):
        employee_id, error = _positive_id(filters["employee"], "employee")
        if error:
            return error
        if not rows.filter(pk=employee_id).exists():
            raise Http404
        rows = rows.filter(pk=employee_id)
    query = filters.get("q", "").strip()
    if len(query) > 100:
        return _failure("invalid_filter", "فیلتر جست‌وجو حداکثر ۱۰۰ نویسه باشد", 400)
    if query:
        rows = rows.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(personnel_code__icontains=query)
            | Q(user__username__icontains=query)
        )
    rows = rows.order_by("user__last_name", "user__first_name", "pk")
    identifiers, error = _bounded_ids(rows, file_format)
    if error:
        return error
    rows = rows.filter(pk__in=identifiers).order_by(
        "user__last_name", "user__first_name", "pk",
    )
    audit_filters = {
        key: value for key, value in filters.items() if key != "q"
    }
    if query:
        audit_filters["queryPresent"] = True
    return _download_response(
        request, "employees", file_format, _EMPLOYEE_HEADERS,
        (_employee_json(employee) for employee in rows.iterator(chunk_size=500)),
        len(identifiers), audit_filters, _audit_scope(context, "employees"),
    )


def _request_export(request, context, file_format: str):
    filters, error = _query_values(request, {"scope", "status", "q", "from", "to"})
    if error:
        return error
    date_range, error = _date_range(filters)
    if error:
        return error
    rows = scoped_leave_requests(context).select_related(
        "employee__user", "employee__company", "employee__site", "employee__department",
        "leave_type", "replacement__user",
    )
    scope_filter = filters.get("scope")
    if scope_filter == "me":
        rows = rows.filter(employee_id=context.employee_id)
    elif scope_filter == "team":
        if context.employee_id is None:
            return _failure("permission_denied", "پرونده کارمندی لازم است", 403)
        rows = rows.filter(manager_snapshot_id=context.employee_id)
    elif scope_filter not in (None, ""):
        return _failure("invalid_scope", "دامنه باید me یا team باشد", 400)
    status_filter = filters.get("status", "")
    if len(status_filter) > 24:
        return _failure("invalid_filter", "وضعیت درخواست معتبر نیست", 400)
    if status_filter:
        rows = rows.filter(status=status_filter)
    query = filters.get("q", "").strip()
    if len(query) > 100:
        return _failure("invalid_filter", "فیلتر جست‌وجو حداکثر ۱۰۰ نویسه باشد", 400)
    if query:
        rows = rows.filter(
            Q(employee__user__username__icontains=query)
            | Q(employee__user__first_name__icontains=query)
            | Q(employee__user__last_name__icontains=query)
        )
    if date_range:
        start, end = date_range
        rows = rows.filter(start_date__lte=end, end_date__gte=start)
    rows = rows.order_by("-created_at", "-pk")
    identifiers, error = _bounded_ids(rows, file_format)
    if error:
        return error
    rows = rows.filter(pk__in=identifiers).order_by("-created_at", "-pk")
    privacy_setting = _privacy_setting()
    payloads = (
        _payload(row, context, privacy_setting=privacy_setting)
        for row in rows.iterator(chunk_size=500)
    )
    audit_filters = {
        key: value for key, value in filters.items() if key != "q"
    }
    if query:
        audit_filters["queryPresent"] = True
    return _download_response(
        request, "leave-requests", file_format, _REQUEST_HEADERS,
        payloads, len(identifiers), audit_filters, _audit_scope(context, "leave-requests"),
    )


def _balance_export(request, context, file_format: str):
    filters, error = _query_values(request, {"year", "employee"})
    if error:
        return error
    try:
        year = int(filters.get("year", ""))
        if not 1 <= year <= 9999:
            raise ValueError
    except (TypeError, ValueError):
        return _failure("invalid_year", "سال معتبر نیست", 400)
    if filters.get("employee"):
        employee_id, error = _positive_id(filters["employee"], "employee")
        if error:
            return error
    else:
        employee_id = context.employee_id
    if employee_id is None:
        return _failure("employee_profile_required", "پرونده کارمندی لازم است", 403)
    employee = scoped_employees(context).filter(pk=employee_id).first()
    if employee is None:
        raise Http404
    rows = LeaveEntitlement.objects.select_related(
        "employee__user", "employee__company", "employee__site",
        "employee__department", "leave_type",
    ).filter(
        employee=employee, year=year, leave_type__counts_against_balance=True,
    ).order_by("leave_type__sort_order", "pk")
    hr_employee_ids = scoped_hr_employees(context).values("pk")
    if context.has_role("hr"):
        rows = rows.filter(
            Q(leave_type__confidential=False) | Q(employee_id__in=hr_employee_ids),
        )
    else:
        rows = rows.filter(leave_type__confidential=False)
    identifiers, error = _bounded_ids(rows, file_format)
    if error:
        return error
    rows = rows.filter(pk__in=identifiers).order_by("leave_type__sort_order", "pk")
    try:
        visible_rows = _visible_entitlement_rows(
            context, rows.iterator(chunk_size=500),
        )
        payloads = [_balance_json(item) for item in visible_rows]
        return _download_response(
            request, "employee-balances", file_format, _BALANCE_HEADERS,
            payloads, len(visible_rows),
            {"year": year, "employee": employee_id},
            _audit_scope(context, "employee-balances"),
        )
    except EntitlementReconciliationError:
        return _failure(
            "entitlement_reconciliation", "دفترکل سهمیه نیازمند بررسی است", 409,
        )


def _report_export(request, context, dataset: str, file_format: str):
    if dataset == "bradford":
        if not context.has_role("hr"):
            return _failure("permission_denied", "گزارش Bradford فقط برای HR دارای دامنه مجاز است", 403)
        filters, error = _query_values(request, {"from", "to", "limit", "offset"})
        if error:
            return error
        response = _bradford_report(request, context)
        if response.status_code >= 400:
            return response
        series = response.data["series"]
        rows = (dict(item) for item in series)
        return _download_response(
            request, dataset, file_format, _BRADFORD_HEADERS,
            rows, len(series), filters, _audit_scope(context, dataset),
        )

    if dataset not in _REPORT_DATASETS:
        raise Http404
    filters, error = _query_values(request, set())
    if error:
        return error
    response = _report_response(request, context, dataset)
    if response.status_code >= 400:
        return response
    series = response.data.get("series", [])
    return _download_response(
        request, dataset, file_format, _REPORT_HEADERS,
        (dict(item) for item in series), len(series), filters,
        _audit_scope(context, dataset),
    )


@rate_limited("expensive_operation")
@api_view(["GET"])
def exports(request, dataset: str, file_format: str):
    if file_format not in {"csv", "xlsx"}:
        raise Http404
    context = authorization_context(request.user)
    if dataset == "employees":
        return _employee_export(request, context, file_format)
    if dataset == "leave-requests":
        return _request_export(request, context, file_format)
    if dataset == "employee-balances":
        return _balance_export(request, context, file_format)
    if dataset in _REPORT_DATASETS | {"bradford"}:
        return _report_export(request, context, dataset, file_format)
    raise Http404
