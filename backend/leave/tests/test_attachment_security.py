import datetime as dt
import importlib
import json
import os
import subprocess
import sys
import tempfile
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from leave import audit_services
from leave.attachment_security import (
    AttachmentScanFailed,
    InvalidAttachment,
    scan_attachment,
    validate_attachment,
)
from leave.attachment_scanning import scan_stored_attachment
from leave.attachment_services import subtract_calendar_months
from leave.attachment_storage import PrivateAttachmentStorage
from leave.models import (
    AuditEvent,
    Company,
    EmployeeProfile,
    LeaveRequest,
    LeaveRequestAttachment,
    LeaveType,
    RoleAssignment,
)
from leave.tests.attachment_helpers import valid_pdf


def valid_png():
    image = Image.new("RGB", (2, 2), "white")
    content = BytesIO()
    image.save(content, format="PNG")
    return content.getvalue()


def attachment_upload(name="evidence.pdf", content=None, content_type="application/pdf"):
    return SimpleUploadedFile(name, content if content is not None else valid_pdf(),
                              content_type=content_type)


class AttachmentSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.company = Company.objects.create(code="ATTACHMENT-A", name="Attachment A")
        self.other_company = Company.objects.create(code="ATTACHMENT-B", name="Attachment B")
        self.hr_user = user_model.objects.create_user(username="attachment-hr")
        self.employee_user = user_model.objects.create_user(username="attachment-employee")
        self.other_user = user_model.objects.create_user(username="attachment-outsider")
        self.other_hr_user = user_model.objects.create_user(username="attachment-other-hr")
        self.employee = EmployeeProfile.objects.create(
            user=self.employee_user, company=self.company, personnel_code="ATT-A-1",
            start_date=dt.date(2020, 1, 1),
        )
        self.other_employee = EmployeeProfile.objects.create(
            user=self.other_user, company=self.other_company, personnel_code="ATT-B-1",
            start_date=dt.date(2020, 1, 1),
        )
        self.other_hr_employee = EmployeeProfile.objects.create(
            user=self.other_hr_user, company=self.other_company, personnel_code="ATT-B-HR",
            start_date=dt.date(2020, 1, 1),
        )
        RoleAssignment.objects.create(
            user=self.hr_user, role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY, company=self.company,
        )
        RoleAssignment.objects.create(
            user=self.other_hr_user, role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY, company=self.other_company,
        )
        self.leave_type = LeaveType.objects.create(
            key="attachment-annual", label_fa="Annual", counts_against_balance=True,
        )
        self.request = self.create_request(self.employee)
        self.client = APIClient()
        self.client.force_authenticate(self.hr_user)
        self.temp_root = tempfile.TemporaryDirectory()
        self.override = override_settings(ZSH_ATTACHMENT_ROOT=self.temp_root.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.scanner_override = override_settings(
            ZSH_ATTACHMENT_SCANNER="leave.tests.test_attachment_security.scanner_accept",
            ZSH_ATTACHMENT_SCANNER_COMMAND=[],
        )
        self.scanner_override.enable()
        self.addCleanup(self.scanner_override.disable)
        self.addCleanup(self.temp_root.cleanup)

    def create_request(self, employee, *, manager=None):
        return LeaveRequest.objects.create(
            employee=employee,
            leave_type=self.leave_type,
            manager_snapshot=manager,
            start_date=dt.date(2026, 11, 2),
            end_date=dt.date(2026, 11, 2),
            working_days="1",
            status="PENDING",
        )

    def upload(self, upload=None, request=None):
        request = request or self.request
        return self.client.post(
            f"/api/v1/leave-requests/{request.pk}/attachments/",
            {"file": upload or attachment_upload()},
            format="multipart",
        )

    def upload_successfully(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.content)
        return LeaveRequestAttachment.objects.get(pk=response.json()["id"])

    def test_allowed_pdf_is_stored_privately_only_after_a_clean_scan(self):
        content = valid_pdf()
        response = self.upload(attachment_upload(content=content))

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["scanStatus"], "clean")
        attachment = LeaveRequestAttachment.objects.get(pk=response.json()["id"])
        self.assertTrue(attachment.file.name.startswith(
            f"leave-attachments/{self.request.pk}/",
        ))
        self.assertTrue(Path(attachment.file.path).is_relative_to(Path(self.temp_root.name)))
        self.assertEqual(Path(attachment.file.path).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(attachment.file.path).parent.stat().st_mode & 0o777, 0o700)
        self.assertFalse(Path(attachment.file.path).is_relative_to(
            Path(__file__).resolve().parents[3],
        ))
        with self.assertRaises(ValueError):
            attachment.file.url
        self.assertTrue(AuditEvent.objects.filter(
            entity="LeaveRequestAttachment",
            entity_id=str(attachment.pk),
            action="leave_request.attachment_scan_started",
        ).exists())
        self.assertTrue(AuditEvent.objects.filter(
            entity="LeaveRequestAttachment",
            entity_id=str(attachment.pk),
            action="leave_request.attachment_scan_completed",
        ).exists())

    def test_attachment_migration_copies_and_verifies_legacy_files_without_deleting_source(self):
        legacy_root = Path(self.temp_root.name) / "legacy"
        private_root = Path(self.temp_root.name) / "private"
        name = f"leave-attachments/{self.request.pk}/historical.pdf"
        content = valid_pdf()

        with override_settings(MEDIA_ROOT=legacy_root, ZSH_ATTACHMENT_ROOT=private_root):
            legacy_storage = FileSystemStorage(location=legacy_root)
            legacy_storage.save(name, ContentFile(content))
            row = SimpleNamespace(pk=17, file=SimpleNamespace(name=name))

            class AttachmentRows:
                def using(self, alias):
                    self.database_alias = alias
                    return self

                def exclude(self, **kwargs):
                    return self

                def iterator(self):
                    return iter([row])

            class HistoricalAttachment:
                objects = AttachmentRows()

            class HistoricalApps:
                def get_model(self, app_label, model_name):
                    self.assertion = (app_label, model_name)
                    return HistoricalAttachment

            migration = importlib.import_module(
                "leave.migrations.0019_attachment_security",
            )
            schema_editor = SimpleNamespace(
                connection=SimpleNamespace(alias="default"),
            )
            migration.copy_existing_attachments(HistoricalApps(), schema_editor)
            private_storage = PrivateAttachmentStorage()

            with legacy_storage.open(name, "rb") as source:
                self.assertEqual(source.read(), content)
            with private_storage.open(name, "rb") as copied:
                self.assertEqual(copied.read(), content)

    def test_retention_uses_calendar_months(self):
        end_of_month = dt.datetime(2026, 3, 31, 12, 0, tzinfo=dt.timezone.utc)

        self.assertEqual(
            subtract_calendar_months(end_of_month, 1),
            dt.datetime(2026, 2, 28, 12, 0, tzinfo=dt.timezone.utc),
        )

    def test_valid_image_is_accepted_and_malformed_image_is_rejected(self):
        valid = self.upload(attachment_upload(
            "evidence.png", valid_png(), "image/png",
        ))
        malformed = self.upload(attachment_upload(
            "malformed.png", b"\x89PNG\r\n\x1a\nnot-an-image", "image/png",
        ))

        self.assertEqual(valid.status_code, 201, valid.content)
        self.assertEqual(malformed.status_code, 400)

    def test_extension_mime_signature_and_malformed_pdf_are_rejected(self):
        disallowed_extension = self.upload(attachment_upload(
            "evidence.txt", valid_pdf(), "application/pdf",
        ))
        mime_spoof = self.upload(attachment_upload(
            "evidence.pdf", valid_pdf(), "image/png",
        ))
        signature_mismatch = self.upload(attachment_upload(
            "evidence.pdf", b"not a pdf", "application/pdf",
        ))
        malformed_pdf = self.upload(attachment_upload(
            "evidence.pdf", b"%PDF-1.7\nxref\n%%EOF", "application/pdf",
        ))

        self.assertEqual(disallowed_extension.status_code, 400)
        self.assertEqual(mime_spoof.status_code, 400)
        self.assertEqual(signature_mismatch.status_code, 400)
        self.assertEqual(malformed_pdf.status_code, 400)
        self.assertEqual(LeaveRequestAttachment.objects.count(), 0)

    def test_oversized_and_path_traversal_filenames_are_rejected(self):
        oversized = self.upload(attachment_upload(
            "large.pdf", b"x" * (10 * 1024 * 1024 + 1), "application/pdf",
        ))
        traversal_content = BytesIO(valid_pdf())
        traversal_upload = SimpleNamespace(
            name="../../escape.pdf",
            size=len(traversal_content.getvalue()),
            content_type="application/pdf",
            read=traversal_content.read,
            seek=traversal_content.seek,
        )

        self.assertEqual(oversized.status_code, 400)
        with self.assertRaises(InvalidAttachment):
            validate_attachment(traversal_upload)
        self.assertEqual(LeaveRequestAttachment.objects.count(), 0)

    def test_authenticated_employee_and_scoped_hr_can_download_but_others_cannot(self):
        attachment = self.upload_successfully()
        url = (
            f"/api/v1/leave-requests/{self.request.pk}/attachments/"
            f"{attachment.pk}/"
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["Content-Security-Policy"], "sandbox; default-src 'none'")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Pragma"], "no-cache")
        self.assertEqual(b"".join(response.streaming_content), valid_pdf())

        self.client.force_authenticate(user=None)
        unauthenticated = self.client.get(url)
        self.assertIn(unauthenticated.status_code, {401, 403})

        self.client.force_authenticate(self.employee_user)
        own_download = self.client.get(url)
        self.assertEqual(own_download.status_code, 200)

        self.client.force_authenticate(self.other_user)
        cross_employee = self.client.get(url)
        self.assertEqual(cross_employee.status_code, 404)

        self.client.force_authenticate(self.other_hr_user)
        cross_company = self.client.get(url)
        self.assertEqual(cross_company.status_code, 404)

    def test_only_clean_active_attachments_can_be_downloaded(self):
        attachment = self.upload_successfully()
        url = f"/api/v1/leave-requests/{self.request.pk}/attachments/{attachment.pk}/"
        for status in (
            LeaveRequestAttachment.SCAN_PENDING,
            LeaveRequestAttachment.SCAN_SCANNING,
            LeaveRequestAttachment.SCAN_REJECTED,
            LeaveRequestAttachment.SCAN_UNAVAILABLE,
            LeaveRequestAttachment.SCAN_FAILED,
        ):
            with self.subTest(scan_status=status):
                LeaveRequestAttachment.objects.filter(pk=attachment.pk).update(
                    scan_status=status,
                )
                self.assertEqual(self.client.get(url).status_code, 404)
        LeaveRequestAttachment.objects.filter(pk=attachment.pk).update(
            scan_status=LeaveRequestAttachment.SCAN_CLEAN,
        )
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_manager_can_download_nonconfidential_direct_report_attachment(self):
        manager_user = get_user_model().objects.create_user(username="attachment-manager")
        manager = EmployeeProfile.objects.create(
            user=manager_user, company=self.company, personnel_code="ATT-A-M",
            start_date=dt.date(2020, 1, 1),
        )
        self.employee.manager = manager
        self.employee.save(update_fields=["manager"])
        self.request.manager_snapshot = manager
        self.request.save(update_fields=["manager_snapshot"])
        attachment = self.upload_successfully()
        self.client.force_authenticate(manager_user)

        response = self.client.get(
            f"/api/v1/leave-requests/{self.request.pk}/attachments/{attachment.pk}/",
        )

        self.assertEqual(response.status_code, 200)

    def test_missing_storage_object_returns_not_found_without_disclosing_path(self):
        attachment = self.upload_successfully()
        attachment.file.storage.delete(attachment.file.name)

        response = self.client.get(
            f"/api/v1/leave-requests/{self.request.pk}/attachments/{attachment.pk}/",
        )

        self.assertEqual(response.status_code, 404)

    def test_storage_failure_is_reported_without_creating_metadata(self):
        with patch.object(PrivateAttachmentStorage, "_save", side_effect=OSError):
            response = self.upload()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(LeaveRequestAttachment.objects.count(), 0)

    def test_database_failure_cleans_up_orphaned_storage_object(self):
        with patch.object(
            audit_services, "record_model_change", side_effect=RuntimeError("audit failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.upload()

        self.assertEqual(LeaveRequestAttachment.objects.count(), 0)
        stored_files = list(Path(self.temp_root.name).rglob("*"))
        self.assertEqual([path for path in stored_files if path.is_file()], [])

    def test_unavailable_scanner_fails_closed_and_clean_scanner_is_explicit(self):
        with override_settings(ZSH_ATTACHMENT_SCANNER="", ZSH_ATTACHMENT_SCANNER_COMMAND=[]):
            unavailable = self.upload()
        self.assertEqual(unavailable.status_code, 202)
        unavailable_row = LeaveRequestAttachment.objects.get(pk=unavailable.json()["id"])
        self.assertEqual(
            unavailable_row.scan_status,
            LeaveRequestAttachment.SCAN_UNAVAILABLE,
        )
        self.assertEqual(
            self.client.get(unavailable.json()["downloadUrl"]).status_code,
            404,
        )

        with override_settings(
            ZSH_ATTACHMENT_SCANNER="leave.tests.test_attachment_security.scanner_reject",
        ):
            infected = self.upload()
        self.assertEqual(infected.status_code, 202)
        infected_row = LeaveRequestAttachment.objects.get(pk=infected.json()["id"])
        self.assertEqual(
            infected_row.scan_status,
            LeaveRequestAttachment.SCAN_REJECTED,
        )
        self.assertEqual(
            self.client.get(infected.json()["downloadUrl"]).status_code,
            404,
        )

        with override_settings(
            ZSH_ATTACHMENT_SCANNER="leave.tests.test_attachment_security.scanner_accept",
        ):
            accepted = self.upload()
        self.assertEqual(accepted.status_code, 201, accepted.content)
        self.assertEqual(accepted.json()["scanStatus"], "clean")

    def test_scanner_command_timeout_crash_and_invalid_output_never_clean(self):
        commands = [
            [sys.executable, "-c", "import time; time.sleep(2)"],
            [sys.executable, "-c", "raise SystemExit(2)"],
            [sys.executable, "-c", "print('not-json')"],
            [sys.executable, "-c", "print('{\"status\":\"maybe\"}')"],
            [sys.executable, "-c",
             "print('{\"status\":\"clean\",\"status\":\"rejected\"}')"],
            [sys.executable, "-c", "print('x' * 70000)"],
        ]
        for command in commands:
            with self.subTest(command=command[-1]), override_settings(
                ZSH_ATTACHMENT_SCANNER="",
                ZSH_ATTACHMENT_SCANNER_COMMAND=command,
                ZSH_ATTACHMENT_SCANNER_TIMEOUT_SECONDS=1,
            ):
                with self.assertRaises(AttachmentScanFailed):
                    scan_attachment(valid_pdf())

        with override_settings(
            ZSH_ATTACHMENT_SCANNER="",
            ZSH_ATTACHMENT_SCANNER_COMMAND=[sys.executable, "-c",
                                           "print('{\"status\":\"clean\"}')"],
        ):
            self.assertEqual(scan_attachment(valid_pdf()), "clean")
        with override_settings(
            ZSH_ATTACHMENT_SCANNER="",
            ZSH_ATTACHMENT_SCANNER_COMMAND=[sys.executable, "-c",
                                           "print('{\"status\":\"rejected\"}')"],
        ):
            self.assertEqual(scan_attachment(valid_pdf()), "rejected")

    def test_scanner_command_failure_is_recorded_and_never_downloadable(self):
        with override_settings(
            ZSH_ATTACHMENT_SCANNER="",
            ZSH_ATTACHMENT_SCANNER_COMMAND=[sys.executable, "-c", "raise SystemExit(2)"],
        ):
            response = self.upload()
        self.assertEqual(response.status_code, 202)
        attachment = LeaveRequestAttachment.objects.get(pk=response.json()["id"])
        self.assertEqual(attachment.scan_status, LeaveRequestAttachment.SCAN_FAILED)
        self.assertEqual(attachment.scan_result_code, "scanner_process_failed")
        self.assertEqual(self.client.get(response.json()["downloadUrl"]).status_code, 404)

    def test_production_settings_fail_fast_without_scanner_command(self):
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "ZSH_SECRET_KEY": "production-settings-test-secret-key-long-enough-123456789",
            "ZSH_ALLOWED_HOSTS": "testserver",
            "ZSH_DB_USER": "zsh_app",
            "ZSH_DB_PASSWORD": "test-password",
            "ZSH_ATTACHMENT_ROOT": "/tmp/zsh-attachment-production-settings-test",
        }
        settings_module = "import config.settings_prod"
        missing = subprocess.run(
            [sys.executable, "-c", settings_module],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("ZSH_ATTACHMENT_SCANNER_COMMAND", missing.stderr)

        env["ZSH_ATTACHMENT_SCANNER_COMMAND"] = json.dumps(["/usr/bin/true"])
        configured = subprocess.run(
            [sys.executable, "-c", settings_module],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)

    def test_retry_rescans_failures_idempotently_and_keeps_final_result(self):
        with override_settings(ZSH_ATTACHMENT_SCANNER="", ZSH_ATTACHMENT_SCANNER_COMMAND=[]):
            upload_response = self.upload()
        attachment = LeaveRequestAttachment.objects.get(pk=upload_response.json()["id"])
        self.assertEqual(attachment.scan_status, LeaveRequestAttachment.SCAN_UNAVAILABLE)
        with override_settings(
            ZSH_ATTACHMENT_SCANNER="leave.tests.test_attachment_security.scanner_accept",
        ):
            retried = scan_stored_attachment(attachment.pk)
            self.assertEqual(retried.scan_status, LeaveRequestAttachment.SCAN_CLEAN)
            attempts = retried.scan_attempts
            repeated = scan_stored_attachment(attachment.pk)
        self.assertEqual(repeated.scan_status, LeaveRequestAttachment.SCAN_CLEAN)
        self.assertEqual(repeated.scan_attempts, attempts)

    def test_attachment_retention_requires_approval_and_audits_holds_and_deletion(self):
        actor = get_user_model().objects.create_superuser(username="attachment-operator")
        attachment = self.upload_successfully()
        old_created_at = subtract_calendar_months(timezone.now(), 3)
        LeaveRequestAttachment.objects.filter(pk=attachment.pk).update(
            created_at=old_created_at,
        )
        attachment.refresh_from_db()

        output = StringIO()
        call_command("manage_attachments", stdout=output)
        self.assertIn(attachment.pk, json.loads(output.getvalue())["eligibleAttachmentIds"])

        with self.assertRaises(CommandError):
            call_command(
                "manage_attachments", action="purge", attachment_id=attachment.pk,
                actor=actor.username, reason="retention complete",
            )
        call_command(
            "manage_attachments", action="hold", attachment_id=attachment.pk,
            actor=actor.username, reason="legal preservation", approve=True,
        )
        with self.assertRaises(CommandError):
            call_command(
                "manage_attachments", action="purge", attachment_id=attachment.pk,
                actor=actor.username, reason="retention complete", approve=True,
            )
        call_command(
            "manage_attachments", action="release-hold", attachment_id=attachment.pk,
            actor=actor.username, reason="hold released", approve=True,
        )
        legacy_root = Path(self.temp_root.name) / "legacy-media"
        legacy_storage = FileSystemStorage(location=legacy_root)
        legacy_storage.save(attachment.file.name, ContentFile(valid_pdf()))
        with override_settings(MEDIA_ROOT=legacy_root):
            call_command(
                "manage_attachments", action="purge", attachment_id=attachment.pk,
                actor=actor.username, reason="retention complete", approve=True,
            )

        attachment.refresh_from_db()
        self.assertEqual(attachment.deletion_status, LeaveRequestAttachment.DELETION_DELETED)
        self.assertIsNotNone(attachment.deleted_at)
        self.assertFalse(attachment.file.storage.exists(attachment.file.name))
        self.assertFalse(legacy_storage.exists(attachment.file.name))
        self.assertEqual(
            set(AuditEvent.objects.filter(entity="LeaveRequestAttachment").values_list(
                "action", flat=True,
            )),
            {
                "leave_request.attachment_added",
                "leave_request.attachment_scan_started",
                "leave_request.attachment_scan_completed",
                "leave_request.attachment_legal_hold_set",
                "leave_request.attachment_legal_hold_released",
                "leave_request.attachment_deletion_started",
                "leave_request.attachment_deleted",
            },
        )
        response = self.client.get(
            f"/api/v1/leave-requests/{self.request.pk}/attachments/{attachment.pk}/",
        )
        self.assertEqual(response.status_code, 404)

    def test_symlinked_storage_directory_cannot_serve_a_private_file(self):
        attachment = self.upload_successfully()
        storage = attachment.file.storage
        storage.delete(attachment.file.name)
        attachment_directory = Path(self.temp_root.name) / "leave-attachments" / str(
            self.request.pk,
        )
        outside = Path(self.temp_root.name) / "outside"
        outside.mkdir()
        attachment_directory.rmdir()
        attachment_directory.symlink_to(outside, target_is_directory=True)

        response = self.client.get(
            f"/api/v1/leave-requests/{self.request.pk}/attachments/{attachment.pk}/",
        )

        self.assertEqual(response.status_code, 404)

    def test_symlinked_storage_directory_rejects_writes_before_they_escape(self):
        attachment = self.upload_successfully()
        storage = attachment.file.storage
        storage.delete(attachment.file.name)
        attachment_directory = Path(self.temp_root.name) / "leave-attachments" / str(
            self.request.pk,
        )
        outside = Path(self.temp_root.name) / "outside"
        outside.mkdir()
        attachment_directory.rmdir()
        attachment_directory.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(SuspiciousFileOperation):
            storage.save(
                f"leave-attachments/{self.request.pk}/retry.pdf",
                ContentFile(valid_pdf()),
            )

        self.assertEqual(list(outside.iterdir()), [])


def scanner_accept(content: bytes) -> bool:
    return bool(content)


def scanner_reject(content: bytes) -> bool:
    return False
