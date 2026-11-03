import datetime as dt
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from leave.entitlement_services import create_entitlement
from leave.services import apply_event
from leave.models import (
    AuditEvent,
    BradfordNote,
    Company,
    ContractPeriod,
    Department,
    EmployeeInvitation,
    EmployeeProfile,
    EntitlementEvent,
    LeaveRequest,
    LeaveType,
    OrganizationSite,
    RoleAssignment,
)


class AuditCoverageTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(username="audit-admin")
        RoleAssignment.objects.create(
            user=self.admin,
            role=RoleAssignment.ROLE_ADMIN,
            scope=RoleAssignment.SCOPE_GLOBAL,
        )
        self.hr = user_model.objects.create_user(username="audit-hr")
        self.other_hr = user_model.objects.create_user(username="audit-other-hr")
        self.company = Company.objects.create(code="AUDIT-A", name="Audit company A")
        self.other_company = Company.objects.create(code="AUDIT-B", name="Audit company B")
        self.department = Department.objects.create(
            company=self.company, code="HR", name="Human resources",
        )
        self.site = OrganizationSite.objects.create(
            company=self.company, code="MAIN", name="Main site",
        )
        self.employee_user = user_model.objects.create_user(username="audit-employee")
        self.employee = EmployeeProfile.objects.create(
            user=self.employee_user,
            company=self.company,
            site=self.site,
            department=self.department,
            personnel_code="AUDIT-1",
            start_date=dt.date(2020, 1, 1),
        )
        RoleAssignment.objects.create(
            user=self.hr,
            role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY,
            company=self.company,
        )
        RoleAssignment.objects.create(
            user=self.other_hr,
            role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY,
            company=self.other_company,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.hr_client = APIClient()
        self.hr_client.force_authenticate(self.hr)

    def test_employee_create_and_update_are_audited_without_personal_values(self):
        created = self.hr_client.post(
            "/api/v1/employees/",
            {
                "displayName": "Sensitive Employee Name",
                "personnelCode": "AUDIT-NEW",
                "companyId": self.company.pk,
                "startDate": "2026-01-01",
                "email": "private@example.test",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        employee_id = created.json()["id"]
        create_event = AuditEvent.objects.get(
            entity="EmployeeProfile", entity_id=str(employee_id), action="employee.create",
        )
        self.assertEqual(create_event.actor_id, self.hr.pk)
        self.assertNotIn("Sensitive Employee Name", str(create_event.after))
        self.assertNotIn("private@example.test", str(create_event.after))
        invitation = EmployeeInvitation.objects.get(employee_id=employee_id)
        invitation_event = AuditEvent.objects.get(
            entity="EmployeeInvitation", entity_id=str(invitation.pk),
            action="invitation.created",
        )
        self.assertEqual(invitation_event.after["status"], "pending")
        self.assertNotIn("private@example.test", str(invitation_event.after))
        self.assertNotIn("token_hash", str(invitation_event.after))

        updated = self.hr_client.patch(
            f"/api/v1/employees/{employee_id}/",
            {
                "displayName": "Updated Sensitive Name",
                "personnelCode": "AUDIT-NEW",
                "companyId": self.company.pk,
                "startDate": "2026-01-01",
            },
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        update_event = AuditEvent.objects.get(
            entity="EmployeeProfile", entity_id=str(employee_id), action="employee.update",
        )
        self.assertTrue(update_event.before)
        self.assertTrue(update_event.after["displayNameChanged"])

    def test_company_department_and_site_updates_are_audited(self):
        for path, obj, data, entity, action in (
            (
                f"/api/v1/companies/{self.company.pk}/",
                self.company, {"code": "AUDIT-A", "name": "Renamed company"},
                "Company", "company.update",
            ),
            (
                f"/api/v1/departments/{self.department.pk}/",
                self.department, {
                    "companyId": self.company.pk, "code": "HR", "name": "People",
                },
                "Department", "department.update",
            ),
            (
                f"/api/v1/sites/{self.site.pk}/",
                self.site, {
                    "companyId": self.company.pk, "code": "MAIN", "name": "North site",
                },
                "OrganizationSite", "organization_site.update",
            ),
        ):
            with self.subTest(entity=entity):
                response = self.client.patch(
                    path, data, format="json",
                    **({"HTTP_X_CORRELATION_ID": "audit-site-change-17"} if entity == "OrganizationSite" else {}),
                )
                self.assertEqual(response.status_code, 200, response.content)
                event = AuditEvent.objects.get(
                    entity=entity, entity_id=str(obj.pk), action=action,
                )
                self.assertEqual(event.actor_id, self.admin.pk)
                self.assertTrue(event.before)
                self.assertTrue(event.after)
                if entity == "OrganizationSite":
                    self.assertEqual(event.correlation_id, "audit-site-change-17")
                    self.assertEqual(
                        response["X-Correlation-ID"], "audit-site-change-17",
                    )
                    self.assertEqual(event.source, f"PATCH {path}")

    def test_contract_and_bradford_mutations_are_audited_without_note_text(self):
        contract = self.hr_client.post(
            f"/api/v1/employees/{self.employee.pk}/contract-periods/",
            {
                "employeeId": self.employee.pk, "start": "2026-01-01", "end": None,
                "workDays": [0, 1, 2, 3, 4], "contractPercent": 100,
            },
            format="json",
        )
        self.assertEqual(contract.status_code, 201, contract.content)
        contract_row = ContractPeriod.objects.get(pk=contract.json()["id"])
        contract_event = AuditEvent.objects.get(
            entity="ContractPeriod", entity_id=str(contract_row.pk),
            action="contract_period.create",
        )
        self.assertEqual(contract_event.actor_id, self.hr.pk)
        self.assertEqual(contract_event.after["employeeId"], self.employee.pk)

        first_body = "confidential medical detail that must not be copied"
        note_response = self.hr_client.post(
            "/api/v1/hr/bradford-notes/",
            {"employeeId": self.employee.pk, "body": first_body},
            format="json",
        )
        self.assertEqual(note_response.status_code, 200, note_response.content)
        note = BradfordNote.objects.get(employee=self.employee)
        create_event = AuditEvent.objects.get(
            entity="BradfordNote", entity_id=str(note.pk), action="bradford_note.create",
        )
        self.assertTrue(create_event.after["contentChanged"])
        self.assertNotIn(first_body, str(create_event.before) + str(create_event.after))

        second_body = "replacement private note"
        updated = self.hr_client.post(
            "/api/v1/hr/bradford-notes/",
            {"employeeId": self.employee.pk, "body": second_body},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        update_event = AuditEvent.objects.get(
            entity="BradfordNote", entity_id=str(note.pk), action="bradford_note.update",
        )
        self.assertTrue(update_event.before)
        self.assertTrue(update_event.after["contentChanged"])
        self.assertNotIn(second_body, str(update_event.before) + str(update_event.after))

    def test_leave_type_create_update_and_deactivate_are_audited(self):
        created = self.client.post(
            "/api/v1/leave-types/",
            {"key": "audit", "labelFa": "Audit", "reason": "catalog change"},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        leave_type_id = created.json()["id"]
        updated = self.client.patch(
            f"/api/v1/leave-types/{leave_type_id}/",
            {"requiresApproval": False, "reason": "approval policy change"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        deactivated = self.client.patch(
            f"/api/v1/leave-types/{leave_type_id}/",
            {"enabled": False, "reason": "retired type"},
            format="json",
        )
        self.assertEqual(deactivated.status_code, 200, deactivated.content)
        self.assertEqual(
            AuditEvent.objects.filter(entity="LeaveType", entity_id=str(leave_type_id)).count(),
            3,
        )
        last_event = AuditEvent.objects.get(
            entity="LeaveType", entity_id=str(leave_type_id), action="leave_type.deactivate",
            after__enabled=False,
        )
        self.assertFalse(last_event.after["enabled"])

    def test_role_assignment_grant_and_revoke_are_audited(self):
        target = get_user_model().objects.create_user(username="audit-role-target")
        granted = self.client.post(
            "/api/v1/admin/role-assignments/",
            {
                "userId": target.pk, "role": "hr", "scope": "company",
                "companyId": self.company.pk,
            },
            format="json",
        )
        self.assertEqual(granted.status_code, 201, granted.content)
        assignment_id = granted.json()["id"]
        grant_event = AuditEvent.objects.get(
            entity="RoleAssignment", entity_id=str(assignment_id),
            action="role.assignment.granted",
        )
        self.assertEqual(grant_event.actor_id, self.admin.pk)

        revoked = self.client.delete(f"/api/v1/admin/role-assignments/{assignment_id}/")
        self.assertEqual(revoked.status_code, 204)
        revoke_event = AuditEvent.objects.get(
            entity="RoleAssignment", entity_id=str(assignment_id),
            action="role.assignment.revoked",
        )
        self.assertTrue(revoke_event.before["isActive"])
        self.assertFalse(revoke_event.after["isActive"])

    def test_entitlement_adjustment_and_bulk_import_have_per_record_audits(self):
        leave_type = LeaveType.objects.create(
            key="audit-entitlement", label_fa="Annual", counts_against_balance=True,
        )
        entitlement = create_entitlement(
            employee=self.employee, leave_type=leave_type, year=1405, base_days=Decimal("10"),
            actor=self.hr, reason="opening balance", idempotency_key="audit-opening",
        )
        adjusted = self.hr_client.post(
            f"/api/v1/entitlements/{entitlement.pk}/adjust",
            {"days": "1.5", "reason": "approved adjustment"},
            format="json",
        )
        self.assertEqual(adjusted.status_code, 200, adjusted.content)
        adjustment_audit = AuditEvent.objects.filter(
            entity="EntitlementEvent", action="entitlement.adjustment",
        ).latest("pk")
        self.assertEqual(adjustment_audit.actor_id, self.hr.pk)
        self.assertEqual(adjustment_audit.after["employeeId"], self.employee.pk)

        another = EmployeeProfile.objects.create(
            user=get_user_model().objects.create_user(username="audit-import-employee"),
            company=self.company,
            personnel_code="AUDIT-IMPORT",
            start_date=dt.date(2020, 1, 1),
        )
        imported = self.hr_client.post(
            "/api/v1/entitlements/import/",
            {"csv": f"personnel_code,type,year,days\n{another.personnel_code},{leave_type.key},1405,5"},
            format="json",
        )
        self.assertEqual(imported.status_code, 200, imported.content)
        self.assertEqual(imported.json()["accepted"], 1)
        imported_audit = AuditEvent.objects.filter(
            entity="EntitlementEvent", action="entitlement.opening",
            after__employeeId=another.pk,
        )
        self.assertEqual(imported_audit.count(), 1)
        self.assertTrue(EntitlementEvent.objects.filter(
            entitlement__employee=another, kind="opening",
        ).exists())

    def test_leave_request_workflow_is_audited_without_reason_text(self):
        leave_type = LeaveType.objects.create(
            key="audit-leave", label_fa="Annual", employee_requestable=True,
        )
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=leave_type,
            start_date=dt.date(2026, 11, 2),
            end_date=dt.date(2026, 11, 2),
            working_days=Decimal("1"),
            status="PENDING",
        )
        apply_event(
            request_id=leave_request.pk,
            user=self.hr,
            event="reject",
            reason="personal reason should remain in scoped request history",
        )
        event = AuditEvent.objects.get(
            entity="LeaveRequest", entity_id=str(leave_request.pk), action="leave.reject",
        )
        self.assertEqual(event.actor_id, self.hr.pk)
        self.assertEqual(event.before, {"status": "PENDING"})
        self.assertEqual(event.after["status"], "REJECTED")
        self.assertEqual(event.after["employee_id"], self.employee.pk)
        self.assertEqual(event.after["start_date"], "2026-11-02")
        self.assertEqual(event.reason, "")
        self.assertNotIn("personal reason", str(event.before) + str(event.after))
        scoped_history = self.hr_client.get("/api/v1/audit/events/")
        self.assertEqual(scoped_history.status_code, 200)
        event_summary = next(
            item for item in scoped_history.json()
            if item["entity"] == "LeaveRequest" and item["entityId"] == str(leave_request.pk)
        )
        self.assertIsNone(event_summary["before"])
        self.assertIsNone(event_summary["after"])
        self.assertEqual(event_summary["reason"], "")

    def test_audit_failure_rolls_back_business_mutation(self):
        with patch("leave.audit_services.AuditEvent.objects.create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.client.patch(
                    f"/api/v1/companies/{self.company.pk}/",
                    {"code": self.company.code, "name": "must roll back"},
                    format="json",
                )
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Audit company A")

    def test_actor_cannot_be_forged_and_scoped_hr_cannot_read_other_company_events(self):
        forged = self.client.patch(
            f"/api/v1/companies/{self.company.pk}/",
            {
                "code": self.company.code, "name": "forged",
                "actorId": self.hr.pk,
            },
            format="json",
        )
        self.assertEqual(forged.status_code, 400)
        self.assertFalse(AuditEvent.objects.filter(
            entity="Company", entity_id=str(self.company.pk),
        ).exists())

        self.client.patch(
            f"/api/v1/companies/{self.company.pk}/",
            {"code": self.company.code, "name": "audited"},
            format="json",
        )
        response = self.hr_client.get("/api/v1/audit/events/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any(
            item["entity"] == "Company" and item["entityId"] == str(self.company.pk)
            for item in response.json()
        ))
        other_client = APIClient()
        other_client.force_authenticate(self.other_hr)
        other_response = other_client.get("/api/v1/audit/events/")
        self.assertEqual(other_response.status_code, 200)
        self.assertEqual(other_response.json(), [])
        ordinary_client = APIClient()
        ordinary_client.force_authenticate(self.employee_user)
        self.assertEqual(ordinary_client.get("/api/v1/audit/events/").status_code, 403)

    def test_audit_payload_never_contains_passwords_tokens_or_note_body(self):
        BradfordNote.objects.create(
            employee=self.employee,
            body="sensitive note body",
            updated_by=self.hr,
        )
        created = self.hr_client.post(
            "/api/v1/employees/",
            {
                "displayName": "No audit name",
                "personnelCode": "AUDIT-SECRET",
                "companyId": self.company.pk,
                "email": "private2@example.test",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        rendered = " ".join(
            str(event.before) + str(event.after) + event.reason
            for event in AuditEvent.objects.all()
        )
        for forbidden in ("sensitive note body", "private2@example.test", "password", "token_hash"):
            self.assertNotIn(forbidden, rendered)
