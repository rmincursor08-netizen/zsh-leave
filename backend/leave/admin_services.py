"""سرویس‌های داشبورد مدیریت: تنظیمات و تعطیلات. هر تغییر اعتبارسنجی، تراکنشی و حسابرسی می‌شود."""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from zsh_core import holidays as H
from zsh_core import settings_schema as S
from zsh_core.policy import LeavePolicy
from . import audit_services
from .authorization import MANAGE_GLOBAL_LEAVE_TYPES, authorization_context, has_permission
from .models import (
    LeaveEntitlement, LeaveRequest, LeaveType, PublicHoliday, PublicHolidayHistory,
    SystemSetting,
)

LEAVE_TYPE_FIELDS = {
    "key": "key",
    "labelFa": "label_fa",
    "color": "color",
    "countsAgainstBalance": "counts_against_balance",
    "requiresApproval": "requires_approval",
    "requiresNote": "requires_note",
    "requiresReplacement": "requires_replacement",
    "employeeRequestable": "employee_requestable",
    "confidential": "confidential",
    "enabled": "enabled",
    "sortOrder": "sort_order",
}

HISTORICAL_LEAVE_TYPE_FIELDS = {
    "key", "label_fa", "counts_against_balance", "requires_approval", "requires_note",
    "requires_replacement", "employee_requestable", "confidential",
}


class LeaveTypeConflict(Exception):
    """A LeaveType change conflicts with its existing references or key."""


@transaction.atomic
def save_leave_type(*, actor, leave_type_id: int | None, data: dict,
                    reason: str, correlation_id: str = "") -> LeaveType:
    if not has_permission(
        authorization_context(actor), MANAGE_GLOBAL_LEAVE_TYPES,
    ):
        raise PermissionDenied("مدیریت سراسری انواع مرخصی مجاز نیست")
    if not isinstance(data, dict) or not data or set(data) - (set(LEAVE_TYPE_FIELDS) | {"reason"}):
        raise ValidationError("فیلدهای نوع مرخصی معتبر نیست")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError({"reason": "دلیل تغییر الزامی است"})
    if len(reason.strip()) > 500:
        raise ValidationError({"reason": "دلیل تغییر حداکثر ۵۰۰ نویسه است"})
    if not isinstance(correlation_id, str) or len(correlation_id) > 128:
        raise ValidationError({"correlationId": "شناسه همبستگی معتبر نیست"})

    item = (
        LeaveType.objects.select_for_update().get(pk=leave_type_id)
        if leave_type_id is not None
        else LeaveType()
    )
    before = audit_services.snapshot(item) if item.pk is not None else None
    old_values = {
        model_key: getattr(item, model_key)
        for model_key in HISTORICAL_LEAVE_TYPE_FIELDS | {"enabled"}
    } if before is not None else {}
    for api_key, model_key in LEAVE_TYPE_FIELDS.items():
        if api_key in data:
            setattr(item, model_key, data[api_key])
    if LeaveType.objects.filter(key=item.key).exclude(pk=item.pk).exists():
        raise LeaveTypeConflict("کلید نوع مرخصی قبلاً استفاده شده است")
    if before is not None and any(
        old_values[field] != getattr(item, field)
        for field in HISTORICAL_LEAVE_TYPE_FIELDS
    ) and (
        LeaveRequest.objects.filter(leave_type_id=item.pk).exists()
        or LeaveEntitlement.objects.filter(leave_type_id=item.pk).exists()
    ):
        raise LeaveTypeConflict(
            "نوع مرخصی دارای سابقه است؛ تغییر کلید یا ویژگی‌های معنایی آن مجاز نیست"
        )
    item.full_clean()
    item.save()
    action = "leave_type.create"
    if before is not None:
        if old_values.get("enabled") != item.enabled:
            action = "leave_type.activate" if item.enabled else "leave_type.deactivate"
        else:
            action = "leave_type.update"
    audit_services.record_model_change(
        actor=actor,
        action=action,
        instance=item,
        origin="admin",
        before=before,
        reason=reason.strip(),
        correlation_id=correlation_id,
    )
    return item


def load_settings() -> dict:
    stored = {s.key: s.value for s in SystemSetting.objects.all()}
    # مقدارهای ذخیره‌شده دوباره اعتبارسنجی می‌شوند؛ مقدار خراب خطا می‌دهد (نادیده گرفته نمی‌شود)
    return S.resolve(stored)


def load_policy() -> LeavePolicy:
    return S.build_policy({s.key: s.value for s in SystemSetting.objects.all()})


@transaction.atomic
def set_setting(*, user, key: str, value, reason: str) -> dict:
    if not (reason or "").strip():
        raise S.SettingError("درج دلیل تغییر الزامی است")
    normalized = S.validate_setting(key, value)
    # سازگاری کل سیاست پس از تغییر بررسی می‌شود (مثلاً ترکیب نامعتبر)
    current = {s.key: s.value for s in SystemSetting.objects.select_for_update().all()}
    candidate = dict(current)
    candidate[key] = S.to_storable(normalized)
    S.build_policy(candidate)
    obj, _ = SystemSetting.objects.select_for_update().get_or_create(key=key)
    before = obj.value
    obj.value, obj.updated_by = S.to_storable(normalized), user
    obj.save()
    audit_services.record_audit(
        actor=user, origin="admin", action="setting.update",
        entity="SystemSetting", entity_id=key,
        before={"value": before}, after={"value": obj.value}, reason=reason,
    )
    return {key: normalized}


def _entry_of(h: PublicHoliday) -> H.HolidayEntry:
    return H.HolidayEntry(h.scope, h.jalali_month, h.jalali_day, h.label, h.jalali_year)


def _snapshot(h: PublicHoliday) -> dict:
    return {"scope": h.scope, "year": h.jalali_year, "month": h.jalali_month,
            "day": h.jalali_day, "label": h.label, "is_active": h.is_active}


@transaction.atomic
def add_holiday(*, user, scope: str, label: str, jalali_text: str, recurring: bool) -> PublicHoliday:
    entry = H.make_entry_from_text(scope, label, jalali_text, recurring)      # خطای قالب/تاریخ
    existing = [_entry_of(h) for h in PublicHoliday.objects.select_for_update().filter(
        scope=entry.scope, is_active=True,
    )]
    years = {entry.jalali_year} if entry.jalali_year else {
        1403, *(holiday.jalali_year for holiday in existing if holiday.jalali_year is not None),
    }
    H.check_duplicates(existing + [entry], years)
    h = PublicHoliday.objects.create(scope=entry.scope, jalali_year=entry.jalali_year,
                                     jalali_month=entry.jalali_month, jalali_day=entry.jalali_day,
                                     label=entry.label, created_by=user, updated_by=user)
    PublicHolidayHistory.objects.create(holiday_id=h.pk, action="create", before=None,
                                        after=_snapshot(h), actor=user)
    audit_services.record_model_change(
        actor=user, origin="admin", action="holiday.create", instance=h,
    )
    return h


@transaction.atomic
def update_holiday(*, user, holiday_id: int, scope: str, label: str,
                   jalali_text: str, recurring: bool, reason: str) -> PublicHoliday:
    if not (reason or "").strip():
        raise H.HolidayError("درج دلیل ویرایش الزامی است")
    holiday = PublicHoliday.objects.select_for_update().get(pk=holiday_id)
    if not holiday.is_active:
        raise H.HolidayError("تعطیلی غیرفعال قابل ویرایش نیست")
    entry = H.make_entry_from_text(scope, label, jalali_text, recurring)
    existing = [_entry_of(item) for item in PublicHoliday.objects.select_for_update().filter(
        scope=entry.scope, is_active=True,
    ).exclude(pk=holiday.pk)]
    years = {entry.jalali_year} if entry.jalali_year else {
        1403, *(item.jalali_year for item in existing if item.jalali_year is not None),
    }
    H.check_duplicates(existing + [entry], years)
    before = audit_services.snapshot(holiday)
    holiday.scope = entry.scope
    holiday.jalali_year = entry.jalali_year
    holiday.jalali_month = entry.jalali_month
    holiday.jalali_day = entry.jalali_day
    holiday.label = entry.label
    holiday.updated_by = user
    holiday.save()
    after = audit_services.snapshot(holiday)
    PublicHolidayHistory.objects.create(
        holiday_id=holiday.pk, action="update", before=before, after=after, actor=user,
    )
    audit_services.record_model_change(
        actor=user, origin="admin", action="holiday.update", instance=holiday,
        before=before, reason=reason,
    )
    return holiday


@transaction.atomic
def delete_holiday(*, user, holiday_id: int, reason: str) -> None:
    if not (reason or "").strip():
        raise H.HolidayError("درج دلیل حذف الزامی است")
    h = PublicHoliday.objects.select_for_update().get(pk=holiday_id)
    history_before = _snapshot(h)
    before = audit_services.snapshot(h)
    h.is_active = False
    h.updated_by = user
    h.save(update_fields=["is_active", "updated_by", "updated_at"])
    after = audit_services.snapshot(h)
    PublicHolidayHistory.objects.create(
        holiday_id=h.pk, action="deactivate", before=history_before,
        after=_snapshot(h), actor=user,
    )
    audit_services.record_model_change(
        actor=user, origin="admin", action="holiday.deactivate", instance=h,
        before=before, reason=reason,
    )


def entries_for(scopes) -> list[H.HolidayEntry]:
    return [_entry_of(h) for h in PublicHoliday.objects.filter(
        scope__in=list(scopes) + ["*"], is_active=True,
    )]
