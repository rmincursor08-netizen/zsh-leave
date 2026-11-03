from __future__ import annotations

import logging

from django.core.exceptions import SuspiciousFileOperation
from django.db import transaction
from django.utils import timezone

from . import audit_services
from .attachment_security import (
    MAX_ATTACHMENT_SIZE,
    AttachmentScanFailed,
    AttachmentScannerUnavailable,
    scan_attachment,
)
from .models import LeaveRequestAttachment

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = {
    LeaveRequestAttachment.SCAN_PENDING,
    LeaveRequestAttachment.SCAN_SCANNING,
    LeaveRequestAttachment.SCAN_UNAVAILABLE,
    LeaveRequestAttachment.SCAN_FAILED,
}


def scan_stored_attachment(attachment_id: int, *, actor=None) -> LeaveRequestAttachment:
    with transaction.atomic():
        attachment = LeaveRequestAttachment.objects.select_for_update().get(pk=attachment_id)
        if attachment.scan_status not in RETRYABLE_STATUSES:
            return attachment
        if attachment.deletion_status != LeaveRequestAttachment.DELETION_ACTIVE:
            return attachment

        previous_status = attachment.scan_status
        attachment.scan_status = LeaveRequestAttachment.SCAN_SCANNING
        attachment.scan_attempts += 1
        attachment.scan_started_at = timezone.now()
        attachment.scan_result_code = ""
        attachment.save(update_fields=[
            "scan_status", "scan_attempts", "scan_started_at", "scan_result_code",
        ])
        audit_services.record_model_change(
            actor=actor,
            origin="system",
            action="leave_request.attachment_scan_started",
            instance=attachment,
            before={"scanStatus": previous_status},
        )

        try:
            with attachment.file.storage.open(attachment.file.name, "rb") as file_handle:
                content = file_handle.read(MAX_ATTACHMENT_SIZE + 1)
            if (
                not content
                or len(content) > MAX_ATTACHMENT_SIZE
                or len(content) != attachment.file_size
            ):
                raise AttachmentScanFailed("stored_file_invalid")
            result = scan_attachment(content)
        except AttachmentScannerUnavailable:
            result = LeaveRequestAttachment.SCAN_UNAVAILABLE
            result_code = "scanner_unavailable"
        except AttachmentScanFailed as exc:
            result = LeaveRequestAttachment.SCAN_FAILED
            result_code = exc.code
        except (OSError, SuspiciousFileOperation):
            logger.error("Private attachment could not be read for scanning.")
            result = LeaveRequestAttachment.SCAN_FAILED
            result_code = "stored_file_unavailable"
        else:
            result_code = result

        attachment.scan_status = result
        attachment.scanned_at = timezone.now()
        attachment.scan_result_code = result_code
        attachment.save(update_fields=["scan_status", "scanned_at", "scan_result_code"])
        audit_services.record_model_change(
            actor=actor,
            origin="system",
            action="leave_request.attachment_scan_completed",
            instance=attachment,
            before={"scanStatus": LeaveRequestAttachment.SCAN_SCANNING},
        )
        return attachment
