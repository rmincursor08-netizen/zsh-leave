import csv
import datetime as dt
import tempfile
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from leave.calendar_services import create_policy_version, create_work_pattern
from leave.authorization import authorization_context
from leave.entitlement_services import create_entitlement
from leave.models import (
    Company,
    Department,
    EmployeeProfile,
    LeaveEntitlement,
    LeaveRequest,
    LeaveRequestAttachment,
    LeaveRequestEvent,
    LeaveType,
    RoleAssignment,
)
from leave.tests.attachment_helpers import valid_pdf
from zsh_core import settings_schema
from zsh_core.jalali import format_jalali


class MultiRolePolicyApiTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.company = Company.objects.create(code="POLICY-A", name="Policy company A")
        self.foreign_company = Company.objects.create(code="POLICY-B", name="Policy company B")
        self.hr_department = Department.objects.create(
            company=self.company, code="HR-SCOPE", name="HR scope",
        )
        self.team_department = Department.objects.create(
            company=self.company, code="TEAM-SCOPE", name="Team scope",
        )
        self.hr_manager_user = users.objects.create_user(
            username="policy-hr-manager", password="Policy-password-2026",
        )
        self.hr_manager = EmployeeProfile.objects.create(
            user=self.hr_manager_user,
            company=self.company,
            department=self.team_department,
            personnel_code="POLICY-HM",
            start_date=dt.date(2020, 1, 1),
        )
        RoleAssignment.objects.create(
            user=self.hr_manager_user,
            role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_DEPARTMENT,
            company=self.company,
            department=self.hr_department,
        )

        self.assigned_user = users.objects.create_user(username="policy-assigned")
        self.assigned = EmployeeProfile.objects.create(
            user=self.assigned_user,
            company=self.company,
            department=self.hr_department,
            personnel_code="POLICY-ASSIGNED",
            start_date=dt.date(2020, 1, 1),
        )
        self.team_user = users.objects.create_user(username="policy-team")
        self.team = EmployeeProfile.objects.create(
            user=self.team_user,
            company=self.company,
            department=self.team_department,
            manager=self.hr_manager,
            personnel_code="POLICY-TEAM",
            start_date=dt.date(2020, 1, 1),
        )
        self.outside_user = users.objects.create_user(username="policy-outside")
        self.outside = EmployeeProfile.objects.create(
            user=self.outside_user,
            company=self.company,
            department=self.team_department,
            personnel_code="POLICY-OUTSIDE",
            start_date=dt.date(2020, 1, 1),
        )
        self.foreign_user = users.objects.create_user(username="policy-foreign")
        self.foreign = EmployeeProfile.objects.create(
            user=self.foreign_user,
            company=self.foreign_company,
            personnel_code="POLICY-FOREIGN",
            start_date=dt.date(2020, 1, 1),
        )

        self.leave_type = LeaveType.objects.create(
            key="policy-annual",
            label_fa="Annual",
            counts_against_balance=True,
        )
        self.confidential_type = LeaveType.objects.create(
            key="policy-medical",
            label_fa="Medical",
            counts_against_balance=True,
            confidential=True,
            employee_requestable=False,
        )
        self.assigned_request = self.make_request(self.assigned)
        self.team_request = self.make_request(self.team, manager=self.hr_manager)
        self.outside_request = self.make_request(self.outside)
        self.foreign_request = self.make_request(self.foreign)

        self._storage_tmp = tempfile.TemporaryDirectory()
        storage_override = override_settings(ZSH_ATTACHMENT_ROOT=self._storage_tmp.name)
        storage_override.enable()
        self.addCleanup(storage_override.disable)
        self.addCleanup(self._storage_tmp.cleanup)
        self.team_attachment = self.make_attachment(self.team_request)

    def make_request(self, employee, *, manager=None, confidential=False):
        return LeaveRequest.objects.create(
            employee=employee,
            manager_snapshot=manager,
            leave_type=self.confidential_type if confidential else self.leave_type,
            start_date=dt.date(2026, 11, 3),
            end_date=dt.date(2026, 11, 3),
            working_days=Decimal("1"),
            status="PENDING",
            reason="scope canary",
        )

    def make_attachment(self, request, *, confidential=False):
        leave_request = (
            self.make_request(request.employee, manager=request.manager_snapshot, confidential=True)
            if confidential else request
        )
        storage = LeaveRequestAttachment._meta.get_field("file").storage
        name = storage.save(
            f"leave-attachments/{leave_request.pk}/policy.pdf",
            ContentFile(valid_pdf()),
        )
        return LeaveRequestAttachment.objects.create(
            request=leave_request,
            file=name,
            file_name="policy.pdf",
            content_type="application/pdf",
            file_size=len(valid_pdf()),
            scan_status=LeaveRequestAttachment.SCAN_CLEAN,
            scanned_at=timezone.now(),
            uploaded_by=self.assigned_user,
        )

    def session_client(self, user):
        user.set_password("Policy-password-2026")
        user.save(update_fields=["password"])
        client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(client.login(
            username=user.get_username(),
            password="Policy-password-2026",
        ))
        client.get("/api/v1/auth/me")
        csrf = client.cookies["csrftoken"].value
        return client, {"HTTP_X_CSRFTOKEN": csrf}

    def request_ids(self, client):
        response = client.get("/api/v1/leave-requests/")
        self.assertEqual(response.status_code, 200, response.content)
        return {row["id"] for row in response.json()["results"]}

    def test_hr_manager_union_is_consistent_for_read_and_export_within_company(self):
        client, _ = self.session_client(self.hr_manager_user)
        expected = {self.assigned_request.pk, self.team_request.pk}
        self.assigned_request.status = "APPROVED"
        self.assigned_request.save(update_fields=["status"])
        self.team_request.status = "APPROVED"
        self.team_request.save(update_fields=["status"])

        self.assertEqual(self.request_ids(client), expected)
        employee_response = client.get("/api/v1/employees/")
        self.assertEqual(employee_response.status_code, 200)
        employee_ids = {row["id"] for row in employee_response.json()}
        self.assertEqual(
            employee_ids,
            {self.hr_manager.pk, self.assigned.pk, self.team.pk},
        )

        detail = client.get(f"/api/v1/leave-requests/{self.team_request.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            client.get(f"/api/v1/leave-requests/{self.outside_request.pk}/").status_code,
            404,
        )
        self.assertEqual(
            client.get(f"/api/v1/leave-requests/{self.foreign_request.pk}/").status_code,
            404,
        )

        export = client.get("/api/v1/exports/leave-requests.csv")
        self.assertEqual(export.status_code, 200)
        content = b"".join(export.streaming_content).decode("utf-8-sig")
        exported = list(csv.DictReader(StringIO(content)))
        self.assertEqual(
            {int(row["id"]) for row in exported},
            expected,
        )
        self.assertNotIn("POLICY-FOREIGN", content)

        calendar = client.get(
            "/api/v1/calendar/events/?scope=org&from=2026-11-03&to=2026-11-03",
        )
        self.assertEqual(calendar.status_code, 200)
        self.assertEqual(
            {item["employeeId"] for item in calendar.json()},
            {self.assigned.pk, self.team.pk},
        )

        create_entitlement(
            employee=self.team,
            leave_type=self.leave_type,
            year=1405,
            base_days=Decimal("10"),
            actor=self.hr_manager_user,
            reason="opening",
            idempotency_key="policy-team-balance",
        )
        balance = client.get(
            f"/api/v1/balances/?year=1405&employee={self.team.pk}",
        )
        self.assertEqual(balance.status_code, 200)
        self.assertEqual(balance.json()[0]["employeeId"], self.team.pk)
        self.assertEqual(client.get("/api/v1/entitlements/?year=1405").status_code, 200)

    def test_role_specific_scopes_and_no_role_are_enforced_through_session_api(self):
        users = get_user_model()
        employee_client, _ = self.session_client(self.assigned_user)
        self.assertEqual(self.request_ids(employee_client), {self.assigned_request.pk})

        manager_user = users.objects.create_user(
            username="policy-manager", password="Policy-password-2026",
        )
        manager = EmployeeProfile.objects.create(
            user=manager_user,
            company=self.company,
            personnel_code="POLICY-MANAGER",
            start_date=dt.date(2020, 1, 1),
        )
        report_user = users.objects.create_user(username="policy-manager-report")
        report = EmployeeProfile.objects.create(
            user=report_user,
            company=self.company,
            manager=manager,
            personnel_code="POLICY-MANAGER-REPORT",
            start_date=dt.date(2020, 1, 1),
        )
        manager_request = self.make_request(report, manager=manager)
        manager_client, manager_csrf = self.session_client(manager_user)
        self.assertEqual(self.request_ids(manager_client), {manager_request.pk})
        manager_comment = manager_client.post(
            f"/api/v1/leave-requests/{manager_request.pk}/comments/",
            {"body": "Manager-only comment"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="policy-manager-comment",
            **manager_csrf,
        )
        self.assertEqual(manager_comment.status_code, 201, manager_comment.content)

        hr_only_user = users.objects.create_user(
            username="policy-hr-only", password="Policy-password-2026",
        )
        hr_only = EmployeeProfile.objects.create(
            user=hr_only_user,
            company=self.company,
            personnel_code="POLICY-HR-ONLY",
            start_date=dt.date(2020, 1, 1),
        )
        RoleAssignment.objects.create(
            user=hr_only_user,
            role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_DEPARTMENT,
            company=self.company,
            department=self.hr_department,
        )
        hr_self_request = self.make_request(hr_only)
        hr_client, _ = self.session_client(hr_only_user)
        self.assertEqual(
            self.request_ids(hr_client),
            {self.assigned_request.pk, hr_self_request.pk},
        )

        executive = users.objects.create_user(
            username="policy-exec", password="Policy-password-2026",
        )
        RoleAssignment.objects.create(
            user=executive,
            role=RoleAssignment.ROLE_EXEC,
            scope=RoleAssignment.SCOPE_COMPANY,
            company=self.company,
        )
        exec_client, _ = self.session_client(executive)
        self.assertEqual(self.request_ids(exec_client), set())
        self.assertEqual(exec_client.get("/api/v1/reports/absences").status_code, 200)

        admin = users.objects.create_superuser(
            username="policy-admin",
            email="policy-admin@example.test",
            password="Policy-password-2026",
        )
        admin_client, _ = self.session_client(admin)
        self.assertEqual(self.request_ids(admin_client), set())
        admin_employees = admin_client.get("/api/v1/employees/")
        self.assertEqual(admin_employees.status_code, 200)
        self.assertGreaterEqual(len(admin_employees.json()), 5)

        no_role = users.objects.create_user(
            username="policy-no-role", password="Policy-password-2026",
        )
        no_role_client, _ = self.session_client(no_role)
        self.assertEqual(self.request_ids(no_role_client), set())
        self.assertEqual(
            no_role_client.get("/api/v1/reports/absences").status_code,
            403,
        )

    def test_team_write_and_attachment_download_follow_effective_scope(self):
        manager_client, csrf = self.session_client(self.hr_manager_user)
        LeaveRequestEvent.objects.create(
            request=self.team_request,
            actor=self.assigned_user,
            origin="manager",
            event="submit",
            to_status="PENDING",
            reason="team event",
        )
        event_response = manager_client.get(
            f"/api/v1/leave-requests/{self.team_request.pk}/events/",
        )
        self.assertEqual(event_response.status_code, 200)
        self.assertEqual(event_response.json()[0]["reason"], "team event")
        notifications = manager_client.get("/api/v1/notifications/")
        self.assertEqual(notifications.status_code, 200)
        self.assertIn(self.team_request.pk, {
            item["requestId"] for item in notifications.json()
        })

        comment = manager_client.post(
            f"/api/v1/leave-requests/{self.team_request.pk}/comments/",
            {"body": "Manager update"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="policy-team-comment",
            **csrf,
        )
        self.assertEqual(comment.status_code, 201, comment.content)

        download = manager_client.get(
            f"/api/v1/leave-requests/{self.team_request.pk}/attachments/"
            f"{self.team_attachment.pk}/",
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/octet-stream")
        self.assertEqual(
            manager_client.get(
                f"/api/v1/leave-requests/{self.outside_request.pk}/attachments/"
                f"{self.team_attachment.pk}/",
            ).status_code,
            404,
        )

        confidential_request = self.make_request(
            self.team, manager=self.hr_manager, confidential=True,
        )
        confidential_attachment = self.make_attachment(confidential_request)
        self.assertEqual(
            manager_client.get(
                f"/api/v1/leave-requests/{confidential_request.pk}/attachments/"
                f"{confidential_attachment.pk}/",
            ).status_code,
            403,
        )

    def test_hr_manager_can_submit_for_same_company_direct_report_outside_assignment(self):
        values = {
            key: settings_schema.to_storable(value)
            for key, value in settings_schema.defaults().items()
            if key.startswith("policy.")
        }
        create_policy_version(
            actor=self.hr_manager_user,
            scope="company",
            company=self.company,
            effective_from=dt.date(2026, 1, 1),
            values=values,
            reason="Multi-role authorization API test policy",
        )
        create_work_pattern(
            actor=self.hr_manager_user,
            name="All days",
            company=self.company,
            weekdays=list(range(7)),
            start_time=dt.time(8, 0),
            end_time=dt.time(16, 0),
            break_minutes=0,
            effective_from=dt.date(2026, 1, 1),
        )
        submit_user = get_user_model().objects.create_user(username="policy-submit-report")
        submit_employee = EmployeeProfile.objects.create(
            user=submit_user,
            company=self.company,
            department=self.team_department,
            manager=self.hr_manager,
            personnel_code="POLICY-SUBMIT",
            start_date=dt.date(2020, 1, 1),
        )
        start = timezone.localdate() + dt.timedelta(days=30)
        create_entitlement(
            employee=submit_employee,
            leave_type=self.leave_type,
            year=int(format_jalali(start).split("/")[0]),
            base_days=Decimal("10"),
            actor=self.hr_manager_user,
            reason="opening",
            idempotency_key="policy-manager-submit-balance",
        )
        client, csrf = self.session_client(self.hr_manager_user)

        response = client.post(
            "/api/v1/leave-requests/",
            {
                "employeeId": submit_employee.pk,
                "leaveTypeKey": self.leave_type.key,
                "start": start.isoformat(),
                "end": start.isoformat(),
                "reason": "Manager submission under approved union policy",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="policy-manager-submit",
            **csrf,
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["employeeId"], submit_employee.pk)
        self.assertTrue(
            LeaveRequest.objects.filter(
                pk=response.json()["id"],
                employee=submit_employee,
            ).exists()
        )

    def test_expired_and_revoked_hr_assignments_do_not_grant_scope(self):
        expired_user = get_user_model().objects.create_user(
            username="policy-expired-hr", password="Policy-password-2026",
        )
        expired_profile = EmployeeProfile.objects.create(
            user=expired_user,
            company=self.company,
            personnel_code="POLICY-EXPIRED",
            start_date=dt.date(2020, 1, 1),
        )
        expired = RoleAssignment.objects.create(
            user=expired_user,
            role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_DEPARTMENT,
            company=self.company,
            department=self.hr_department,
            expires_at=timezone.now() - dt.timedelta(seconds=1),
        )
        inactive_user = get_user_model().objects.create_user(
            username="policy-revoked-hr", password="Policy-password-2026",
        )
        inactive_profile = EmployeeProfile.objects.create(
            user=inactive_user,
            company=self.company,
            personnel_code="POLICY-REVOKED",
            start_date=dt.date(2020, 1, 1),
        )
        RoleAssignment.objects.create(
            user=inactive_user,
            role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_DEPARTMENT,
            company=self.company,
            department=self.hr_department,
            is_active=False,
        )

        for user, own_profile in (
            (expired_user, expired_profile),
            (inactive_user, inactive_profile),
        ):
            with self.subTest(user=user.username):
                client, _ = self.session_client(user)
                response = client.get("/api/v1/employees/")
                self.assertEqual(response.status_code, 200)
                ids = {row["id"] for row in response.json()}
                self.assertNotIn(self.assigned.pk, ids)
                if own_profile:
                    self.assertEqual(ids, {own_profile.pk})
                else:
                    self.assertEqual(ids, set())
                self.assertFalse(authorization_context(user).has_role("hr"))

    def test_expired_assignment_is_audited_and_replaced_by_expiring_grant(self):
        users = get_user_model()
        expired_user = users.objects.create_user(username="policy-renewed")
        expired = RoleAssignment.objects.create(
            user=expired_user,
            role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_DEPARTMENT,
            company=self.company,
            department=self.hr_department,
            expires_at=timezone.now() - dt.timedelta(seconds=1),
        )
        admin = users.objects.create_superuser(
            username="policy-role-admin",
            email="policy-role-admin@example.test",
            password="Policy-password-2026",
        )
        client, csrf = self.session_client(admin)
        new_expiry = timezone.now() + dt.timedelta(days=30)
        response = client.post(
            "/api/v1/admin/role-assignments/",
            {
                "userId": expired_user.pk,
                "role": RoleAssignment.ROLE_HR,
                "scope": RoleAssignment.SCOPE_DEPARTMENT,
                "companyId": self.company.pk,
                "departmentId": self.hr_department.pk,
                "expiresAt": new_expiry.isoformat(),
            },
            format="json",
            **csrf,
        )
        self.assertEqual(response.status_code, 201, response.content)
        expired.refresh_from_db()
        self.assertFalse(expired.is_active)
        replacement = RoleAssignment.objects.get(pk=response.json()["id"])
        self.assertEqual(replacement.expires_at, new_expiry)
        self.assertTrue(authorization_context(expired_user).has_role("hr"))

    def test_hr_manager_can_write_for_team_but_not_foreign_company(self):
        client, csrf = self.session_client(self.hr_manager_user)
        employee_response = client.post(
            "/api/v1/employees/",
            {
                "displayName": "Team employee",
                "personnelCode": "POLICY-NEW-TEAM",
                "companyId": self.company.pk,
                "departmentId": self.team_department.pk,
                "managerId": self.hr_manager.pk,
            },
            format="json",
            **csrf,
        )
        self.assertEqual(employee_response.status_code, 201, employee_response.content)
        self.assertEqual(employee_response.json()["companyId"], self.company.pk)

        entitlement = create_entitlement(
            employee=self.team,
            leave_type=self.leave_type,
            year=1405,
            base_days=Decimal("10"),
            actor=self.hr_manager_user,
            reason="opening",
            idempotency_key="policy-team-opening",
        )
        response = client.post(
            f"/api/v1/entitlements/{entitlement.pk}/adjust",
            {"days": "1", "reason": "approved union scope"},
            format="json",
            **csrf,
        )
        self.assertEqual(response.status_code, 200, response.content)

        foreign_entitlement = create_entitlement(
            employee=self.foreign,
            leave_type=self.leave_type,
            year=1405,
            base_days=Decimal("10"),
            actor=self.hr_manager_user,
            reason="opening",
            idempotency_key="policy-foreign-opening",
        )
        denied = client.post(
            f"/api/v1/entitlements/{foreign_entitlement.pk}/adjust",
            {"days": "1", "reason": "out of scope"},
            format="json",
            **csrf,
        )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(
            LeaveEntitlement.objects.get(pk=foreign_entitlement.pk).manual_adjustment,
            Decimal("0.0"),
        )
