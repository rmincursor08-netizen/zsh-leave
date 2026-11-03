import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.contrib.sessions.models import Session
from django.test import TransactionTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from leave.invitation_services import account_access_allowed, invite_employee
from leave.employment_services import organization_localdate
from leave.notification_services import EmailProviderError
from leave.models import (
    AuditEvent,
    Company,
    Department,
    EmployeeInvitation,
    EmployeeProfile,
    RoleAssignment,
)

User = get_user_model()
VALID_PASSWORD = "Quartz!Twelve-Anchor-846"


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="leave@example.test",
    ZSH_EMAIL_ENABLED=True,
    ZSH_PUBLIC_BASE_URL="https://leave.example.test",
    ZSH_INVITATION_EXPOSE_DEV_LINKS=False,
)
class EmployeeInvitationTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.client = APIClient()
        self.company = Company.objects.create(code="INVITE", name="Invitation company")
        self.department = Department.objects.create(
            company=self.company, code="OPS", name="Operations",
        )
        self.hr_user = User.objects.create_user(username="invitation-hr", password=VALID_PASSWORD)
        self.hr = EmployeeProfile.objects.create(
            user=self.hr_user,
            company=self.company,
            department=self.department,
            personnel_code="HR-INV",
            start_date="2020-01-01",
        )
        RoleAssignment.objects.create(
            user=self.hr_user,
            role=RoleAssignment.ROLE_HR,
            scope=RoleAssignment.SCOPE_COMPANY,
            company=self.company,
        )
        self.client.force_authenticate(self.hr_user)
        self.user, self.employee = self.make_employee("invitee", "person@example.test", "E-INV")

    def make_employee(self, username, email, code):
        user = User.objects.create_user(
            username=username,
            email=email,
            is_active=False,
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        employee = EmployeeProfile.objects.create(
            user=user,
            company=self.company,
            department=self.department,
            personnel_code=code,
            start_date="2020-01-01",
            account_status=EmployeeProfile.ACCOUNT_CREATED,
        )
        return user, employee

    def issue(self, employee=None, email=None):
        response = self.client.post(
            f"/api/v1/employees/{(employee or self.employee).pk}/invitation/",
            {"email": email} if email else {},
            format="json",
        )
        return response

    def invite_token(self, employee=None):
        response = self.issue(employee)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "sent")
        match = re.search(r"#token=([A-Za-z0-9_-]+)", mail.outbox[-1].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def activate(self, token, *, email=None, password=VALID_PASSWORD, extra=None):
        data = {"token": token, "email": email or self.user.email, "password": password}
        if extra:
            data.update(extra)
        return APIClient().post("/api/v1/auth/activate", data, format="json")

    def test_valid_invitation_sets_validated_password_and_activates_account(self):
        token = self.invite_token()
        response = self.activate(token)

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.employee.refresh_from_db()
        invitation = EmployeeInvitation.objects.get(employee=self.employee)
        self.assertTrue(self.user.check_password(VALID_PASSWORD))
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.employee.account_status, EmployeeProfile.ACCOUNT_ACTIVATED)
        self.assertIsNotNone(self.employee.email_verified_at)
        self.assertEqual(invitation.status, EmployeeInvitation.STATUS_CONSUMED)
        self.assertEqual(
            AuditEvent.objects.filter(action__in=[
                "activation.succeeded", "password.setup.completed",
            ]).count(),
            2,
        )

    def test_hr_employee_creation_automatically_starts_invitation(self):
        response = self.client.post(
            "/api/v1/employees/",
            {
                "displayName": "New Employee",
                "personnelCode": "E-CREATED",
                "companyId": self.company.pk,
                "email": "new.employee@example.test",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        created = EmployeeProfile.objects.get(personnel_code="E-CREATED")
        self.assertEqual(response.json()["accountStatus"], EmployeeProfile.INVITATION_SENT)
        self.assertEqual(response.json()["invitationDelivery"], "sent")
        self.assertEqual(created.account_status, EmployeeProfile.INVITATION_SENT)
        self.assertFalse(created.user.is_active)
        self.assertFalse(created.user.has_usable_password())
        self.assertEqual(EmployeeInvitation.objects.filter(employee=created).count(), 1)

    def test_employee_creation_without_email_keeps_account_unusable(self):
        response = self.client.post(
            "/api/v1/employees/",
            {
                "firstName": "No",
                "lastName": "Email",
                "personnelCode": "E-NO-EMAIL",
                "companyId": self.company.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        created = EmployeeProfile.objects.get(personnel_code="E-NO-EMAIL")
        self.assertEqual(created.account_status, EmployeeProfile.ACCOUNT_CREATED)
        self.assertFalse(created.user.is_active)
        self.assertFalse(created.user.has_usable_password())
        self.assertEqual(EmployeeInvitation.objects.filter(employee=created).count(), 0)

    def test_employee_creation_rejects_duplicate_email_and_personnel_code(self):
        duplicate_email = self.client.post(
            "/api/v1/employees/",
            {
                "firstName": "Duplicate",
                "lastName": "Email",
                "personnelCode": "E-DUP-EMAIL",
                "companyId": self.company.pk,
                "email": self.user.email.upper(),
            },
            format="json",
        )
        duplicate_code = self.client.post(
            "/api/v1/employees/",
            {
                "firstName": "Duplicate",
                "lastName": "Code",
                "personnelCode": self.employee.personnel_code,
                "companyId": self.company.pk,
                "email": "new.person@example.test",
            },
            format="json",
        )

        self.assertEqual(duplicate_email.status_code, 409)
        self.assertEqual(duplicate_email.json()["code"], "duplicate_email")
        self.assertEqual(duplicate_code.status_code, 409)
        self.assertFalse(EmployeeProfile.objects.filter(personnel_code="E-DUP-EMAIL").exists())

    def test_hr_cannot_create_employee_outside_assignment_scope(self):
        scoped_assignment = RoleAssignment.objects.get(
            user=self.hr_user, role=RoleAssignment.ROLE_HR,
        )
        scoped_assignment.scope = RoleAssignment.SCOPE_DEPARTMENT
        scoped_assignment.department = self.department
        scoped_assignment.save()
        other_department = Department.objects.create(
            company=self.company, code="OTHER", name="Other department",
        )

        response = self.client.post(
            "/api/v1/employees/",
            {
                "firstName": "Out",
                "lastName": "OfScope",
                "personnelCode": "E-OUT-SCOPE",
                "companyId": self.company.pk,
                "departmentId": other_department.pk,
                "email": "out.of.scope@example.test",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(EmployeeProfile.objects.filter(personnel_code="E-OUT-SCOPE").exists())

    def test_expired_invitation_is_rejected_and_marked_expired(self):
        token = self.invite_token()
        invitation = EmployeeInvitation.objects.get(employee=self.employee)
        invitation.expires_at = timezone.now() - timedelta(seconds=1)
        invitation.save(update_fields=["expires_at"])

        response = self.activate(token)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_invitation")
        invitation.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(invitation.status, EmployeeInvitation.STATUS_EXPIRED)
        self.assertEqual(self.employee.account_status, EmployeeProfile.INVITATION_EXPIRED)

    def test_revoked_invitation_is_rejected(self):
        token = self.invite_token()
        revoked = self.client.post(
            f"/api/v1/employees/{self.employee.pk}/invitation/revoke",
            {"reason": "Invitation request withdrawn"},
            format="json",
        )
        self.assertEqual(revoked.status_code, 200)

        response = self.activate(token)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_invitation")
        self.assertTrue(AuditEvent.objects.filter(action="invitation.revoked").exists())

    def test_consumed_token_cannot_be_reused(self):
        token = self.invite_token()
        self.assertEqual(self.activate(token).status_code, 200)

        response = self.activate(token)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_invitation")

    def test_token_is_bound_to_the_invited_user_and_email(self):
        other_user, other_employee = self.make_employee(
            "other-invitee", "other@example.test", "E-INV-2",
        )
        token = self.invite_token()

        wrong_email = self.activate(token, email=other_user.email)
        changed_account = self.activate(
            token,
            extra={"userId": other_user.pk, "employeeId": other_employee.pk},
        )

        self.assertEqual(wrong_email.status_code, 400)
        self.assertEqual(changed_account.status_code, 400)
        self.assertEqual(wrong_email.json(), changed_account.json())
        other_user.refresh_from_db()
        self.assertFalse(other_user.has_usable_password())

    def test_wrong_email_and_malformed_token_have_generic_errors(self):
        token = self.invite_token()

        wrong_email = self.activate(token, email="wrong@example.test")
        malformed = self.activate("not-a-token")

        self.assertEqual(wrong_email.status_code, 400)
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(wrong_email.json(), malformed.json())

    def test_password_validator_rejection_does_not_consume_invitation(self):
        token = self.invite_token()

        response = self.activate(token, password="123")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "password_invalid")
        self.assertEqual(
            EmployeeInvitation.objects.get(employee=self.employee).status,
            EmployeeInvitation.STATUS_SENT,
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())

    def test_duplicate_and_resend_supersede_the_previous_invitation(self):
        old_token = self.invite_token()
        resent = self.issue()

        self.assertEqual(resent.status_code, 200)
        self.assertEqual(resent.json()["status"], "sent")
        invitations = EmployeeInvitation.objects.filter(employee=self.employee).order_by("pk")
        self.assertEqual(invitations.count(), 2)
        self.assertEqual(invitations[0].status, EmployeeInvitation.STATUS_REVOKED)
        self.assertEqual(invitations[1].status, EmployeeInvitation.STATUS_SENT)
        self.assertEqual(self.activate(old_token).status_code, 400)
        self.assertTrue(AuditEvent.objects.filter(action="invitation.resent").exists())

    def test_resend_rejects_email_already_assigned_to_another_user(self):
        other_user, _ = self.make_employee(
            "duplicate-email-target", "other@example.test", "E-INV-OTHER",
        )
        response = self.issue(email=other_user.email)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "duplicate_email")
        self.assertEqual(
            EmployeeInvitation.objects.filter(employee=self.employee).count(),
            0,
        )

    def test_invitation_rate_limit_is_enforced(self):
        cache.clear()
        for _ in range(10):
            self.assertEqual(self.issue().status_code, 200)
        self.assertEqual(self.issue().status_code, 429)

    def test_public_activation_rate_limit_is_enforced(self):
        client = APIClient()
        cache.clear()
        for _ in range(5):
            self.assertEqual(
                client.post("/api/v1/auth/activate", {}, format="json").status_code,
                400,
            )
        self.assertEqual(
            client.post("/api/v1/auth/activate", {}, format="json").status_code,
            429,
        )

    def test_email_provider_unavailable_does_not_claim_delivery(self):
        with override_settings(
            DEBUG=True,
            ZSH_EMAIL_ENABLED=False,
            ZSH_INVITATION_EXPOSE_DEV_LINKS=True,
        ):
            response = self.issue()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(response.json()["message_fa"], "دعوت ذخیره شد اما سرویس ایمیل در دسترس نیست")
        self.assertIn("developmentActivationUrl", response.json())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            EmployeeInvitation.objects.filter(
                employee=self.employee, status=EmployeeInvitation.STATUS_PENDING,
            ).count(),
            1,
        )

    def test_email_provider_connection_failure_keeps_invitation_pending(self):
        with patch(
            "leave.invitation_services.send_email",
            side_effect=EmailProviderError("ProviderConnectionError", retryable=True),
        ):
            response = self.issue()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(
            EmployeeInvitation.objects.get(employee=self.employee).status,
            EmployeeInvitation.STATUS_PENDING,
        )
        self.assertTrue(AuditEvent.objects.filter(
            action="invitation.delivery_failed", result="failed",
        ).exists())

    def test_raw_token_is_never_stored_in_database_or_audit_records(self):
        token = self.invite_token()
        invitation = EmployeeInvitation.objects.get(employee=self.employee)
        with self.assertLogs("django.request", level="WARNING") as captured:
            response = self.activate(token, email="wrong@example.test")

        self.assertEqual(response.status_code, 400)
        self.assertNotIn(token, "\n".join(captured.output))
        self.assertEqual(invitation.token_hash, hashlib.sha256(token.encode("ascii")).hexdigest())
        self.assertNotEqual(invitation.token_hash, token)
        stored_records = (
            list(EmployeeInvitation.objects.values())
            + list(AuditEvent.objects.values())
        )
        self.assertNotIn(token, str(stored_records))

    def test_offboarded_employee_cannot_activate(self):
        token = self.invite_token()
        self.employee.end_date = timezone.localdate() - timedelta(days=1)
        self.employee.save(update_fields=["end_date"])

        response = self.activate(token)

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.has_usable_password())
        self.assertEqual(self.employee.account_status, EmployeeProfile.ACCOUNT_OFFBOARDED)

    def test_suspended_employee_cannot_activate(self):
        token = self.invite_token()
        suspended = self.client.post(
            f"/api/v1/employees/{self.employee.pk}/suspend",
            {"reason": "Account access review"},
            format="json",
        )
        self.assertEqual(suspended.status_code, 200)

        response = self.activate(token)

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.has_usable_password())

    def test_suspend_invalidates_existing_session_and_audits_reason(self):
        self.user.set_password(VALID_PASSWORD)
        self.user.is_active = True
        self.user.save(update_fields=["password", "is_active"])
        self.employee.account_status = EmployeeProfile.ACCOUNT_ACTIVATED
        self.employee.save(update_fields=["account_status"])

        employee_client = APIClient(enforce_csrf_checks=True)
        employee_client.get("/api/v1/auth/me")
        csrf = employee_client.cookies["csrftoken"].value
        login = employee_client.post(
            "/api/v1/auth/login",
            {"username": self.user.username, "password": VALID_PASSWORD},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(login.status_code, 200, login.content)
        session_key = employee_client.cookies["sessionid"].value
        self.assertTrue(Session.objects.filter(session_key=session_key).exists())

        suspended = self.client.post(
            f"/api/v1/employees/{self.employee.pk}/suspend",
            {"reason": "Security investigation"},
            format="json",
        )

        self.assertEqual(suspended.status_code, 200)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertEqual(employee_client.get("/api/v1/auth/me").status_code, 401)
        self.assertEqual(
            AuditEvent.objects.get(action="account.suspended").reason,
            "Security investigation",
        )
        self.assertTrue(self.user.__class__.objects.filter(
            pk=self.user.pk, is_active=False,
        ).exists())
    def test_future_employment_activation_does_not_grant_login_access(self):
        token = self.invite_token()
        self.employee.start_date = timezone.localdate() + timedelta(days=30)
        self.employee.save(update_fields=["start_date"])

        activation = self.activate(token)
        login_client = APIClient(enforce_csrf_checks=True)
        csrf = login_client.get("/api/v1/auth/me").cookies["csrftoken"].value
        login = login_client.post(
            "/api/v1/auth/login",
            {"username": self.user.username, "password": VALID_PASSWORD},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )

        self.assertEqual(activation.status_code, 200)
        self.assertEqual(login.status_code, 401)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.user.check_password(VALID_PASSWORD))
        self.assertTrue(
            account_access_allowed(self.user, on_date=self.employee.start_date),
        )

    def test_employee_access_observes_inclusive_dates_and_suspension(self):
        today = organization_localdate()
        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        self.employee.account_status = EmployeeProfile.ACCOUNT_ACTIVATED
        self.employee.start_date = today - timedelta(days=1)
        self.employee.end_date = today
        self.employee.save(update_fields=["account_status", "start_date", "end_date"])
        self.assertTrue(account_access_allowed(self.user, on_date=today))

        self.employee.end_date = today + timedelta(days=1)
        self.employee.save(update_fields=["end_date"])
        self.assertTrue(account_access_allowed(self.user, on_date=today))

        self.employee.start_date = today + timedelta(days=1)
        self.employee.save(update_fields=["start_date"])
        self.assertFalse(account_access_allowed(self.user, on_date=today))

        self.employee.start_date = today - timedelta(days=1)
        self.employee.account_status = EmployeeProfile.ACCOUNT_SUSPENDED
        self.employee.save(update_fields=["start_date", "account_status"])
        self.assertFalse(account_access_allowed(self.user, on_date=today))

    def test_existing_session_is_rejected_after_employment_end_date_passes(self):
        self.user.is_active = True
        self.user.set_password(VALID_PASSWORD)
        self.user.save(update_fields=["password", "is_active"])
        self.employee.account_status = EmployeeProfile.ACCOUNT_ACTIVATED
        self.employee.start_date = organization_localdate() - timedelta(days=30)
        self.employee.end_date = None
        self.employee.save(update_fields=["account_status", "start_date", "end_date"])
        client = APIClient()
        login = client.post(
            "/api/v1/auth/login",
            {"username": self.user.username, "password": VALID_PASSWORD},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.content)
        self.assertEqual(client.get("/api/v1/auth/me").status_code, 200)

        EmployeeProfile.objects.filter(pk=self.employee.pk).update(
            end_date=organization_localdate() - timedelta(days=1),
        )
        expired_response = client.get("/api/v1/auth/me")

        self.assertEqual(expired_response.status_code, 401)
        self.user.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.employee.account_status, EmployeeProfile.ACCOUNT_OFFBOARDED)

    def test_development_link_is_never_returned_when_debug_is_disabled(self):
        with override_settings(
            DEBUG=False,
            ZSH_EMAIL_ENABLED=False,
            ZSH_INVITATION_EXPOSE_DEV_LINKS=True,
        ):
            response = self.issue()

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("developmentActivationUrl", response.json())


class ConcurrentInvitationConsumptionTests(TransactionTestCase):
    reset_sequences = True

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="leave@example.test",
        ZSH_EMAIL_ENABLED=True,
        ZSH_PUBLIC_BASE_URL="https://leave.example.test",
    )
    def test_simultaneous_consumption_succeeds_at_most_once(self):
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock concurrency is validated on PostgreSQL.")

        company = Company.objects.create(code="RACE", name="Race company")
        department = Department.objects.create(
            company=company, code="OPS", name="Operations",
        )
        operator = User.objects.create_user(username="race-operator", password=VALID_PASSWORD)
        user = User.objects.create_user(
            username="race-invitee", email="race@example.test", is_active=False,
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        employee = EmployeeProfile.objects.create(
            user=user,
            company=company,
            department=department,
            personnel_code="RACE-1",
            start_date="2020-01-01",
            account_status=EmployeeProfile.ACCOUNT_CREATED,
        )
        invite_employee(employee_id=employee.pk, actor=operator)
        token_match = re.search(r"#token=([A-Za-z0-9_-]+)", mail.outbox[-1].body)
        self.assertIsNotNone(token_match)
        token = token_match.group(1)
        barrier = threading.Barrier(2)

        def consume():
            close_old_connections()
            barrier.wait()
            result = APIClient().post(
                "/api/v1/auth/activate",
                {"token": token, "email": user.email, "password": VALID_PASSWORD},
                format="json",
            )
            close_old_connections()
            return result.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _index: consume(), range(2)))

        self.assertEqual(outcomes.count(200), 1)
        self.assertEqual(outcomes.count(400), 1)
