import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from leave.calendar_services import create_policy_version, create_work_pattern
from leave.entitlement_services import create_entitlement, entitlement_balance
from leave.models import (
    AuditEvent,
    Company,
    EmployeeProfile,
    LeaveRequest,
    LeaveRequestComment,
    LeaveRequestEvent,
    LeaveType,
    RoleAssignment,
    WorkflowIdempotency,
)
from zsh_core import settings_schema, workflow as wf


class LeaveWorkflowApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.company = Company.objects.create(code="WORKFLOW", name="Workflow company")
        self.employee_user = user_model.objects.create_user(username="employee")
        self.manager_user = user_model.objects.create_user(username="manager")
        self.other_user = user_model.objects.create_user(username="other")
        self.manager = EmployeeProfile.objects.create(
            user=self.manager_user,
            company=self.company,
            personnel_code="M-1",
            start_date=dt.date(2020, 1, 1),
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.employee_user,
            company=self.company,
            personnel_code="E-1",
            manager=self.manager,
            start_date=dt.date(2020, 1, 1),
        )
        self.hr_user = user_model.objects.create_user(username="hr")
        RoleAssignment.objects.create(
            user=self.hr_user, role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY, company=self.company,
        )
        self.leave_type = LeaveType.objects.create(
            key="annual",
            label_fa="Annual leave",
            requires_approval=True,
        )
        values = {
            key: settings_schema.to_storable(value)
            for key, value in settings_schema.defaults().items()
            if key.startswith("policy.")
        }
        create_policy_version(
            actor=self.manager_user,
            scope="company",
            company=self.company,
            effective_from=dt.date(2026, 1, 1),
            values=values,
            reason="Workflow API test policy",
        )
        create_work_pattern(
            actor=self.manager_user,
            name="All days",
            company=self.company,
            weekdays=list(range(7)),
            start_time=dt.time(8, 0),
            end_time=dt.time(16, 0),
            break_minutes=0,
            effective_from=dt.date(2026, 1, 1),
        )
        self.employee_client = APIClient()
        self.employee_client.force_authenticate(self.employee_user)
        self.manager_client = APIClient()
        self.manager_client.force_authenticate(self.manager_user)
        self.other_client = APIClient()
        self.other_client.force_authenticate(self.other_user)
        self.hr_client = APIClient()
        self.hr_client.force_authenticate(self.hr_user)

    def submit(self, client, key, start, end=None):
        return client.post(
            "/api/v1/leave-requests/",
            {
                "leaveTypeKey": self.leave_type.key,
                "start": start,
                "end": end or start,
                "reason": "Annual leave",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def test_complete_leave_lifecycle_and_idempotent_replays(self):
        submitted = self.submit(self.employee_client, "submit-correct", "2026-11-02")
        self.assertEqual(submitted.status_code, 201, submitted.content)
        request_id = submitted.json()["id"]
        self.assertEqual(submitted.json()["status"], wf.PENDING)

        replay = self.submit(self.employee_client, "submit-correct", "2026-11-02")
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json()["id"], request_id)
        self.assertEqual(LeaveRequest.objects.count(), 1)

        conflicting_replay = self.employee_client.post(
            "/api/v1/leave-requests/",
            {
                "leaveTypeKey": self.leave_type.key,
                "start": "2026-11-03",
                "end": "2026-11-03",
                "reason": "Different payload",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="submit-correct",
        )
        self.assertEqual(conflicting_replay.status_code, 409)

        corrected = self.employee_client.patch(
            f"/api/v1/leave-requests/{request_id}/",
            {"start": "2026-11-03", "end": "2026-11-03", "reason": "Updated reason"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="correct-pending",
        )
        self.assertEqual(corrected.status_code, 200, corrected.content)
        self.assertEqual(corrected.json()["start"], "2026-11-03")
        correction_replay = self.employee_client.patch(
            f"/api/v1/leave-requests/{request_id}/",
            {"start": "2026-11-03", "end": "2026-11-03", "reason": "Updated reason"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="correct-pending",
        )
        self.assertEqual(correction_replay.status_code, 200)
        self.assertEqual(
            LeaveRequestEvent.objects.filter(request_id=request_id, event="correct").count(),
            1,
        )

        comment = self.employee_client.post(
            f"/api/v1/leave-requests/{request_id}/comments/",
            {"body": "Please review this request."},
            format="json",
            HTTP_IDEMPOTENCY_KEY="comment-1",
        )
        self.assertEqual(comment.status_code, 201, comment.content)
        self.assertEqual(
            self.manager_client.get(
                f"/api/v1/leave-requests/{request_id}/comments/",
            ).json()[0]["body"],
            "Please review this request.",
        )
        comment_replay = self.employee_client.post(
            f"/api/v1/leave-requests/{request_id}/comments/",
            {"body": "Please review this request."},
            format="json",
            HTTP_IDEMPOTENCY_KEY="comment-1",
        )
        self.assertEqual(comment_replay.status_code, 201)
        self.assertEqual(comment_replay.json()["id"], comment.json()["id"])

        approved = self.manager_client.post(
            f"/api/v1/leave-requests/{request_id}/approve",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="approve-1",
        )
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.json()["status"], wf.APPROVED)
        approval_replay = self.manager_client.post(
            f"/api/v1/leave-requests/{request_id}/approve",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="approve-1",
        )
        self.assertEqual(approval_replay.status_code, 200)
        self.assertEqual(LeaveRequestEvent.objects.filter(
            request_id=request_id, event="approve",
        ).count(), 1)

        withdrawal = self.employee_client.post(
            f"/api/v1/leave-requests/{request_id}/withdraw",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="withdraw-1",
        )
        self.assertEqual(withdrawal.status_code, 200, withdrawal.content)
        self.assertEqual(withdrawal.json()["status"], wf.WITHDRAWAL_PENDING)
        rejected_withdrawal = self.manager_client.post(
            f"/api/v1/leave-requests/{request_id}/reject_withdraw",
            {"reason": "Business coverage is needed."},
            format="json",
            HTTP_IDEMPOTENCY_KEY="reject-withdraw-1",
        )
        self.assertEqual(rejected_withdrawal.status_code, 200, rejected_withdrawal.content)
        self.assertEqual(rejected_withdrawal.json()["status"], wf.APPROVED)
        approved_withdrawal = self.employee_client.post(
            f"/api/v1/leave-requests/{request_id}/withdraw",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="withdraw-2",
        )
        self.assertEqual(approved_withdrawal.status_code, 200, approved_withdrawal.content)
        cancelled = self.manager_client.post(
            f"/api/v1/leave-requests/{request_id}/approve_withdraw",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="approve-withdraw-1",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.content)
        self.assertEqual(cancelled.json()["status"], wf.CANCELLED)

        amendment_source = self.submit(
            self.employee_client, "submit-amend-source", "2026-11-10",
        )
        amendment_source_id = amendment_source.json()["id"]
        self.assertEqual(amendment_source.status_code, 201, amendment_source.content)
        self.manager_client.post(
            f"/api/v1/leave-requests/{amendment_source_id}/approve",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="approve-amend-source",
        )
        amendment = self.employee_client.post(
            f"/api/v1/leave-requests/{amendment_source_id}/amend",
            {"start": "2026-11-11", "end": "2026-11-11"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="amend-1",
        )
        self.assertEqual(amendment.status_code, 201, amendment.content)
        amendment_id = amendment.json()["id"]
        self.assertEqual(amendment.json()["supersedesId"], amendment_source_id)
        resolved_amendment = self.manager_client.post(
            f"/api/v1/leave-requests/{amendment_id}/approve",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="approve-amendment",
        )
        self.assertEqual(resolved_amendment.status_code, 200, resolved_amendment.content)
        self.assertEqual(resolved_amendment.json()["status"], wf.APPROVED)
        self.assertEqual(
            LeaveRequest.objects.get(pk=amendment_source_id).status,
            wf.CANCELLED,
        )

        reject_source = self.submit(
            self.employee_client, "submit-reject", "2026-11-20",
        )
        reject_id = reject_source.json()["id"]
        rejected = self.manager_client.post(
            f"/api/v1/leave-requests/{reject_id}/reject",
            {"reason": "Insufficient coverage."},
            format="json",
            HTTP_IDEMPOTENCY_KEY="reject-1",
        )
        self.assertEqual(rejected.status_code, 200, rejected.content)
        self.assertEqual(rejected.json()["status"], wf.REJECTED)

        cancel_source = self.submit(
            self.employee_client, "submit-cancel", "2026-11-24",
        )
        cancel_id = cancel_source.json()["id"]
        cancelled_pending = self.employee_client.post(
            f"/api/v1/leave-requests/{cancel_id}/cancel",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="cancel-1",
        )
        self.assertEqual(cancelled_pending.status_code, 200, cancelled_pending.content)
        self.assertEqual(cancelled_pending.json()["status"], wf.CANCELLED)

        history = self.manager_client.get(
            f"/api/v1/leave-requests/{request_id}/events/",
        )
        self.assertEqual(history.status_code, 200)
        self.assertTrue({"submit", "correct", "comment", "approve", "request_withdraw",
                         "reject_withdraw", "approve_withdraw"}.issubset(
            {event["event"] for event in history.json()},
        ))
        self.assertTrue(AuditEvent.objects.filter(
            entity="LeaveRequest", entity_id=str(request_id), action="leave.correct",
        ).exists())
        correction_audit = AuditEvent.objects.get(
            entity="LeaveRequest", entity_id=str(request_id), action="leave.correct",
        )
        self.assertTrue(correction_audit.after["reason_changed"])
        self.assertEqual(LeaveRequestComment.objects.filter(request_id=request_id).count(), 1)

    def test_deactivated_leave_type_cannot_be_selected_for_new_request(self):
        self.leave_type.enabled = False
        self.leave_type.save(update_fields=["enabled"])

        response = self.submit(self.employee_client, "submit-disabled-type", "2026-11-04")

        self.assertIn(response.status_code, (400, 422), response.content)
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_hr_absence_is_directly_approved_and_idempotent(self):
        counted_leave_type = LeaveType.objects.create(
            key="counted-absence", label_fa="Counted absence", counts_against_balance=True,
        )
        entitlement = create_entitlement(
            employee=self.employee, leave_type=counted_leave_type, year=1405,
            base_days=Decimal("5"), actor=self.hr_user, reason="opening entitlement",
            idempotency_key="hr-absence-entitlement",
        )
        body = {
            "employeeId": self.employee.pk,
            "leaveTypeKey": counted_leave_type.key,
            "start": "2026-11-09",
            "end": "2026-11-09",
            "workingDays": "1",
            "reason": "HR-recorded absence",
        }

        missing_key = self.hr_client.post(
            "/api/v1/hr/absences/", body, format="json",
        )
        self.assertEqual(missing_key.status_code, 422)
        self.assertEqual(LeaveRequest.objects.count(), 0)

        created = self.hr_client.post(
            "/api/v1/hr/absences/", body, format="json",
            HTTP_IDEMPOTENCY_KEY="hr-absence-create",
        )
        self.assertEqual(created.status_code, 201, created.content)
        request_id = created.json()["id"]
        self.assertEqual(created.json()["status"], wf.APPROVED)

        request = LeaveRequest.objects.get(pk=request_id)
        events = list(request.events.order_by("pk"))
        self.assertEqual(len(events), 1)
        self.assertEqual(
            (events[0].event, events[0].origin, events[0].from_status, events[0].to_status),
            ("submit", "hr", "", wf.APPROVED),
        )
        self.assertEqual(
            list(AuditEvent.objects.filter(
                entity="LeaveRequest", entity_id=str(request_id),
            ).values_list("action", flat=True)),
            ["leave.submit"],
        )
        self.assertEqual(entitlement_balance(entitlement)["used"], Decimal("1.0"))

        replay = self.hr_client.post(
            "/api/v1/hr/absences/", body, format="json",
            HTTP_IDEMPOTENCY_KEY="hr-absence-create",
        )
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay.json()["id"], request_id)
        self.assertEqual(LeaveRequest.objects.count(), 1)
        self.assertEqual(LeaveRequestEvent.objects.filter(request=request).count(), 1)
        self.assertEqual(entitlement_balance(entitlement)["used"], Decimal("1.0"))

        changed_payload = {**body, "workingDays": "2"}
        conflict = self.hr_client.post(
            "/api/v1/hr/absences/", changed_payload, format="json",
            HTTP_IDEMPOTENCY_KEY="hr-absence-create",
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_hr_absence_rejects_unauthorized_and_rolls_back_invalid_submission(self):
        body = {
            "employeeId": self.employee.pk,
            "leaveTypeKey": self.leave_type.key,
            "start": "2026-11-09",
            "end": "2026-11-09",
            "workingDays": "2",
            "reason": "Invalid day count",
        }
        denied = self.other_client.post(
            "/api/v1/hr/absences/", body, format="json",
            HTTP_IDEMPOTENCY_KEY="hr-absence-denied",
        )
        self.assertEqual(denied.status_code, 403)

        other_company = Company.objects.create(code="HR-OTHER", name="Other company")
        other_hr_user = get_user_model().objects.create_user(username="other-hr")
        RoleAssignment.objects.create(
            user=other_hr_user, role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY, company=other_company,
        )
        other_hr_client = APIClient()
        other_hr_client.force_authenticate(other_hr_user)
        out_of_scope = other_hr_client.post(
            "/api/v1/hr/absences/", body, format="json",
            HTTP_IDEMPOTENCY_KEY="hr-absence-out-of-scope",
        )
        self.assertEqual(out_of_scope.status_code, 404)

        invalid = self.hr_client.post(
            "/api/v1/hr/absences/", body, format="json",
            HTTP_IDEMPOTENCY_KEY="hr-absence-invalid",
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.assertEqual(LeaveRequestEvent.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.filter(entity="LeaveRequest").count(), 0)
        self.assertEqual(WorkflowIdempotency.objects.count(), 0)

    def test_scope_and_transition_permissions_are_enforced(self):
        response = self.submit(self.employee_client, "submit-scope", "2026-11-02")
        request_id = response.json()["id"]

        self.assertEqual(
            self.other_client.get(f"/api/v1/leave-requests/{request_id}/").status_code,
            404,
        )
        self.assertEqual(
            self.other_client.post(
                f"/api/v1/leave-requests/{request_id}/approve",
                {},
                format="json",
                HTTP_IDEMPOTENCY_KEY="unauthorized-approve",
            ).status_code,
            404,
        )
        self.assertEqual(
            self.employee_client.post(
                f"/api/v1/leave-requests/{request_id}/approve",
                {},
                format="json",
                HTTP_IDEMPOTENCY_KEY="owner-approve",
            ).status_code,
            403,
        )

    def test_replay_key_is_actor_scoped_and_comments_are_audited(self):
        response = self.submit(self.employee_client, "submit-actor", "2026-11-02")
        request_id = response.json()["id"]
        self.employee_client.post(
            f"/api/v1/leave-requests/{request_id}/comments/",
            {"body": "Employee note"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="shared-comment-key",
        )

        manager_comment = self.manager_client.post(
            f"/api/v1/leave-requests/{request_id}/comments/",
            {"body": "Manager note"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="shared-comment-key",
        )
        self.assertEqual(manager_comment.status_code, 201, manager_comment.content)
        manager_replay = self.manager_client.post(
            f"/api/v1/leave-requests/{request_id}/comments/",
            {"body": "Manager note"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="shared-comment-key",
        )
        self.assertEqual(manager_replay.status_code, 201)
        self.assertEqual(manager_replay.json()["id"], manager_comment.json()["id"])
        self.assertEqual(LeaveRequestComment.objects.filter(request_id=request_id).count(), 2)
        self.assertTrue(LeaveRequestEvent.objects.filter(
            request_id=request_id, event="comment",
        ).exists())

    def test_confidential_decisions_and_comments_are_hr_only(self):
        confidential_type = LeaveType.objects.create(
            key="medical",
            label_fa="Medical",
            employee_requestable=False,
            confidential=True,
        )
        request = LeaveRequest.objects.create(
            employee=self.employee,
            manager_snapshot=self.manager,
            leave_type=confidential_type,
            start_date=dt.date(2026, 11, 2),
            end_date=dt.date(2026, 11, 2),
            working_days="1",
            status=wf.PENDING,
            reason="Sensitive medical reason",
        )
        LeaveRequestEvent.objects.create(
            request=request,
            actor=self.employee_user,
            origin=wf.EMPLOYEE,
            event="submit",
            from_status="",
            to_status=wf.PENDING,
            reason="Sensitive event reason",
        )
        self.assertEqual(
            self.manager_client.post(
                f"/api/v1/leave-requests/{request.pk}/approve",
                {},
                format="json",
                HTTP_IDEMPOTENCY_KEY="confidential-approve",
            ).status_code,
            403,
        )
        self.assertEqual(
            self.manager_client.get(
                f"/api/v1/leave-requests/{request.pk}/comments/",
            ).status_code,
            403,
        )
        self.assertEqual(
            self.manager_client.get(
                f"/api/v1/leave-requests/{request.pk}/events/",
            ).json()[0]["reason"],
            "",
        )

        self.assertEqual(
            self.hr_client.get(
                f"/api/v1/leave-requests/{request.pk}/events/",
            ).json()[0]["reason"],
            "Sensitive event reason",
        )
        comment = self.hr_client.post(
            f"/api/v1/leave-requests/{request.pk}/comments/",
            {"body": "HR-only note"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="confidential-comment",
        )
        self.assertEqual(comment.status_code, 201, comment.content)
