from __future__ import annotations

import calendar
from datetime import datetime

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.utils import timezone

from . import audit_services
from .models import LeaveRequestAttachment


def _validate_retention_operator(actor, reason: str) -> None:
    if not getattr(actor, "is_active", False) or not getattr(actor, "is_superuser", False):
        raise ValueError("An active superuser is required for attachment retention actions.")
    if not reason or not reason.strip() or len(reason) > 500:
        raise ValueError("A non-empty audit reason of at most 500 characters is required.")


def subtract_calendar_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year, month = divmod(month_index, 12)
    month += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def set_attachment_legal_hold(
    attachment_id: int,
    *,
    actor,
    reason: str,
    enabled: bool,
) -> LeaveRequestAttachment:
    _validate_retention_operator(actor, reason)
    action = (
        "leave_request.attachment_legal_hold_set"
        if enabled
        else "leave_request.attachment_legal_hold_released"
    )
    with transaction.atomic():
        attachment = LeaveRequestAttachment.objects.select_for_update().get(pk=attachment_id)
        if attachment.deletion_status == LeaveRequestAttachment.DELETION_DELETED:
            raise ValueError("A deleted attachment cannot be placed on legal hold.")
        if attachment.deletion_status != LeaveRequestAttachment.DELETION_ACTIVE:
            raise ValueError("A pending deletion cannot change legal hold state.")
        if attachment.legal_hold == enabled:
            raise ValueError("The attachment already has the requested legal hold state.")
        attachment.legal_hold = enabled
        attachment.save(update_fields=["legal_hold"])
        audit_services.record_model_change(
            actor=actor,
            origin="admin",
            action=action,
            instance=attachment,
            reason=reason,
        )
    return attachment


def purge_attachment(
    attachment_id: int,
    *,
    actor,
    reason: str,
    now: datetime | None = None,
) -> LeaveRequestAttachment:
    _validate_retention_operator(actor, reason)
    now = now or timezone.now()
    with transaction.atomic():
        attachment = LeaveRequestAttachment.objects.select_for_update().get(pk=attachment_id)
        if attachment.legal_hold:
            raise ValueError("The attachment is under legal hold.")
        cutoff = subtract_calendar_months(
            now,
            settings.ZSH_ATTACHMENT_RETENTION_MONTHS,
        )
        if attachment.created_at > cutoff:
            raise ValueError("The attachment has not reached its retention deadline.")
        if attachment.deletion_status == LeaveRequestAttachment.DELETION_DELETED:
            return attachment
        if attachment.deletion_status == LeaveRequestAttachment.DELETION_ACTIVE:
            attachment.deletion_status = LeaveRequestAttachment.DELETION_PENDING
            attachment.deletion_requested_at = now
            attachment.deleted_by = actor
            attachment.deletion_reason = reason
            attachment.save(update_fields=[
                "deletion_status", "deletion_requested_at", "deleted_by", "deletion_reason",
            ])
            audit_services.record_model_change(
                actor=actor,
                origin="admin",
                action="leave_request.attachment_deletion_started",
                instance=attachment,
                reason=reason,
            )

    storage = attachment.file.storage
    storage_name = attachment.file.name
    if storage_name:
        storage.delete(storage_name)
        FileSystemStorage(location=settings.MEDIA_ROOT).delete(storage_name)

    with transaction.atomic():
        attachment = LeaveRequestAttachment.objects.select_for_update().get(pk=attachment_id)
        if attachment.deletion_status == LeaveRequestAttachment.DELETION_DELETED:
            return attachment
        attachment.deletion_status = LeaveRequestAttachment.DELETION_DELETED
        attachment.deleted_at = now
        attachment.scan_status = LeaveRequestAttachment.SCAN_DELETED
        attachment.save(update_fields=["deletion_status", "deleted_at", "scan_status"])
        audit_services.record_model_change(
            actor=actor,
            origin="admin",
            action="leave_request.attachment_deleted",
            instance=attachment,
            reason=reason,
        )
    return attachment
