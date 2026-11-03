"""Centralized, allowlisted audit snapshots and fail-closed event writes."""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from .models import AuditEvent


SNAPSHOT_FIELDS = {
    "Company": {
        "code": "code", "name": "name", "isActive": "is_active",
    },
    "OrganizationSite": {
        "companyId": "company_id", "code": "code", "name": "name",
        "isActive": "is_active",
    },
    "Department": {
        "companyId": "company_id", "parentId": "parent_id", "code": "code", "name": "name",
    },
    "EmployeeProfile": {
        "userId": "user_id", "companyId": "company_id", "siteId": "site_id",
        "departmentId": "department_id", "managerId": "manager_id",
        "startDate": "start_date", "endDate": "end_date", "isEmployee": "is_employee",
        "accountStatus": "account_status",
    },
    "EmployeeInvitation": {
        "employeeId": "employee_id", "purpose": "purpose", "status": "status",
        "expiresAt": "expires_at", "createdById": "created_by_id",
    },
    "RoleAssignment": {
        "userId": "user_id", "role": "role", "scope": "scope", "companyId": "company_id",
        "siteId": "site_id", "departmentId": "department_id", "employeeId": "employee_id",
        "isActive": "is_active", "createdById": "created_by_id", "revokedById": "revoked_by_id",
        "expiresAt": "expires_at",
    },
    "WorkPattern": {
        "name": "name", "companyId": "company_id", "siteId": "site_id",
        "departmentId": "department_id", "employeeId": "employee_id",
        "workingWeekdays": "working_weekdays", "startTime": "start_time",
        "endTime": "end_time", "breakMinutes": "break_minutes",
        "effectiveFrom": "effective_from", "effectiveTo": "effective_to",
        "isActive": "is_active",
    },
    "LeavePolicyVersion": {
        "scope": "scope", "companyId": "company_id", "siteId": "site_id",
        "departmentId": "department_id", "employeeId": "employee_id",
        "version": "version", "effectiveFrom": "effective_from", "values": "values",
        "createdById": "created_by_id",
    },
    "CalendarHoliday": {
        "title": "title", "startDate": "start_date", "endDate": "end_date",
        "scope": "scope", "companyId": "company_id", "siteId": "site_id",
        "kind": "holiday_kind", "isActive": "is_active",
    },
    "LeaveType": {
        "key": "key", "labelFa": "label_fa", "labelEn": "label_en", "color": "color",
        "countsAgainstBalance": "counts_against_balance",
        "requiresApproval": "requires_approval", "requiresNote": "requires_note",
        "requiresReplacement": "requires_replacement",
        "employeeRequestable": "employee_requestable", "confidential": "confidential",
        "enabled": "enabled", "sortOrder": "sort_order",
    },
    "LeaveEntitlement": {
        "employeeId": "employee_id", "leaveTypeId": "leave_type_id", "year": "year",
        "baseDays": "base_days", "carryOverDays": "carry_over_days",
        "manualAdjustment": "manual_adjustment",
    },
    "EntitlementEvent": {
        "entitlementId": "entitlement_id", "actorId": "actor_id", "kind": "kind",
        "before": "before", "after": "after", "requestId": "request_id",
        "baseDelta": "base_delta", "carryOverDelta": "carry_over_delta",
        "manualAdjustmentDelta": "manual_adjustment_delta",
        "reservedDelta": "reserved_delta", "usedDelta": "used_delta",
    },
    "LeaveRequest": {
        "employee_id": "employee_id", "manager_snapshot_id": "manager_snapshot_id",
        "leave_type_id": "leave_type_id", "policy_version_id": "policy_version_id",
        "start_date": "start_date", "end_date": "end_date", "working_days": "working_days",
        "status": "status", "replacement_id": "replacement_id", "supersedes_id": "supersedes_id",
    },
    "LeaveRequestComment": {
        "requestId": "request_id", "actorId": "actor_id",
    },
    "LeaveRequestAttachment": {
        "requestId": "request_id", "uploadedById": "uploaded_by_id",
        "contentType": "content_type", "fileSize": "file_size",
        "scanStatus": "scan_status", "scanAttempts": "scan_attempts",
        "scanStartedAt": "scan_started_at", "scannedAt": "scanned_at",
        "scanResultCode": "scan_result_code", "deletionStatus": "deletion_status",
        "legalHold": "legal_hold", "deletionRequestedAt": "deletion_requested_at",
        "deletedAt": "deleted_at", "deletedById": "deleted_by_id",
    },
    "BradfordNote": {
        "employeeId": "employee_id", "updatedById": "updated_by_id",
    },
    "ContractPeriod": {
        "employeeId": "employee_id", "startDate": "start_date", "endDate": "end_date",
        "workDays": "work_days", "contractPercent": "contract_percent",
        "createdById": "created_by_id",
    },
    "SystemSetting": {
        "key": "key", "value": "value", "updatedById": "updated_by_id",
    },
    "PublicHoliday": {
        "scope": "scope", "year": "jalali_year", "month": "jalali_month",
        "day": "jalali_day", "label": "label", "isActive": "is_active",
    },
    "PublicHolidayHistory": {
        "holidayId": "holiday_id", "action": "action", "before": "before", "after": "after",
    },
    "CalendarHolidayHistory": {
        "holidayId": "holiday_id", "action": "action", "before": "before", "after": "after",
    },
}

SAFE_EXTRA_KEYS = {
    "EmployeeProfile": {"displayNameChanged"},
    "LeaveRequest": {"status", "from_status", "to_status", "reason_changed"},
    "EntitlementEvent": {
        "base_days", "carry_over_days", "manual_adjustment", "reserved_days", "used_days",
        "source_entitlement_id", "entitlementId", "employeeId", "leaveTypeId", "year",
    },
    "BradfordNote": {"contentChanged"},
    "Export": {"dataset", "format", "filters", "scope", "rowCount"},
}
SENSITIVE_KEYS = {
    "body", "comment", "decision_comment", "email", "medical", "password", "reason",
    "secret", "token", "file_name", "filename", "description", "source",
}
CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized in SENSITIVE_KEYS or any(
        term in normalized for term in ("password", "token", "secret", "medical")
    )


def _json_value(value):
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {
            key: _json_value(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported audit snapshot value: {type(value).__name__}")


def snapshot(instance) -> dict:
    """Capture only the model's declared, non-secret audit fields."""
    model_name = instance.__class__.__name__
    fields = SNAPSHOT_FIELDS.get(model_name)
    if fields is None:
        raise TypeError(f"No safe audit snapshot is defined for {model_name}")
    result = {}
    for output_key, attribute in fields.items():
        value = getattr(instance, attribute)
        if _is_sensitive_key(output_key):
            continue
        result[output_key] = _json_value(value)
    return result


def _safe_payload(entity: str, payload: dict | None) -> dict | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise TypeError("Audit snapshots must be dictionaries or None")
    model_fields = SNAPSHOT_FIELDS.get(entity, {})
    allowed = set(model_fields) | SAFE_EXTRA_KEYS.get(entity, set())
    return {
        key: _json_value(value)
        for key, value in payload.items()
        if key in allowed and not _is_sensitive_key(key)
    }


def record_audit(
    *,
    actor,
    action: str,
    entity: str,
    entity_id,
    origin: str,
    before: dict | None = None,
    after: dict | None = None,
    reason: str = "",
    result: str = "ok",
    source_context: str | None = None,
    correlation_id: str | None = None,
) -> AuditEvent:
    if not action or len(action) > 64 or not entity or len(entity) > 64:
        raise ValueError("Audit action and entity are required and length-limited")
    if not origin or len(origin) > 16:
        raise ValueError("Audit origin is required and length-limited")
    if len(str(entity_id)) > 64:
        raise ValueError("Audit entity identifier exceeds 64 characters")
    if not isinstance(reason, str) or not isinstance(result, str):
        raise TypeError("Audit reason and result must be strings")

    from .middleware import current_audit_context

    context = current_audit_context()
    safe_correlation_id = (
        correlation_id
        if isinstance(correlation_id, str) and CORRELATION_PATTERN.fullmatch(correlation_id)
        else context["correlation_id"] if context else ""
    )
    return AuditEvent.objects.create(
        actor=actor,
        origin=origin,
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        before=_safe_payload(entity, before),
        after=_safe_payload(entity, after),
        reason=reason,
        result=result,
        correlation_id=safe_correlation_id,
        source=((source_context or context["source"]) if context else source_context or "")[:128],
    )


def record_model_change(
    *,
    actor,
    action: str,
    instance,
    origin: str,
    before: dict | None = None,
    reason: str = "",
    source_context: str | None = None,
    correlation_id: str | None = None,
) -> AuditEvent:
    entity = instance.__class__.__name__
    return record_audit(
        actor=actor,
        action=action,
        entity=entity,
        entity_id=instance.pk,
        origin=origin,
        before=before,
        after=snapshot(instance),
        reason=reason,
        source_context=source_context,
        correlation_id=correlation_id,
    )
