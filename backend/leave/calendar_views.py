from __future__ import annotations

import datetime as dt

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import Http404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from zsh_core import holidays as holiday_rules
from zsh_core import jalali
from . import admin_services
from .authorization import (
    MANAGE_SYSTEM_CONFIGURATION,
    READ_COMPANY_CALENDAR,
    READ_PERSONAL_CALENDAR,
    READ_TEAM_CALENDAR,
    READ_WORKDAY_ESTIMATE,
    READ_WORK_PATTERNS,
    READ_LEAVE_POLICY,
    authorization_context,
    has_permission,
    scoped_employees,
    scoped_hr_leave_requests,
    scoped_leave_requests,
)
from .calendar_services import (
    CalendarConfigurationError,
    _patterns_for,
    calculate_working_days,
    create_calendar_holiday,
    create_policy_version,
    create_work_pattern,
    deactivate_calendar_holiday,
    resolve_policy_version,
    update_calendar_holiday,
)
from .employment_services import is_employed_on, organization_localdate
from .throttling import rate_limited
from .models import (
    CalendarHoliday,
    CalendarHolidayHistory,
    Company,
    Department,
    EmployeeProfile,
    LeavePolicyVersion,
    LeaveRequest,
    OrganizationSite,
    PublicHoliday,
    WorkPattern,
)


def _failure(code: str, message: str, status: int, fields: dict | None = None) -> Response:
    body = {"code": code, "message_fa": message}
    if fields:
        body["fields"] = fields
    return Response(body, status=status)


def _validation_fields(error: DjangoValidationError) -> dict:
    if hasattr(error, "message_dict"):
        return {key: "; ".join(map(str, values)) for key, values in error.message_dict.items()}
    return {"non_field_errors": "; ".join(map(str, error.messages))}


def _parse_date(value: str | None, key: str) -> dt.date:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} الزامی است")
    parsed = dt.date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError(f"{key} باید به قالب YYYY-MM-DD باشد")
    return parsed


def _date_range(params, start_key: str = "from", end_key: str = "to") -> tuple[dt.date, dt.date]:
    start = _parse_date(params.get(start_key), start_key)
    end = _parse_date(params.get(end_key), end_key)
    if start > end:
        raise ValueError("پایان بازه باید پس از شروع باشد")
    if (end - start).days > 92:
        raise ValueError("بازه تقویم حداکثر ۹۳ روز است")
    return start, end


def _employee_for(user) -> EmployeeProfile | None:
    return EmployeeProfile.objects.select_related(
        "company", "site", "department", "user",
    ).filter(user=user, is_employee=True).first()


def _is_admin(user) -> bool:
    return has_permission(authorization_context(user), MANAGE_SYSTEM_CONFIGURATION)


def _calendar_holiday_json(holiday: CalendarHoliday) -> dict:
    return {
        "id": holiday.pk,
        "title": holiday.title,
        "start": holiday.start_date.isoformat(),
        "end": holiday.end_date.isoformat(),
        "scope": holiday.scope,
        "kind": holiday.holiday_kind,
    }


def _legacy_holiday_json(holiday: PublicHoliday) -> dict:
    return {
        "id": holiday.pk,
        "scope": holiday.scope,
        "jalaliYear": holiday.jalali_year,
        "jalaliMonth": holiday.jalali_month,
        "jalaliDay": holiday.jalali_day,
        "label": holiday.label,
        "isActive": holiday.is_active,
    }


def _pattern_json(pattern: WorkPattern) -> dict:
    return {
        "id": pattern.pk,
        "name": pattern.name,
        "companyId": pattern.company_id,
        "siteId": pattern.site_id,
        "departmentId": pattern.department_id,
        "employeeId": pattern.employee_id,
        "workingWeekdays": pattern.working_weekdays,
        "startTime": pattern.start_time.strftime("%H:%M"),
        "endTime": pattern.end_time.strftime("%H:%M"),
        "breakMinutes": pattern.break_minutes,
        "dailyWorkMinutes": pattern.daily_work_minutes,
        "weeklyWorkMinutes": pattern.weekly_work_minutes,
        "effectiveFrom": pattern.effective_from.isoformat(),
        "effectiveTo": pattern.effective_to.isoformat() if pattern.effective_to else None,
    }


def _policy_json(version: LeavePolicyVersion) -> dict:
    return {
        "id": version.pk,
        "scope": version.scope,
        "version": version.version,
        "effectiveFrom": version.effective_from.isoformat(),
        "values": version.values,
    }


@api_view(["GET"])
def calendar_events(request):
    try:
        start, end = _date_range(request.query_params)
    except ValueError as exc:
        return _failure("invalid_range", str(exc), 400)

    scope = request.query_params.get("scope", "me")
    context = authorization_context(request.user)
    employee = context.employee
    if scope == "org":
        if not has_permission(context, READ_COMPANY_CALENDAR):
            return _failure("permission_denied", "دسترسی تقویم سازمانی مجاز نیست", 403)
        requests = scoped_leave_requests(context).filter(
            status="APPROVED",
        )
    elif scope == "team":
        if not has_permission(context, READ_TEAM_CALENDAR):
            return _failure("employee_profile_required", "پرونده کارمندی برای کاربر ثبت نشده است", 403)
        requests = scoped_leave_requests(context).filter(
            manager_snapshot_id=context.employee_id, status="APPROVED",
        )
    elif scope == "me":
        if not has_permission(context, READ_PERSONAL_CALENDAR):
            return _failure("employee_profile_required", "پرونده کارمندی برای کاربر ثبت نشده است", 403)
        requests = LeaveRequest.objects.filter(
            employee_id=context.employee_id,
            status__in=["PENDING", "ESCALATED", "APPROVED", "WITHDRAWAL_PENDING"],
        )
    else:
        return _failure("invalid_scope", "دامنه باید me، team یا org باشد", 400)

    rows = requests.filter(
        start_date__lte=end, end_date__gte=start,
    ).select_related("employee__user", "leave_type").order_by("start_date", "pk")
    events = []
    for row in rows:
        own = employee is not None and row.employee_id == employee.pk
        confidential = row.leave_type.confidential
        events.append({
            "id": row.pk,
            "employeeId": row.employee_id,
            "employeeName": row.employee.user.get_full_name() or row.employee.user.get_username(),
            "start": row.start_date.isoformat(),
            "end": row.end_date.isoformat(),
            "status": row.status,
            "label": row.leave_type.label_fa if own and not confidential else "غیبت",
            "color": row.leave_type.color if own and not confidential else "#6b7280",
            "scope": scope,
            "durationKind": "daily",
        })
    return Response(events)


@api_view(["GET"])
def calendar_holidays(request):
    try:
        start, end = _date_range(request.query_params)
    except ValueError as exc:
        return _failure("invalid_range", str(exc), 400)

    employee = _employee_for(request.user)
    if not employee and not _is_admin(request.user):
        return _failure("employee_profile_required", "پرونده کارمندی برای کاربر ثبت نشده است", 403)
    scope_query = Q(scope__in=["national", "organization"])
    if employee:
        scope_query |= Q(scope="company", company_id=employee.company_id)
        if employee.site_id:
            scope_query |= Q(scope="site", site_id=employee.site_id)
    entries = CalendarHoliday.objects.filter(
        scope_query, is_active=True, start_date__lte=end, end_date__gte=start,
    ).order_by("start_date", "pk")
    result = [_calendar_holiday_json(item) for item in entries]

    legacy_scopes = [employee.company.code] if employee else []
    if employee and employee.site_id:
        legacy_scopes.append(employee.site.code)
    legacy = [
        holiday_rules.HolidayEntry(item.scope, item.jalali_month, item.jalali_day,
                                   item.label, item.jalali_year)
        for item in PublicHoliday.objects.filter(scope__in=["*", *legacy_scopes], is_active=True)
    ]
    try:
        resolved_dates = holiday_rules.holidays_in_range(legacy, legacy_scopes, start, end)
    except jalali.JalaliError as exc:
        return _failure("unsupported_calendar_range", str(exc), 400)
    for holiday_date in sorted(resolved_dates):
        labels = [
            item.label for item in legacy
            if item.scope in {"*", *legacy_scopes}
            and item.resolve(jalali.from_gregorian(holiday_date)[0]) == holiday_date
        ]
        result.append({
            "id": f"legacy-{holiday_date.isoformat()}",
            "title": "، ".join(labels),
            "start": holiday_date.isoformat(),
            "end": holiday_date.isoformat(),
            "scope": "legacy",
            "kind": "official",
        })
    return Response(result)


@rate_limited("expensive_operation")
@api_view(["GET"])
def estimate_working_days(request):
    start_value = request.query_params.get("start")
    end_value = request.query_params.get("end")
    try:
        start = _parse_date(start_value, "start")
        end = _parse_date(end_value, "end")
        if start > end:
            raise ValueError("پایان بازه باید پس از شروع باشد")
        if (end - start).days > 92:
            raise ValueError("بازه محاسبه حداکثر ۹۳ روز است")
    except ValueError as exc:
        return _failure("invalid_range", str(exc), 400)

    requested_id = request.query_params.get("employee")
    context = authorization_context(request.user)
    if requested_id:
        try:
            requested_pk = int(requested_id)
        except ValueError:
            raise Http404
        employee = scoped_employees(context).select_related(
            "company", "site", "department", "user",
        ).filter(pk=requested_pk).first()
        if employee is None:
            raise Http404
    else:
        employee = context.employee
    if not employee:
        return _failure("employee_profile_required", "پرونده کارمندی برای کاربر ثبت نشده است", 403)
    if not has_permission(context, READ_WORKDAY_ESTIMATE, employee_id=employee.pk):
        raise Http404
    if not is_employed_on(employee, start) or not is_employed_on(employee, end):
        return _failure("employee_not_employed", "کارمند در کل بازه محاسبه اشتغال ندارد", 422)
    try:
        calculation = calculate_working_days(employee=employee, start=start, end=end)
    except CalendarConfigurationError as exc:
        return _failure("calendar_not_configured", str(exc), 409)
    except DjangoValidationError as exc:
        return _failure("invalid_range", str(exc), 400, _validation_fields(exc))
    return Response({
        "days": str(calculation.days),
        "estimated": False,
        "policyVersionId": calculation.policy_version_id,
        "workPatternIds": calculation.work_pattern_ids,
    })


@api_view(["GET", "POST"])
def work_patterns(request):
    if request.method == "GET":
        context = authorization_context(request.user)
        employee = context.employee
        if not has_permission(context, READ_WORK_PATTERNS):
            return _failure("employee_profile_required", "پرونده کارمندی برای کاربر ثبت نشده است", 403)
        try:
            start, end = _date_range(request.query_params, "from", "to")
        except ValueError as exc:
            return _failure("invalid_range", str(exc), 400)
        patterns = _patterns_for(employee, start, end)
        return Response([_pattern_json(pattern) for pattern in patterns])

    if not _is_admin(request.user):
        return _failure("permission_denied", "ثبت الگوی کاری فقط برای مدیرکل سامانه مجاز است", 403)
    data = request.data
    if not isinstance(data, dict):
        return _failure("invalid_payload", "بدنه درخواست باید یک شیء JSON باشد", 400)
    try:
        company = Company.objects.get(pk=data.get("companyId"))
        site = OrganizationSite.objects.get(pk=data["siteId"]) if data.get("siteId") else None
        department = Department.objects.get(pk=data["departmentId"]) if data.get("departmentId") else None
        employee = EmployeeProfile.objects.get(pk=data["employeeId"]) if data.get("employeeId") else None
        pattern = create_work_pattern(
            actor=request.user,
            name=str(data.get("name", "")),
            company=company,
            site=site,
            department=department,
            employee=employee,
            weekdays=data.get("workingWeekdays"),
            start_time=dt.time.fromisoformat(data.get("startTime", "")),
            end_time=dt.time.fromisoformat(data.get("endTime", "")),
            break_minutes=int(data.get("breakMinutes", 0)),
            effective_from=_parse_date(data.get("effectiveFrom"), "effectiveFrom"),
            effective_to=_parse_date(data["effectiveTo"], "effectiveTo") if data.get("effectiveTo") else None,
        )
    except (Company.DoesNotExist, OrganizationSite.DoesNotExist,
            Department.DoesNotExist, EmployeeProfile.DoesNotExist):
        return _failure("related_object_not_found", "محدوده سازمانی پیدا نشد", 404)
    except (ValueError, TypeError) as exc:
        return _failure("invalid_payload", str(exc), 400)
    except DjangoValidationError as exc:
        return _failure("invalid_payload", "اطلاعات الگوی کاری معتبر نیست", 400, _validation_fields(exc))
    return Response(_pattern_json(pattern), status=201)


@api_view(["GET", "POST"])
def leave_policies(request):
    if request.method == "GET":
        context = authorization_context(request.user)
        employee = context.employee
        if not has_permission(context, READ_LEAVE_POLICY):
            return _failure("employee_profile_required", "پرونده کارمندی برای کاربر ثبت نشده است", 403)
        try:
            on_date = (
                _parse_date(request.query_params.get("date"), "date")
                if request.query_params.get("date") else organization_localdate()
            )
            version = resolve_policy_version(employee, on_date)
        except ValueError as exc:
            return _failure("invalid_date", str(exc), 400)
        except CalendarConfigurationError as exc:
            return _failure("policy_not_configured", str(exc), 409)
        return Response(_policy_json(version))

    if not _is_admin(request.user):
        return _failure("permission_denied", "ثبت سیاست فقط برای مدیرکل سامانه مجاز است", 403)
    data = request.data
    if not isinstance(data, dict):
        return _failure("invalid_payload", "بدنه درخواست باید یک شیء JSON باشد", 400)
    try:
        company = Company.objects.get(pk=data["companyId"]) if data.get("companyId") else None
        site = OrganizationSite.objects.get(pk=data["siteId"]) if data.get("siteId") else None
        department = Department.objects.get(pk=data["departmentId"]) if data.get("departmentId") else None
        employee = EmployeeProfile.objects.get(pk=data["employeeId"]) if data.get("employeeId") else None
        version = create_policy_version(
            actor=request.user,
            scope=data.get("scope", ""),
            company=company,
            site=site,
            department=department,
            employee=employee,
            effective_from=_parse_date(data.get("effectiveFrom"), "effectiveFrom"),
            values=data.get("values"),
            reason=str(data.get("reason", "")),
        )
    except (Company.DoesNotExist, OrganizationSite.DoesNotExist,
            Department.DoesNotExist, EmployeeProfile.DoesNotExist):
        return _failure("related_object_not_found", "محدوده سازمانی پیدا نشد", 404)
    except (ValueError, TypeError) as exc:
        return _failure("invalid_payload", str(exc), 400)
    except DjangoValidationError as exc:
        return _failure("invalid_payload", "اطلاعات سیاست معتبر نیست", 400, _validation_fields(exc))
    return Response(_policy_json(version), status=201)


@api_view(["GET", "POST", "PATCH", "DELETE"])
def calendar_holiday_management(request, holiday_id: int | None = None):
    if request.method == "GET":
        employee = _employee_for(request.user)
        if _is_admin(request.user):
            holidays = PublicHoliday.objects.filter(is_active=True)
        elif employee:
            scopes = ["*", employee.company.code]
            if employee.site_id:
                scopes.append(employee.site.code)
            holidays = PublicHoliday.objects.filter(scope__in=scopes, is_active=True)
        else:
            return _failure("employee_profile_required", "پرونده کارمندی برای کاربر ثبت نشده است", 403)
        return Response([_legacy_holiday_json(item) for item in holidays.order_by(
            "jalali_month", "jalali_day", "jalali_year",
        )])

    if not _is_admin(request.user):
        return _failure("permission_denied", "مدیریت تعطیلات فقط برای مدیرکل سامانه مجاز است", 403)
    if request.method == "POST":
        data = request.data
        if not isinstance(data, dict):
            return _failure("invalid_holiday", "بدنه درخواست باید یک شیء JSON باشد", 400)
        try:
            month = int(data.get("jalaliMonth"))
            day = int(data.get("jalaliDay"))
            year_value = data.get("jalaliYear")
            recurring = year_value is None
            year = int(year_value) if year_value is not None else (1403 if month == 12 and day == 30 else 1404)
            holiday = admin_services.add_holiday(
                user=request.user,
                scope=data.get("scope", ""),
                label=data.get("label", ""),
                jalali_text=f"{year}/{month}/{day}",
                recurring=recurring,
            )
        except (ValueError, DjangoValidationError) as exc:
            return _failure("invalid_holiday", str(exc), 400)
        return Response(_legacy_holiday_json(holiday), status=201)

    if holiday_id is None:
        return _failure("missing_holiday_id", "شناسه تعطیلی الزامی است", 400)
    if request.method == "DELETE":
        if not isinstance(request.data, dict):
            return _failure("invalid_holiday", "بدنه درخواست باید یک شیء JSON باشد", 400)
        reason = request.data.get("reason", "")
        try:
            admin_services.delete_holiday(user=request.user, holiday_id=holiday_id, reason=reason)
        except PublicHoliday.DoesNotExist:
            raise Http404
        except (ValueError, DjangoValidationError) as exc:
            return _failure("invalid_holiday", str(exc), 400)
        return Response(status=204)
    data = request.data
    if not isinstance(data, dict):
        return _failure("invalid_holiday", "بدنه درخواست باید یک شیء JSON باشد", 400)
    try:
        month = int(data.get("jalaliMonth"))
        day = int(data.get("jalaliDay"))
        year_value = data.get("jalaliYear")
        recurring = year_value is None
        year = int(year_value) if year_value is not None else (1403 if month == 12 and day == 30 else 1404)
        holiday = admin_services.update_holiday(
            user=request.user,
            holiday_id=holiday_id,
            scope=data.get("scope", ""),
            label=data.get("label", ""),
            jalali_text=f"{year}/{month}/{day}",
            recurring=recurring,
            reason=data.get("reason", ""),
        )
    except PublicHoliday.DoesNotExist:
        raise Http404
    except (ValueError, DjangoValidationError) as exc:
        return _failure("invalid_holiday", str(exc), 400)
    return Response(_legacy_holiday_json(holiday))


def _calendar_holiday_fields(data, *, partial: bool = False) -> dict:
    if not isinstance(data, dict):
        raise DjangoValidationError({"fields": "بدنه درخواست باید یک شیء JSON باشد"})
    allowed = {
        "title", "start", "end", "scope", "companyId", "siteId",
        "kind", "description", "source",
    }
    if set(data.keys()) - allowed:
        raise DjangoValidationError({"fields": "فیلد ناشناخته در درخواست وجود دارد"})
    values = {}
    source = {
        "title": "title",
        "scope": "scope",
        "kind": "holiday_kind",
        "description": "description",
        "source": "source",
    }
    for api_key, model_key in source.items():
        if api_key in data:
            values[model_key] = data.get(api_key)
        elif not partial and api_key in {"title", "scope"}:
            raise DjangoValidationError({api_key: "این فیلد الزامی است"})
    for api_key, model_key in (("start", "start_date"), ("end", "end_date")):
        if api_key in data:
            values[model_key] = _parse_date(data.get(api_key), api_key)
        elif not partial:
            raise DjangoValidationError({api_key: "این فیلد الزامی است"})

    company = None
    site = None
    if "companyId" in data and data.get("companyId") is not None:
        company = Company.objects.get(pk=data.get("companyId"))
    if "siteId" in data and data.get("siteId") is not None:
        site = OrganizationSite.objects.get(pk=data.get("siteId"))
        if company is None:
            company = site.company
    if "scope" in data or "companyId" in data or "siteId" in data:
        values["company"] = company
        values["site"] = site
    return values


@api_view(["GET", "POST"])
def calendar_holiday_admin(request):
    if request.method == "GET":
        if not _is_admin(request.user):
            return _failure("permission_denied", "فهرست کامل تعطیلات فقط برای مدیرکل سامانه مجاز است", 403)
        return Response([_calendar_holiday_json(item) for item in CalendarHoliday.objects.order_by(
            "start_date", "pk",
        )])
    if not _is_admin(request.user):
        return _failure("permission_denied", "مدیریت تعطیلات فقط برای مدیرکل سامانه مجاز است", 403)
    if not isinstance(request.data, dict):
        return _failure("invalid_holiday", "بدنه درخواست باید یک شیء JSON باشد", 400)
    try:
        fields = _calendar_holiday_fields(request.data)
        item = create_calendar_holiday(actor=request.user, **fields)
    except (Company.DoesNotExist, OrganizationSite.DoesNotExist):
        return _failure("related_object_not_found", "محدوده سازمانی پیدا نشد", 404)
    except ValueError as exc:
        return _failure("invalid_holiday", str(exc), 400)
    except DjangoValidationError as exc:
        return _failure("invalid_holiday", "اطلاعات تعطیلی معتبر نیست", 400, _validation_fields(exc))
    return Response(_calendar_holiday_json(item), status=201)


@api_view(["PATCH", "DELETE"])
def calendar_holiday_admin_detail(request, holiday_id: int):
    if not _is_admin(request.user):
        return _failure("permission_denied", "مدیریت تعطیلات فقط برای مدیرکل سامانه مجاز است", 403)
    if not isinstance(request.data, dict):
        return _failure("invalid_holiday", "بدنه درخواست باید یک شیء JSON باشد", 400)
    try:
        if request.method == "PATCH":
            changes = _calendar_holiday_fields(request.data, partial=True)
            item = update_calendar_holiday(actor=request.user, holiday_id=holiday_id, changes=changes)
            return Response(_calendar_holiday_json(item))
        deactivate_calendar_holiday(
            actor=request.user,
            holiday_id=holiday_id,
            reason=request.data.get("reason", ""),
        )
    except CalendarHoliday.DoesNotExist:
        raise Http404
    except ValueError as exc:
        return _failure("invalid_holiday", str(exc), 400)
    except DjangoValidationError as exc:
        return _failure("invalid_holiday", "اطلاعات تعطیلی معتبر نیست", 400, _validation_fields(exc))
    return Response(status=204)


@api_view(["GET"])
def work_patterns_history(request):
    if not _is_admin(request.user):
        return _failure("permission_denied", "فهرست همه الگوها فقط برای مدیرکل سامانه مجاز است", 403)
    return Response([_pattern_json(pattern) for pattern in WorkPattern.objects.order_by(
        "company_id", "effective_from", "pk",
    )])


@api_view(["GET"])
def policy_history(request):
    if not _is_admin(request.user):
        return _failure("permission_denied", "فهرست همه سیاست‌ها فقط برای مدیرکل سامانه مجاز است", 403)
    versions = LeavePolicyVersion.objects.order_by("scope", "effective_from", "version")
    return Response([_policy_json(version) for version in versions])


@api_view(["GET"])
def holiday_history(request, holiday_id: int):
    if not _is_admin(request.user):
        return _failure("permission_denied", "تاریخچه تعطیلات فقط برای مدیرکل سامانه مجاز است", 403)
    history = CalendarHolidayHistory.objects.filter(
        holiday_id=holiday_id,
    ).order_by("created_at").values("action", "before", "after", "created_at")
    return Response(list(history))
