import datetime as dt
import json
import sys
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.test import APIClient

from leave.authorization import MANAGE_GLOBAL_LEAVE_TYPES, authorization_context, has_permission
from leave.authorization_services import grant_role_assignment
from leave.admin_services import save_leave_type
from leave.entitlement_services import create_entitlement
from leave.employment_services import organization_localdate
from leave.invitation_services import suspend_employee
from leave.models import (
    AuditEvent,
    BradfordNote,
    Company,
    Department,
    EntitlementEvent,
    EmployeeProfile,
    LeaveEntitlement,
    LeaveRequest,
    LeaveRequestEvent,
    LeaveType,
    RoleAssignment,
    SystemSetting,
)
from leave.tests.attachment_helpers import valid_pdf


class FrontendApiTests(TestCase):
    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.company = Company.objects.create(code="FRONTEND", name="Frontend company")
        self.department = Department.objects.create(
            company=self.company, code="OPS", name="Operations",
        )
        self.hr_user = user_model.objects.create_user(username="frontend-hr")
        self.employee_user = user_model.objects.create_user(username="frontend-employee")
        self.hr = EmployeeProfile.objects.create(
            user=self.hr_user, company=self.company, department=self.department,
            personnel_code="HR-1", start_date=dt.date(2020, 1, 1),
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.employee_user, company=self.company, department=self.department,
            personnel_code="E-1", start_date=dt.date(2020, 1, 1),
        )
        RoleAssignment.objects.create(
            user=self.hr_user, role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY, company=self.company,
        )
        self.leave_type = LeaveType.objects.create(
            key="annual", label_fa="استحقاقی", counts_against_balance=True,
        )
        self.confidential_type = LeaveType.objects.create(
            key="medical", label_fa="محرمانه‌پزشکی", counts_against_balance=True,
            employee_requestable=False, confidential=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.hr_user)

    def test_frontend_http_client_paths_resolve_to_backend_routes(self):
        routes = {
            "/api/v1/auth/login": "auth-login",
            "/api/v1/auth/logout": "auth-logout",
            "/api/v1/auth/me": "auth-me",
            "/api/v1/auth/password": "auth-password",
            "/api/v1/auth/activate": "auth-activate",
            "/api/v1/leave-types/": "leave-types",
            "/api/v1/leave-types/1/": "leave-type-detail",
            "/api/v1/leave-requests/": "leave-requests",
            "/api/v1/leave-requests/1/": "leave-request-detail",
            "/api/v1/leave-requests/1/approve": "leave-request-action",
            "/api/v1/leave-requests/1/comments/": "leave-request-comments",
            "/api/v1/leave-requests/1/attachments/": "leave-request-attachments",
            "/api/v1/leave-requests/1/attachments/2/": "leave-request-attachment-download",
            "/api/v1/balances/": "balances",
            "/api/v1/entitlements/": "entitlements",
            "/api/v1/entitlements/1/adjust": "entitlement-adjustment",
            "/api/v1/entitlements/import/": "entitlements-import",
            "/api/v1/hr/absences/": "hr-absence",
            "/api/v1/calendar/events/": "calendar-events",
            "/api/v1/calendar/holidays/": "calendar-holidays",
            "/api/v1/holidays/": "holidays",
            "/api/v1/holidays/1/": "holiday-detail",
            "/api/v1/notifications/": "notifications",
            "/api/v1/notifications/1/read": "notification-read",
            "/api/v1/employees/": "employees",
            "/api/v1/employees/1/": "employee-detail",
            "/api/v1/employees/1/invitation/": "employee-invitation",
            "/api/v1/employees/1/invitation/revoke": "employee-invitation-revoke",
            "/api/v1/employees/1/suspend": "employee-account-suspend",
            "/api/v1/employees/1/contract-periods/": "employee-contracts",
            "/api/v1/companies/": "companies",
            "/api/v1/companies/1/": "company-detail",
            "/api/v1/sites/": "sites",
            "/api/v1/sites/1/": "site-detail",
            "/api/v1/departments/": "departments",
            "/api/v1/departments/1/": "department-detail",
            "/api/v1/coverage/": "coverage",
            "/api/v1/reports/absences": "reports",
            "/api/v1/admin/settings/": "admin-settings",
            "/api/v1/admin/jobs/": "admin-jobs",
            "/api/v1/audit/events/": "audit-events",
            "/api/v1/ready/": "ready",
            "/api/v1/working-days/estimate": "working-days-estimate",
            "/api/v1/policy/client": "client-policy",
            "/api/v1/hr/bradford-notes/": "bradford-notes",
        }
        for path, route_name in routes.items():
            with self.subTest(path=path):
                self.assertEqual(resolve(path).url_name, route_name)

    def test_scoped_hr_cannot_create_or_mutate_global_leave_types(self):
        initial_count = LeaveType.objects.count()
        initial_audits = AuditEvent.objects.filter(entity="LeaveType").count()

        created = self.client.post(
            "/api/v1/leave-types/",
            {"key": "other", "labelFa": "Other", "reason": "catalog change"},
            format="json",
        )
        self.assertEqual(created.status_code, 403)
        self.assertEqual(LeaveType.objects.count(), initial_count)

        for field, value in (
            ("confidential", True),
            ("countsAgainstBalance", False),
            ("requiresApproval", False),
            ("enabled", False),
        ):
            with self.subTest(field=field):
                response = self.client.patch(
                    f"/api/v1/leave-types/{self.leave_type.pk}/",
                    {field: value, "reason": "unauthorized change"},
                    format="json",
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.client.patch(
                "/api/v1/leave-types/999999/",
                {"enabled": False, "reason": "object substitution"},
                format="json",
            ).status_code,
            403,
        )

        self.leave_type.refresh_from_db()
        self.assertTrue(self.leave_type.counts_against_balance)
        self.assertTrue(self.leave_type.requires_approval)
        self.assertFalse(self.leave_type.confidential)
        self.assertTrue(self.leave_type.enabled)
        self.assertEqual(AuditEvent.objects.filter(entity="LeaveType").count(), initial_audits)
        self.assertFalse(has_permission(
            authorization_context(self.hr_user), MANAGE_GLOBAL_LEAVE_TYPES,
        ))

    def test_leave_type_mutation_rejects_employee_and_manager(self):
        manager_user = get_user_model().objects.create_user(username="leave-type-manager")
        manager = EmployeeProfile.objects.create(
            user=manager_user, company=self.company, department=self.department,
            personnel_code="M-1", start_date=dt.date(2020, 1, 1),
        )
        self.employee.manager = manager
        self.employee.save(update_fields=["manager"])
        audit_count = AuditEvent.objects.filter(entity="LeaveType").count()

        for user in (self.employee_user, manager_user):
            with self.subTest(user=user.username):
                client = APIClient()
                client.force_authenticate(user)
                self.assertEqual(client.post(
                    "/api/v1/leave-types/",
                    {"key": "unauthorized", "labelFa": "No", "reason": "unauthorized"},
                    format="json",
                ).status_code, 403)
                self.assertEqual(client.patch(
                    f"/api/v1/leave-types/{self.leave_type.pk}/",
                    {"requiresApproval": False, "reason": "unauthorized"},
                    format="json",
                ).status_code, 403)

        self.leave_type.refresh_from_db()
        self.assertTrue(self.leave_type.requires_approval)
        self.assertEqual(AuditEvent.objects.filter(entity="LeaveType").count(), audit_count)

    def test_employee_cannot_reassign_manager_through_employee_api(self):
        manager_user = get_user_model().objects.create_user(username="unauthorized-manager")
        manager = EmployeeProfile.objects.create(
            user=manager_user, company=self.company, department=self.department,
            personnel_code="M-UNAUTH", start_date=dt.date(2020, 1, 1),
        )
        self.client.force_authenticate(self.employee_user)

        response = self.client.patch(
            f"/api/v1/employees/{self.employee.pk}/",
            {"managerId": manager.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.manager_id)

    def test_hr_employee_api_accepts_valid_manager_and_rejects_cycle(self):
        manager_payload = {
            "displayName": "Employee",
            "personnelCode": self.employee.personnel_code,
            "companyId": self.company.pk,
            "managerId": self.hr.pk,
        }
        assigned = self.client.patch(
            f"/api/v1/employees/{self.employee.pk}/", manager_payload, format="json",
        )
        self.assertEqual(assigned.status_code, 200, assigned.content)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.manager_id, self.hr.pk)

        cycle_payload = {
            "displayName": "Human resources",
            "personnelCode": self.hr.personnel_code,
            "companyId": self.company.pk,
            "managerId": self.employee.pk,
        }
        cycle = self.client.patch(
            f"/api/v1/employees/{self.hr.pk}/", cycle_payload, format="json",
        )
        self.assertEqual(cycle.status_code, 400, cycle.content)
        self.hr.refresh_from_db()
        self.assertIsNone(self.hr.manager_id)

    def test_global_admin_can_manage_and_audit_leave_types(self):
        admin = get_user_model().objects.create_user(username="leave-type-admin")
        RoleAssignment.objects.create(
            user=admin, role=RoleAssignment.ROLE_ADMIN, scope=RoleAssignment.SCOPE_GLOBAL,
        )
        client = APIClient()
        client.force_authenticate(admin)
        self.assertTrue(has_permission(
            authorization_context(admin), MANAGE_GLOBAL_LEAVE_TYPES,
        ))

        created = client.post(
            "/api/v1/leave-types/",
            {
                "key": "special", "labelFa": "ویژه", "confidential": True,
                "employeeRequestable": False, "countsAgainstBalance": True,
                "requiresApproval": False, "reason": "Approved policy update",
            },
            format="json",
            HTTP_X_CORRELATION_ID="leave-type-create-123",
        )
        self.assertEqual(created.status_code, 201, created.content)
        item = LeaveType.objects.get(key="special")
        create_audit = AuditEvent.objects.get(
            entity="LeaveType", entity_id=str(item.pk), action="leave_type.create",
        )
        self.assertEqual(create_audit.actor_id, admin.pk)
        self.assertIsNone(create_audit.before)
        self.assertEqual(create_audit.after["requiresApproval"], False)
        self.assertEqual(create_audit.reason, "Approved policy update")
        self.assertEqual(create_audit.correlation_id, "leave-type-create-123")
        self.assertIsNotNone(create_audit.created_at)
        duplicate = client.post(
            "/api/v1/leave-types/",
            {"key": "special", "labelFa": "Duplicate", "reason": "duplicate test"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.content)
        audit_response = client.get("/api/v1/audit/events/")
        self.assertEqual(audit_response.status_code, 200)
        serialized_audit = next(
            event for event in audit_response.json()
            if event["entity"] == "LeaveType" and event["entityId"] == str(item.pk)
        )
        self.assertEqual(serialized_audit["correlationId"], "leave-type-create-123")
        self.assertIsNone(serialized_audit["before"])
        self.assertEqual(serialized_audit["after"]["key"], "special")

        updated = client.patch(
            f"/api/v1/leave-types/{item.pk}/",
            {"requiresApproval": True, "reason": "  Approval policy restored  "},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        update_audit = AuditEvent.objects.get(
            entity="LeaveType", entity_id=str(item.pk), action="leave_type.update",
            reason="Approval policy restored",
        )
        self.assertFalse(update_audit.before["requiresApproval"])
        self.assertTrue(update_audit.after["requiresApproval"])

        deactivated = client.patch(
            f"/api/v1/leave-types/{item.pk}/",
            {"enabled": False, "reason": "Retired by policy"},
            format="json",
        )
        self.assertEqual(deactivated.status_code, 200)
        item.refresh_from_db()
        self.assertFalse(item.enabled)
        activated = client.patch(
            f"/api/v1/leave-types/{item.pk}/",
            {"enabled": True, "reason": "Reopened by policy"},
            format="json",
        )
        self.assertEqual(activated.status_code, 200, activated.content)
        activate_audit = AuditEvent.objects.get(
            entity="LeaveType", entity_id=str(item.pk), action="leave_type.activate",
        )
        self.assertEqual(activate_audit.actor_id, admin.pk)
        self.assertEqual(activate_audit.reason, "Reopened by policy")
        self.assertFalse(activate_audit.before["enabled"])
        self.assertTrue(activate_audit.after["enabled"])
        self.assertEqual(client.delete(f"/api/v1/leave-types/{item.pk}/").status_code, 405)

    def test_leave_type_read_access_is_separate_from_write_access(self):
        disabled = LeaveType.objects.create(
            key="disabled", label_fa="Disabled", enabled=False,
        )
        hr_response = self.client.get("/api/v1/leave-types/")
        self.assertEqual(hr_response.status_code, 200)
        self.assertEqual(
            {item["key"] for item in hr_response.json()}, {"annual", "medical"},
        )

        employee_client = APIClient()
        employee_client.force_authenticate(self.employee_user)
        employee_response = employee_client.get("/api/v1/leave-types/")
        self.assertEqual(employee_response.status_code, 200)
        self.assertEqual({item["key"] for item in employee_response.json()}, {"annual"})

        admin = get_user_model().objects.create_user(username="leave-type-read-admin")
        RoleAssignment.objects.create(
            user=admin, role=RoleAssignment.ROLE_ADMIN, scope=RoleAssignment.SCOPE_GLOBAL,
        )
        admin_client = APIClient()
        admin_client.force_authenticate(admin)
        admin_response = admin_client.get("/api/v1/leave-types/")
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(
            {item["key"] for item in admin_response.json()},
            {"annual", "medical", disabled.key},
        )

        anonymous = APIClient().get("/api/v1/leave-types/")
        self.assertIn(anonymous.status_code, (401, 403))

        executive = get_user_model().objects.create_user(username="leave-type-executive")
        RoleAssignment.objects.create(
            user=executive, role=RoleAssignment.ROLE_EXEC,
            scope=RoleAssignment.SCOPE_COMPANY, company=self.company,
        )
        executive_client = APIClient()
        executive_client.force_authenticate(executive)
        self.assertEqual(executive_client.get("/api/v1/leave-types/").status_code, 403)

    def test_scoped_hr_outside_a_company_cannot_write_global_leave_types(self):
        outside_company = Company.objects.create(code="OUTSIDE", name="Outside company")
        outside_hr = get_user_model().objects.create_user(username="outside-scope-hr")
        RoleAssignment.objects.create(
            user=outside_hr, role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY, company=outside_company,
        )
        client = APIClient()
        client.force_authenticate(outside_hr)

        response = client.patch(
            f"/api/v1/leave-types/{self.leave_type.pk}/",
            {"enabled": False, "reason": "out of scope attempt"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.leave_type.refresh_from_db()
        self.assertTrue(self.leave_type.enabled)

    def test_leave_type_mutations_without_reason_and_hr_self_grant_are_denied(self):
        admin = get_user_model().objects.create_user(username="reason-required-admin")
        RoleAssignment.objects.create(
            user=admin, role=RoleAssignment.ROLE_ADMIN, scope=RoleAssignment.SCOPE_GLOBAL,
        )
        admin_client = APIClient()
        admin_client.force_authenticate(admin)
        audit_count = AuditEvent.objects.filter(entity="LeaveType").count()
        missing_reason = admin_client.patch(
            f"/api/v1/leave-types/{self.leave_type.pk}/",
            {"requiresApproval": False},
            format="json",
        )
        self.assertEqual(missing_reason.status_code, 400)
        for reason in ("", " \t\n", "x" * 501):
            with self.subTest(reason=repr(reason)):
                response = admin_client.patch(
                    f"/api/v1/leave-types/{self.leave_type.pk}/",
                    {"requiresApproval": False, "reason": reason},
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("reason", response.json().get("fields", {}))
        self.leave_type.refresh_from_db()
        self.assertTrue(self.leave_type.requires_approval)
        self.assertEqual(AuditEvent.objects.filter(entity="LeaveType").count(), audit_count)

        self_grant = self.client.post(
            "/api/v1/admin/role-assignments/",
            {
                "userId": self.hr_user.pk, "role": "admin", "scope": "global",
                "permission": MANAGE_GLOBAL_LEAVE_TYPES,
            },
            format="json",
        )
        self.assertEqual(self_grant.status_code, 403)
        self.assertFalse(RoleAssignment.objects.filter(
            user=self.hr_user, role=RoleAssignment.ROLE_ADMIN, is_active=True,
        ).exists())
        with self.assertRaises(PermissionDenied):
            grant_role_assignment(
                actor=self.hr_user, user_id=self.hr_user.pk,
                role=RoleAssignment.ROLE_ADMIN, scope=RoleAssignment.SCOPE_GLOBAL,
            )
        with self.assertRaises(PermissionDenied):
            save_leave_type(
                actor=self.hr_user, leave_type_id=self.leave_type.pk,
                data={"confidential": True}, reason="attempted service bypass",
            )
        self.leave_type.refresh_from_db()
        self.assertFalse(self.leave_type.confidential)

    def test_semantic_leave_type_changes_are_rejected_after_historical_use(self):
        admin = get_user_model().objects.create_user(username="leave-type-history-admin")
        RoleAssignment.objects.create(
            user=admin, role=RoleAssignment.ROLE_ADMIN, scope=RoleAssignment.SCOPE_GLOBAL,
        )
        client = APIClient()
        client.force_authenticate(admin)
        LeaveRequest.objects.create(
            employee=self.employee, leave_type=self.leave_type,
            start_date=dt.date(2026, 11, 2), end_date=dt.date(2026, 11, 2),
            working_days=Decimal("1"), status="APPROVED",
        )
        create_entitlement(
            employee=self.employee, leave_type=self.leave_type, year=1405,
            base_days=Decimal("10"), actor=self.hr_user, reason="history test",
            idempotency_key="leave-type-history-entitlement",
        )

        response = client.patch(
            f"/api/v1/leave-types/{self.leave_type.pk}/",
            {"labelFa": "Renamed historical type", "reason": "Rename request type"},
            format="json",
        )

        self.assertEqual(response.status_code, 409, response.content)
        self.leave_type.refresh_from_db()
        self.assertEqual(self.leave_type.label_fa, "استحقاقی")
        with self.assertRaises(ValidationError):
            save_leave_type(
                actor=admin, leave_type_id=self.leave_type.pk,
                data={"labelFa": "No reason"}, reason=" \t ",
            )

    def test_hr_pages_read_real_leave_and_organization_data(self):
        entitlement = create_entitlement(
            employee=self.employee, leave_type=self.leave_type, year=1405,
            base_days=Decimal("20"), actor=self.hr_user, reason="opening",
            idempotency_key="frontend-api-opening",
        )

        self.assertEqual(self.client.get("/api/v1/leave-types/").status_code, 200)
        employees = self.client.get("/api/v1/employees/").json()
        self.assertEqual({row["id"] for row in employees}, {self.hr.pk, self.employee.pk})
        self.assertEqual(self.client.get("/api/v1/companies/").json()[0]["id"], self.company.pk)
        self.assertEqual(self.client.get("/api/v1/departments/").json()[0]["id"], self.department.pk)

        balance = self.client.get("/api/v1/balances/?year=1405&employee=%s" % self.employee.pk)
        self.assertEqual(balance.status_code, 200)
        self.assertEqual(balance.json()[0]["remaining"], "20.0")
        self.assertEqual(self.client.get("/api/v1/entitlements/?year=1405").json()[0]["id"], entitlement.pk)

        contract = self.client.post(
            f"/api/v1/employees/{self.employee.pk}/contract-periods/",
            {
                "employeeId": self.employee.pk, "start": "2026-01-01", "end": None,
                "workDays": [5, 6, 0, 1, 2], "contractPercent": 100,
            },
            format="json",
        )
        self.assertEqual(contract.status_code, 201, contract.content)
        self.assertEqual(
            self.client.get(f"/api/v1/employees/{self.employee.pk}/contract-periods/").json()[0]["workDays"],
            [5, 6, 0, 1, 2],
        )

    def test_employee_api_status_uses_effective_employment_dates(self):
        today = organization_localdate()
        self.employee.end_date = today + dt.timedelta(days=2)
        self.employee.save(update_fields=["end_date"])
        future = EmployeeProfile.objects.create(
            user=get_user_model().objects.create_user(username="api-future-hire"),
            company=self.company, department=self.department, personnel_code="API-FUTURE",
            start_date=today + dt.timedelta(days=1),
        )
        suspended = EmployeeProfile.objects.create(
            user=get_user_model().objects.create_user(username="api-suspended"),
            company=self.company, department=self.department, personnel_code="API-SUSPENDED",
            start_date=today - dt.timedelta(days=1),
            account_status=EmployeeProfile.ACCOUNT_SUSPENDED,
        )

        employees = {
            row["id"]: row for row in self.client.get("/api/v1/employees/").json()
        }

        self.assertTrue(employees[self.employee.pk]["isActive"])
        self.assertFalse(employees[future.pk]["isActive"])
        self.assertFalse(employees[suspended.pk]["isActive"])

    def test_coverage_headcount_is_effective_for_each_day_including_history(self):
        today = organization_localdate()
        yesterday = today - dt.timedelta(days=1)
        tomorrow = today + dt.timedelta(days=1)
        self.employee.end_date = today
        self.employee.save(update_fields=["end_date"])

        starts_today = EmployeeProfile.objects.create(
            user=get_user_model().objects.create_user(username="coverage-start-today"),
            company=self.company, department=self.department, personnel_code="COV-TODAY",
            start_date=today, end_date=today,
        )
        starts_tomorrow = EmployeeProfile.objects.create(
            user=get_user_model().objects.create_user(username="coverage-future-start"),
            company=self.company, department=self.department, personnel_code="COV-FUTURE",
            start_date=tomorrow,
        )
        future_end = EmployeeProfile.objects.create(
            user=get_user_model().objects.create_user(username="coverage-future-end"),
            company=self.company, department=self.department, personnel_code="COV-END",
            start_date=yesterday - dt.timedelta(days=2), end_date=tomorrow,
        )
        past_end = EmployeeProfile.objects.create(
            user=get_user_model().objects.create_user(username="coverage-past-end"),
            company=self.company, department=self.department, personnel_code="COV-PAST",
            start_date=yesterday - dt.timedelta(days=2), end_date=yesterday - dt.timedelta(days=1),
        )

        with CaptureQueriesContext(connection) as one_day_queries:
            one_day_response = self.client.get(
                f"/api/v1/coverage/?from={today.isoformat()}&to={today.isoformat()}",
            )
        with CaptureQueriesContext(connection) as three_day_queries:
            response = self.client.get(
                f"/api/v1/coverage/?from={yesterday.isoformat()}&to={tomorrow.isoformat()}",
            )
        self.assertEqual(one_day_response.status_code, 200, one_day_response.content)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(three_day_queries), len(one_day_queries))
        headcount_by_date = {row["date"]: row["headcount"] for row in response.json()}

        self.assertEqual(headcount_by_date[yesterday.isoformat()], 3)
        self.assertEqual(headcount_by_date[today.isoformat()], 4)
        self.assertEqual(headcount_by_date[tomorrow.isoformat()], 3)
        self.assertEqual(EmployeeProfile.objects.filter(pk__in=[
            starts_today.pk, starts_tomorrow.pk, future_end.pk, past_end.pk,
        ]).count(), 4)

    def test_coverage_uses_audited_status_as_of_each_historical_day(self):
        today = organization_localdate()
        yesterday = today - dt.timedelta(days=1)
        suspend_employee(
            employee_id=self.employee.pk,
            actor=self.hr_user,
            reason="Test suspension",
        )

        response = self.client.get(
            f"/api/v1/coverage/?from={yesterday.isoformat()}&to={today.isoformat()}",
        )

        self.assertEqual(response.status_code, 200, response.content)
        headcount_by_date = {row["date"]: row["headcount"] for row in response.json()}
        self.assertEqual(headcount_by_date[yesterday.isoformat()], 2)
        self.assertEqual(headcount_by_date[today.isoformat()], 1)

    def test_historical_report_population_excludes_future_and_former_employees(self):
        SystemSetting.objects.create(key="privacy.min_group_size", value=2)
        today = organization_localdate()
        former_user = get_user_model().objects.create_user(username="report-former")
        former = EmployeeProfile.objects.create(
            user=former_user, company=self.company, department=self.department,
            personnel_code="REPORT-FORMER", start_date=today - dt.timedelta(days=10),
            end_date=today - dt.timedelta(days=1),
        )
        for employee in (self.employee, former):
            LeaveRequest.objects.create(
                employee=employee, leave_type=self.leave_type,
                start_date=today - dt.timedelta(days=5),
                end_date=today - dt.timedelta(days=5),
                working_days=Decimal("1"), status="APPROVED",
            )

        response = self.client.get("/api/v1/reports/absences")
        bradford = self.client.get("/api/v1/reports/bradford")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["series"], [])
        self.assertEqual(bradford.status_code, 409)
        self.assertEqual(bradford.json()["status"], "configuration_error")
        self.assertEqual(
            bradford.json()["emptyStateReason"],
            "bradford_policy_not_configured",
        )
        self.assertEqual(LeaveRequest.objects.filter(employee__in=(self.employee, former)).count(), 2)

    def test_confidential_balances_require_hr_scope(self):
        create_entitlement(
            employee=self.employee, leave_type=self.leave_type, year=1405,
            base_days=Decimal("20"), actor=self.hr_user, reason="opening",
            idempotency_key="privacy-annual",
        )
        create_entitlement(
            employee=self.employee, leave_type=self.confidential_type, year=1405,
            base_days=Decimal("4"), actor=self.hr_user, reason="opening",
            idempotency_key="privacy-confidential",
        )

        self.client.force_authenticate(self.employee_user)
        own = self.client.get(f"/api/v1/balances/?year=1405&employee={self.employee.pk}")
        self.assertEqual(own.status_code, 200)
        self.assertEqual([row["leaveTypeKey"] for row in own.json()], ["annual"])
        self.assertNotIn("محرمانه", own.content.decode())

        manager_user = get_user_model().objects.create_user(username="privacy-manager")
        manager = EmployeeProfile.objects.create(
            user=manager_user, company=self.company, department=self.department,
            personnel_code="M-1", start_date=dt.date(2020, 1, 1),
        )
        self.employee.manager = manager
        self.employee.save(update_fields=["manager"])
        self.client.force_authenticate(manager_user)
        team = self.client.get(f"/api/v1/balances/?year=1405&employee={self.employee.pk}")
        self.assertEqual(team.status_code, 200)
        self.assertEqual([row["leaveTypeKey"] for row in team.json()], ["annual"])

        LeaveRequest.objects.create(
            employee=self.employee, manager_snapshot=manager, leave_type=self.confidential_type,
            start_date=dt.date(2026, 11, 2), end_date=dt.date(2026, 11, 2),
            working_days=Decimal("1"), status="APPROVED",
        )
        LeaveRequest.objects.create(
            employee=self.employee, manager_snapshot=manager, leave_type=self.leave_type,
            start_date=dt.date(2026, 11, 3), end_date=dt.date(2026, 11, 3),
            working_days=Decimal("1"), status="APPROVED",
        )
        coverage = self.client.get(
            "/api/v1/coverage/?from=2026-11-02&to=2026-11-03",
        )
        self.assertEqual(coverage.status_code, 200)
        coverage_by_day = {row["date"]: row["awayCount"] for row in coverage.json()}
        self.assertEqual(coverage_by_day["2026-11-02"], 0)
        self.assertEqual(coverage_by_day["2026-11-03"], 1)

        self.client.force_authenticate(self.hr_user)
        scoped_hr = self.client.get(f"/api/v1/balances/?year=1405&employee={self.employee.pk}")
        self.assertEqual(scoped_hr.status_code, 200)
        self.assertEqual(
            {row["leaveTypeKey"] for row in scoped_hr.json()}, {"annual", "medical"},
        )
        hr_coverage = self.client.get("/api/v1/coverage/?from=2026-11-02&to=2026-11-02")
        self.assertEqual(hr_coverage.status_code, 200)
        self.assertEqual(hr_coverage.json()[0]["awayCount"], 1)

        other_company = Company.objects.create(code="PRIVACY-2", name="Other company")
        other_employee_user = get_user_model().objects.create_user(username="privacy-other")
        other_employee = EmployeeProfile.objects.create(
            user=other_employee_user, company=other_company, personnel_code="E-2",
            start_date=dt.date(2020, 1, 1),
        )
        other_hr_user = get_user_model().objects.create_user(username="privacy-other-hr")
        RoleAssignment.objects.create(
            user=other_hr_user, role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY, company=other_company,
        )
        self.client.force_authenticate(other_hr_user)
        out_of_scope = self.client.get(
            f"/api/v1/balances/?year=1405&employee={self.employee.pk}",
        )
        self.assertEqual(out_of_scope.status_code, 404)

        admin = get_user_model().objects.create_superuser(
            username="privacy-admin", password="unused",
        )
        self.client.force_authenticate(admin)
        admin_response = self.client.get(
            f"/api/v1/balances/?year=1405&employee={other_employee.pk}",
        )
        self.assertEqual(admin_response.status_code, 404)

    def test_entitlements_hide_confidential_data_outside_hr_scope(self):
        create_entitlement(
            employee=self.employee, leave_type=self.confidential_type, year=1405,
            base_days=Decimal("4"), actor=self.hr_user, reason="opening",
            idempotency_key="privacy-entitlement-in-scope",
        )
        self.client.force_authenticate(self.employee_user)
        self.assertEqual(self.client.get("/api/v1/entitlements/?year=1405").status_code, 403)

        self.client.force_authenticate(self.hr_user)
        in_scope = self.client.get("/api/v1/entitlements/?year=1405")
        self.assertEqual(in_scope.status_code, 200)
        self.assertEqual([row["leaveTypeKey"] for row in in_scope.json()], ["medical"])

        other_company = Company.objects.create(code="ENT-OTHER", name="Other company")
        other_employee_user = get_user_model().objects.create_user(username="entitlement-other")
        other_employee = EmployeeProfile.objects.create(
            user=other_employee_user, company=other_company, personnel_code="E-3",
            start_date=dt.date(2020, 1, 1),
        )
        create_entitlement(
            employee=other_employee, leave_type=self.confidential_type, year=1405,
            base_days=Decimal("8"), actor=self.hr_user, reason="opening",
            idempotency_key="privacy-entitlement-out-of-scope",
        )
        other_hr_user = get_user_model().objects.create_user(username="entitlement-other-hr")
        RoleAssignment.objects.create(
            user=other_hr_user, role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY, company=other_company,
        )
        mixed_user = get_user_model().objects.create_user(username="entitlement-mixed")
        mixed_employee = EmployeeProfile.objects.create(
            user=mixed_user, company=self.company, department=self.department,
            personnel_code="MIXED-1", start_date=dt.date(2020, 1, 1),
        )
        RoleAssignment.objects.create(
            user=mixed_user, role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY, company=other_company,
        )
        create_entitlement(
            employee=mixed_employee, leave_type=self.leave_type, year=1405,
            base_days=Decimal("10"), actor=self.hr_user, reason="opening",
            idempotency_key="privacy-entitlement-mixed-scope",
        )
        self.client.force_authenticate(other_hr_user)
        out_of_scope = self.client.get("/api/v1/entitlements/?year=1405")
        self.assertEqual(out_of_scope.status_code, 200)
        self.assertEqual(
            {row["employeeId"] for row in out_of_scope.json()}, {other_employee.pk},
        )
        self.client.force_authenticate(mixed_user)
        mixed_scope = self.client.get("/api/v1/entitlements/?year=1405")
        self.assertEqual(mixed_scope.status_code, 200)
        self.assertEqual(
            {row["employeeId"] for row in mixed_scope.json()},
            {other_employee.pk, mixed_employee.pk},
        )

        admin = get_user_model().objects.create_superuser(
            username="entitlement-admin", password="unused",
        )
        self.client.force_authenticate(admin)
        self.assertEqual(self.client.get("/api/v1/entitlements/?year=1405").status_code, 403)

    def test_executive_reports_exclude_confidential_records_and_balances(self):
        SystemSetting.objects.create(key="privacy.min_group_size", value=2)
        second_user = get_user_model().objects.create_user(username="report-second")
        second_employee = EmployeeProfile.objects.create(
            user=second_user, company=self.company, department=self.department,
            personnel_code="E-2", start_date=dt.date(2020, 1, 1),
        )
        for employee, key in ((self.employee, "report-confidential-1"),
                              (second_employee, "report-confidential-2")):
            LeaveRequest.objects.create(
                employee=employee, leave_type=self.confidential_type,
                start_date=dt.date(2026, 11, 2), end_date=dt.date(2026, 11, 2),
                working_days=Decimal("1"), status="APPROVED",
            )
            create_entitlement(
                employee=employee, leave_type=self.confidential_type, year=1405,
                base_days=Decimal("4"), actor=self.hr_user, reason="opening",
                idempotency_key=key,
            )
        exec_user = get_user_model().objects.create_user(username="privacy-exec")
        RoleAssignment.objects.create(
            user=exec_user, role=RoleAssignment.ROLE_EXEC,
            scope=RoleAssignment.SCOPE_COMPANY, company=self.company,
        )
        self.client.force_authenticate(exec_user)

        for kind in ("absences", "trends", "process-health", "balances"):
            with self.subTest(report=kind):
                response = self.client.get(f"/api/v1/reports/{kind}")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("medical", json.dumps(response.json()))
                self.assertNotIn("محرمانه", response.content.decode())
                self.assertEqual(response.json()["series"], [])

        self.client.force_authenticate(self.hr_user)
        hr_absences = self.client.get("/api/v1/reports/absences")
        self.assertEqual(hr_absences.status_code, 200)
        self.assertEqual(hr_absences.json()["series"][0]["label"], "محرمانه‌پزشکی")

    def test_notifications_are_persistently_marked_read_and_notes_are_saved(self):
        request = LeaveRequest.objects.create(
            employee=self.employee, leave_type=self.leave_type,
            start_date=dt.date(2026, 11, 2), end_date=dt.date(2026, 11, 2),
            working_days=Decimal("1"), status="PENDING",
        )
        event = LeaveRequestEvent.objects.create(
            request=request, actor=self.employee_user, origin="employee",
            event="submit", to_status="PENDING",
        )
        notifications = self.client.get("/api/v1/notifications/")
        self.assertEqual(notifications.status_code, 200)
        self.assertEqual(notifications.json()[0]["id"], event.pk)
        self.assertFalse(notifications.json()[0]["read"])
        self.assertEqual(
            self.client.post(f"/api/v1/notifications/{event.pk}/read").status_code, 204,
        )
        self.assertTrue(self.client.get("/api/v1/notifications/").json()[0]["read"])

        saved = self.client.post(
            "/api/v1/hr/bradford-notes/",
            {"employeeId": self.employee.pk, "body": "Reviewed with employee"},
            format="json",
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(self.client.get("/api/v1/hr/bradford-notes/").json()[0]["body"],
                         "Reviewed with employee")

    def test_confidential_notification_is_hidden_from_manager_but_visible_to_employee_and_hr(self):
        manager_user = get_user_model().objects.create_user(username="confidential-manager")
        manager = EmployeeProfile.objects.create(
            user=manager_user, company=self.company, department=self.department,
            personnel_code="M-CONF", start_date=dt.date(2020, 1, 1),
        )
        self.employee.manager = manager
        self.employee.save(update_fields=["manager"])
        other_hr = get_user_model().objects.create_user(username="confidential-hr")
        RoleAssignment.objects.create(
            user=other_hr, role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY, company=self.company,
        )
        request = LeaveRequest.objects.create(
            employee=self.employee, manager_snapshot=manager,
            leave_type=self.confidential_type,
            start_date=dt.date(2026, 11, 2), end_date=dt.date(2026, 11, 2),
            working_days=Decimal("1"), status="PENDING",
        )
        event = LeaveRequestEvent.objects.create(
            request=request, actor=self.hr_user, origin="hr",
            event="submit", to_status="PENDING",
        )

        self.client.force_authenticate(manager_user)
        self.assertEqual(self.client.get("/api/v1/notifications/").json(), [])
        self.client.force_authenticate(self.employee_user)
        self.assertEqual(
            self.client.get("/api/v1/notifications/").json()[0]["id"], event.pk,
        )
        self.client.force_authenticate(other_hr)
        self.assertEqual(
            self.client.get("/api/v1/notifications/").json()[0]["id"], event.pk,
        )

    def test_company_scope_rejects_foreign_hr_targets_without_side_effects(self):
        other_company = Company.objects.create(code="SCOPE-B", name="Scope company B")
        other_user = get_user_model().objects.create_user(username="scope-company-b")
        other_employee = EmployeeProfile.objects.create(
            user=other_user, company=other_company, personnel_code="SCOPE-B-1",
            start_date=dt.date(2020, 1, 1),
        )
        entitlement = create_entitlement(
            employee=other_employee, leave_type=self.leave_type, year=1405,
            base_days=Decimal("12"), actor=self.hr_user, reason="opening",
            idempotency_key="scope-company-opening",
        )
        BradfordNote.objects.create(
            employee=other_employee, body="Private company B note", updated_by=self.hr_user,
        )
        event_count = EntitlementEvent.objects.count()
        audit_count = AuditEvent.objects.count()

        adjustment = self.client.post(
            f"/api/v1/entitlements/{entitlement.pk}/adjust",
            {"days": "2", "reason": "out of scope"}, format="json",
        )
        self.assertEqual(adjustment.status_code, 404)
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.manual_adjustment, Decimal("0.0"))
        self.assertEqual(EntitlementEvent.objects.count(), event_count)
        self.assertEqual(AuditEvent.objects.count(), audit_count)

        valid_csv = "personnel_code,type,year,days\nE-1,annual,1406,5\n"
        preview = self.client.post(
            "/api/v1/entitlements/import/?dry_run=1", {"csv": valid_csv}, format="json",
        )
        self.assertEqual(preview.json()["accepted"], 1)
        crafted_csv = "personnel_code,type,year,days\nSCOPE-B-1,annual,1405,8\n"
        commit = self.client.post(
            "/api/v1/entitlements/import/", {"csv": crafted_csv}, format="json",
        )
        self.assertEqual(commit.status_code, 200)
        self.assertEqual((commit.json()["accepted"], commit.json()["rejected"]), (0, 1))
        self.assertFalse(LeaveEntitlement.objects.filter(
            employee=other_employee, leave_type=self.leave_type, year=1406,
        ).exists())
        self.assertEqual(EntitlementEvent.objects.count(), event_count)
        self.assertEqual(AuditEvent.objects.count(), audit_count)

        notes = self.client.get("/api/v1/hr/bradford-notes/")
        self.assertEqual(notes.status_code, 200)
        self.assertEqual(notes.json(), [])
        write_note = self.client.post(
            "/api/v1/hr/bradford-notes/",
            {"employeeId": other_employee.pk, "body": "ID substitution"}, format="json",
        )
        self.assertEqual(write_note.status_code, 404)
        self.assertEqual(BradfordNote.objects.get(employee=other_employee).body,
                         "Private company B note")

    def test_department_scope_unions_self_and_direct_reports_for_hr_operations(self):
        department_a = Department.objects.create(
            company=self.company, code="SCOPE-A", name="Scope department A",
        )
        department_b = Department.objects.create(
            company=self.company, code="SCOPE-B", name="Scope department B",
        )
        self.hr.department = department_b
        self.hr.save(update_fields=["department"])
        assignment = RoleAssignment.objects.get(user=self.hr_user, role=RoleAssignment.ROLE_HR)
        assignment.scope = RoleAssignment.SCOPE_DEPARTMENT
        assignment.department = department_a
        assignment.save()
        in_scope_user = get_user_model().objects.create_user(username="scope-dept-a")
        in_scope_employee = EmployeeProfile.objects.create(
            user=in_scope_user, company=self.company, department=department_a,
            personnel_code="SCOPE-A-1", start_date=dt.date(2020, 1, 1),
        )
        report_user = get_user_model().objects.create_user(username="scope-direct-report")
        report = EmployeeProfile.objects.create(
            user=report_user, company=self.company, department=department_b,
            manager=self.hr, personnel_code="SCOPE-B-2", start_date=dt.date(2020, 1, 1),
        )
        self.assertEqual(self.client.get("/api/v1/employees/").status_code, 200)
        self.assertEqual(
            {row["id"] for row in self.client.get("/api/v1/employees/").json()},
            {self.hr.pk, in_scope_employee.pk, report.pk},
        )
        self.assertEqual(
            authorization_context(self.hr_user).has_role("manager"), True,
        )
        self.assertEqual(
            authorization_context(self.hr_user).has_role("hr"), True,
        )
        self.assertEqual(
            authorization_context(self.hr_user).employee_id, self.hr.pk,
        )

        self_entitlement = create_entitlement(
            employee=self.hr, leave_type=self.leave_type, year=1405,
            base_days=Decimal("10"), actor=self.hr_user, reason="opening",
            idempotency_key="scope-self-opening",
        )
        report_entitlement = create_entitlement(
            employee=report, leave_type=self.leave_type, year=1405,
            base_days=Decimal("10"), actor=self.hr_user, reason="opening",
            idempotency_key="scope-report-opening",
        )
        BradfordNote.objects.create(employee=self.hr, body="self", updated_by=self.hr_user)
        BradfordNote.objects.create(employee=report, body="report", updated_by=self.hr_user)
        event_count = EntitlementEvent.objects.count()
        audit_count = AuditEvent.objects.count()
        note_count = BradfordNote.objects.count()

        for entitlement in (self_entitlement, report_entitlement):
            with self.subTest(employee=entitlement.employee_id):
                response = self.client.post(
                    f"/api/v1/entitlements/{entitlement.pk}/adjust",
                    {"days": "1", "reason": "outside department"}, format="json",
                )
                self.assertEqual(response.status_code, 200)
        preview = self.client.post(
            "/api/v1/entitlements/import/?dry_run=1",
            {"csv": "personnel_code,type,year,days\nSCOPE-B-2,annual,1406,5\n"},
            format="json",
        )
        self.assertEqual((preview.json()["accepted"], preview.json()["rejected"]), (1, 0))
        commit = self.client.post(
            "/api/v1/entitlements/import/",
            {"csv": "personnel_code,type,year,days\nSCOPE-B-2,annual,1406,5\n"},
            format="json",
        )
        self.assertEqual((commit.json()["accepted"], commit.json()["rejected"]), (1, 0))
        self.assertEqual(
            {item["employeeId"] for item in self.client.get("/api/v1/hr/bradford-notes/").json()},
            {self.hr.pk, report.pk},
        )
        for employee in (self.hr, report):
            write = self.client.post(
                "/api/v1/hr/bradford-notes/",
                {"employeeId": employee.pk, "body": "outside department"}, format="json",
            )
            self.assertEqual(write.status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/v1/employees/{report.pk}/contract-periods/").status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/v1/coverage/?from=2026-11-02&to=2026-11-02").json(),
            [
                {"date": "2026-11-02", "departmentName": department_a.name,
                 "awayCount": 0, "headcount": 1},
                {"date": "2026-11-02", "departmentName": department_b.name,
                 "awayCount": 0, "headcount": 2},
            ],
        )
        self.assertGreater(EntitlementEvent.objects.count(), event_count)
        self.assertGreater(AuditEvent.objects.count(), audit_count)
        self.assertEqual(BradfordNote.objects.count(), note_count)
        self_entitlement.refresh_from_db()
        report_entitlement.refresh_from_db()
        self.assertEqual(self_entitlement.manual_adjustment, Decimal("1.0"))
        self.assertEqual(report_entitlement.manual_adjustment, Decimal("1.0"))
        self.assertTrue(LeaveEntitlement.objects.filter(
            employee=report, leave_type=self.leave_type, year=1406,
        ).exists())

    def test_hr_scope_and_explicit_admin_global_access_remain_authorized(self):
        in_scope_entitlement = create_entitlement(
            employee=self.employee, leave_type=self.leave_type, year=1405,
            base_days=Decimal("10"), actor=self.hr_user, reason="opening",
            idempotency_key="scope-allowed-opening",
        )
        adjustment = self.client.post(
            f"/api/v1/entitlements/{in_scope_entitlement.pk}/adjust",
            {"days": "2", "reason": "in scope"}, format="json",
        )
        self.assertEqual(adjustment.status_code, 200, adjustment.content)
        allowed_import = self.client.post(
            "/api/v1/entitlements/import/",
            {"csv": "personnel_code,type,year,days\nE-1,annual,1406,5\n"},
            format="json",
        )
        self.assertEqual(allowed_import.json()["accepted"], 1)
        allowed_note = self.client.post(
            "/api/v1/hr/bradford-notes/",
            {"employeeId": self.employee.pk, "body": "in scope"}, format="json",
        )
        self.assertEqual(allowed_note.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/hr/bradford-notes/").json()[0]["body"],
                         "in scope")

        other_company = Company.objects.create(code="ADMIN-GLOBAL", name="Admin global")
        other_user = get_user_model().objects.create_user(username="admin-global-target")
        other_employee = EmployeeProfile.objects.create(
            user=other_user, company=other_company, personnel_code="ADMIN-GLOBAL-1",
            start_date=dt.date(2020, 1, 1),
        )
        other_entitlement = create_entitlement(
            employee=other_employee, leave_type=self.leave_type, year=1405,
            base_days=Decimal("10"), actor=self.hr_user, reason="opening",
            idempotency_key="admin-global-opening",
        )
        admin = get_user_model().objects.create_superuser(
            username="scope-global-admin", password="unused",
        )
        self.client.force_authenticate(admin)
        admin_adjustment = self.client.post(
            f"/api/v1/entitlements/{other_entitlement.pk}/adjust",
            {"days": "1", "reason": "admin global"}, format="json",
        )
        self.assertEqual(admin_adjustment.status_code, 200, admin_adjustment.content)
        admin_import = self.client.post(
            "/api/v1/entitlements/import/",
            {"csv": "personnel_code,type,year,days\nADMIN-GLOBAL-1,annual,1406,5\n"},
            format="json",
        )
        self.assertEqual(admin_import.json()["accepted"], 1)
        admin_note = self.client.post(
            "/api/v1/hr/bradford-notes/",
            {"employeeId": other_employee.pk, "body": "admin global"}, format="json",
        )
        self.assertEqual(admin_note.status_code, 200)
        self.assertEqual(BradfordNote.objects.get(employee=other_employee).body, "admin global")

    def test_request_attachments_are_validated_and_downloaded_through_authorized_route(self):
        request = LeaveRequest.objects.create(
            employee=self.employee, leave_type=self.leave_type,
            start_date=dt.date(2026, 11, 2), end_date=dt.date(2026, 11, 2),
            working_days=Decimal("1"), status="PENDING",
        )
        with tempfile.TemporaryDirectory() as attachment_root:
            with override_settings(
                ZSH_ATTACHMENT_ROOT=attachment_root,
                ZSH_ATTACHMENT_SCANNER="",
                ZSH_ATTACHMENT_SCANNER_COMMAND=[
                    sys.executable,
                    "-c",
                    "print('{\"status\":\"clean\"}')",
                ],
            ):
                upload = SimpleUploadedFile(
                    "evidence.pdf", valid_pdf(),
                    content_type="application/pdf",
                )
                response = self.client.post(
                    f"/api/v1/leave-requests/{request.pk}/attachments/",
                    {"file": upload}, format="multipart",
                )
                self.assertEqual(response.status_code, 201, response.content)
                listing = self.client.get(f"/api/v1/leave-requests/{request.pk}/attachments/")
                self.assertEqual(listing.status_code, 200)
                download = self.client.get(listing.json()[0]["downloadUrl"])
                self.assertEqual(download.status_code, 200)
                self.assertEqual(b"".join(download.streaming_content), valid_pdf())

                invalid = SimpleUploadedFile(
                    "bad.pdf", b"not a pdf", content_type="application/pdf",
                )
                rejected = self.client.post(
                    f"/api/v1/leave-requests/{request.pk}/attachments/",
                    {"file": invalid}, format="multipart",
                )
                self.assertEqual(rejected.status_code, 400)

    def test_settings_and_health_routes_match_production_client_paths(self):
        admin = get_user_model().objects.create_superuser(
            username="frontend-admin", email="admin@example.test", password="test-password",
        )
        client = APIClient()
        client.force_authenticate(admin)
        settings_response = client.get("/api/v1/admin/settings/")
        self.assertEqual(settings_response.status_code, 200)
        self.assertIn("bradford.threshold_review", {row["key"] for row in settings_response.json()})
        ready_response = client.get("/api/v1/ready/")
        self.assertEqual(ready_response.status_code, 200, ready_response.content)
        jobs_response = client.get("/api/v1/admin/jobs/")
        self.assertEqual(jobs_response.status_code, 200)
        self.assertEqual(
            jobs_response.json()["scheduler"]["status"],
            "scheduler_not_configured",
        )
        self.assertEqual(
            {job["name"] for job in jobs_response.json()["jobs"]},
            {
                "overdue_escalation", "manager_reminders",
                "carry_forward", "entitlement_expiration", "notification_delivery",
            },
        )
