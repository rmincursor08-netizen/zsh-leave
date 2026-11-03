from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from . import audit_services
from .employment_services import is_employed_on, organization_localdate
from .models import EmployeeInvitation, EmployeeProfile
from .notification_services import (
    EmailProviderError,
    EmailProviderUnavailable,
    email_provider_configured,
    queue_activation_confirmation,
    send_email,
)

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
ACTIVE_INVITATION_STATUSES = (
    EmployeeInvitation.STATUS_PENDING,
    EmployeeInvitation.STATUS_SENT,
)


class InvitationError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class InvitationResult:
    status: str
    development_url: str | None = None


def normalize_email(email: str) -> str:
    if not isinstance(email, str):
        raise InvitationError("invalid_email")
    normalized = email.strip().casefold()
    try:
        validate_email(normalized)
    except ValidationError as exc:
        raise InvitationError("invalid_email") from exc
    if len(normalized) > 254:
        raise InvitationError("invalid_email")
    return normalized


def account_access_allowed(user, *, on_date=None) -> bool:
    if not getattr(user, "is_active", False):
        return False
    profile = EmployeeProfile.objects.select_related("user").filter(
        user_id=user.pk, is_employee=True,
    ).first()
    if profile is None:
        return True
    today = on_date or organization_localdate()
    if profile.end_date is not None and profile.end_date < today:
        with transaction.atomic():
            profile = EmployeeProfile.objects.select_for_update().select_related("user").get(
                pk=profile.pk,
            )
            if profile.user.is_active:
                profile.user.is_active = False
                profile.user.save(update_fields=["is_active"])
            if profile.account_status != EmployeeProfile.ACCOUNT_OFFBOARDED:
                profile.account_status = EmployeeProfile.ACCOUNT_OFFBOARDED
                profile.save(update_fields=["account_status"])
                _audit(action="account.offboarded", employee_id=profile.pk)
            revoked = EmployeeInvitation.objects.filter(
                employee=profile,
                status__in=ACTIVE_INVITATION_STATUSES,
            ).update(status=EmployeeInvitation.STATUS_REVOKED)
            if revoked:
                _audit(action="invitation.revoked", employee_id=profile.pk)
        return False
    return (
        profile.account_status == EmployeeProfile.ACCOUNT_ACTIVATED
        and is_employed_on(profile, today)
    )


def _audit(*, action: str, employee_id: int | None, actor=None, result="ok", reason=""):
    profile = EmployeeProfile.objects.filter(pk=employee_id).first() if employee_id else None
    invitation = None
    if profile is not None and action.startswith("invitation."):
        invitation = EmployeeInvitation.objects.filter(employee_id=profile.pk).order_by("-pk").first()
    instance = invitation or profile
    audit_services.record_audit(
        actor=actor,
        origin="auth",
        action=action,
        entity="EmployeeInvitation" if invitation is not None else (
            "EmployeeProfile" if profile is not None else "Invitation"
        ),
        entity_id=instance.pk if instance is not None else "",
        before=None,
        after=audit_services.snapshot(instance) if instance is not None else None,
        reason=reason,
        result=result,
    )


def _development_url(token: str) -> str | None:
    if not (
        settings.DEBUG
        and getattr(settings, "ZSH_INVITATION_EXPOSE_DEV_LINKS", False)
    ):
        return None
    base_url = getattr(settings, "ZSH_PUBLIC_BASE_URL", "http://localhost:5173").rstrip("/")
    return f"{base_url}/activate-account#token={quote(token, safe='')}"


def invite_employee(*, employee_id: int, actor, email: str | None = None) -> InvitationResult:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    now = timezone.now()
    ttl_hours = settings.ZSH_INVITATION_TOKEN_TTL_HOURS

    with transaction.atomic():
        employee = EmployeeProfile.objects.select_for_update().select_related("user").get(
            pk=employee_id,
        )
        if employee.end_date is not None and employee.end_date < organization_localdate():
            raise InvitationError("account_not_invitable")
        if employee.account_status in {
            EmployeeProfile.ACCOUNT_ACTIVATED,
            EmployeeProfile.ACCOUNT_SUSPENDED,
            EmployeeProfile.ACCOUNT_OFFBOARDED,
        }:
            raise InvitationError("account_not_invitable")
        target_email = normalize_email(email if email is not None else employee.user.email)
        if get_user_model().objects.filter(email__iexact=target_email).exclude(
            pk=employee.user_id,
        ).exists():
            raise InvitationError("email_in_use")
        was_invited = EmployeeInvitation.objects.filter(employee=employee).exists()
        revoked = EmployeeInvitation.objects.select_for_update().filter(
            employee=employee, status__in=ACTIVE_INVITATION_STATUSES,
        ).update(status=EmployeeInvitation.STATUS_REVOKED)
        if revoked:
            _audit(action="invitation.revoked", employee_id=employee.pk, actor=actor)

        if employee.user.email.casefold() != target_email:
            employee.user.email = target_email
            employee.user.save(update_fields=["email"])
            employee.email_verified_at = None

        invitation = EmployeeInvitation.objects.create(
            employee=employee,
            token_hash=token_hash,
            email=target_email,
            expires_at=now + timedelta(hours=ttl_hours),
            created_by=actor,
        )
        employee.account_status = EmployeeProfile.INVITATION_PENDING
        employee.save(update_fields=["account_status", "email_verified_at"])
        _audit(
            action="invitation.resent" if was_invited else "invitation.created",
            employee_id=employee.pk,
            actor=actor,
        )

        if not email_provider_configured() or not settings.ZSH_PUBLIC_BASE_URL:
            _audit(
                action="invitation.delivery_failed",
                employee_id=employee.pk,
                actor=actor,
                result="failed",
            )
            return InvitationResult("pending", _development_url(raw_token))

        activation_url = _development_url(raw_token)
        public_base_url = settings.ZSH_PUBLIC_BASE_URL.rstrip("/")
        activation_link = f"{public_base_url}/activate-account#token={quote(raw_token, safe='')}"
        try:
            send_email(
                subject="فعال‌سازی حساب کاربری",
                body=(
                    "برای تنظیم رمز عبور و فعال‌سازی حساب، پیوند زیر را باز کنید. "
                    "این پیوند یک‌بارمصرف است و پس از انقضا قابل استفاده نیست:\n"
                    f"{activation_link}"
                ),
                recipient=target_email,
            )
            sent_count = 1
        except (EmailProviderError, EmailProviderUnavailable):
            sent_count = 0

        if sent_count != 1:
            _audit(
                action="invitation.delivery_failed",
                employee_id=employee.pk,
                actor=actor,
                result="failed",
            )
            return InvitationResult("pending", activation_url)

        invitation.status = EmployeeInvitation.STATUS_SENT
        invitation.sent_at = timezone.now()
        invitation.save(update_fields=["status", "sent_at"])
        employee.account_status = EmployeeProfile.INVITATION_SENT
        employee.save(update_fields=["account_status"])
        _audit(action="invitation.sent", employee_id=employee.pk, actor=actor)
        return InvitationResult("sent", activation_url)


def revoke_invitation(*, employee_id: int, actor, reason: str) -> bool:
    with transaction.atomic():
        employee = EmployeeProfile.objects.select_for_update().get(pk=employee_id)
        changed = EmployeeInvitation.objects.select_for_update().filter(
            employee=employee, status__in=ACTIVE_INVITATION_STATUSES,
        ).update(status=EmployeeInvitation.STATUS_REVOKED)
        if changed:
            employee.account_status = EmployeeProfile.INVITATION_REVOKED
            employee.save(update_fields=["account_status"])
            _audit(
                action="invitation.revoked",
                employee_id=employee.pk,
                actor=actor,
                reason=reason,
            )
        return bool(changed)


def suspend_employee(*, employee_id: int, actor, reason: str) -> None:
    with transaction.atomic():
        employee = EmployeeProfile.objects.select_for_update().select_related("user").get(
            pk=employee_id,
        )
        revoked = EmployeeInvitation.objects.select_for_update().filter(
            employee=employee, status__in=ACTIVE_INVITATION_STATUSES,
        ).update(status=EmployeeInvitation.STATUS_REVOKED)
        if revoked:
            _audit(action="invitation.revoked", employee_id=employee.pk, actor=actor)
        employee.account_status = EmployeeProfile.ACCOUNT_SUSPENDED
        employee.user.is_active = False
        employee.user.save(update_fields=["is_active"])
        employee.save(update_fields=["account_status"])
        for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
            if session.get_decoded().get("_auth_user_id") == str(employee.user_id):
                session.delete()
        _audit(
            action="account.suspended",
            employee_id=employee.pk,
            actor=actor,
            reason=reason,
        )


def activate_invitation(*, raw_token, email, password) -> str | None:
    if not isinstance(raw_token, str) or not TOKEN_PATTERN.fullmatch(raw_token):
        _audit(action="activation.failed", employee_id=None, result="failed")
        return "invalid_invitation"
    try:
        supplied_email = normalize_email(email)
    except InvitationError:
        _audit(action="activation.failed", employee_id=None, result="failed")
        return "invalid_invitation"
    if not isinstance(password, str) or not password:
        _audit(action="activation.failed", employee_id=None, result="failed")
        return "invalid_invitation"

    token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    error_code = None
    now = timezone.now()
    with transaction.atomic():
        invitation_ref = (
            EmployeeInvitation.objects.filter(
                token_hash=token_hash,
                purpose=EmployeeInvitation.PURPOSE_EMPLOYEE_ACTIVATION,
            )
            .values("pk", "employee_id")
            .first()
        )
        employee = None
        invitation = None
        if invitation_ref is None:
            error_code = "invalid_invitation"
        else:
            employee = EmployeeProfile.objects.select_for_update().select_related("user").get(
                pk=invitation_ref["employee_id"],
            )
            invitation = (
                EmployeeInvitation.objects.select_for_update()
                .filter(
                    pk=invitation_ref["pk"],
                    token_hash=token_hash,
                    purpose=EmployeeInvitation.PURPOSE_EMPLOYEE_ACTIVATION,
                )
                .first()
            )
        if invitation_ref is not None and invitation is None:
            error_code = "invalid_invitation"
        elif invitation is not None and invitation.status not in ACTIVE_INVITATION_STATUSES:
            error_code = "invalid_invitation"
        elif invitation is not None and invitation.expires_at <= now:
            invitation.status = EmployeeInvitation.STATUS_EXPIRED
            invitation.save(update_fields=["status"])
            if employee.account_status in {
                EmployeeProfile.INVITATION_PENDING,
                EmployeeProfile.INVITATION_SENT,
            }:
                employee.account_status = EmployeeProfile.INVITATION_EXPIRED
                employee.save(update_fields=["account_status"])
            error_code = "invalid_invitation"
        elif invitation is not None:
            user = employee.user
            if (
                employee.account_status in {
                    EmployeeProfile.ACCOUNT_SUSPENDED,
                    EmployeeProfile.ACCOUNT_OFFBOARDED,
                }
                or employee.end_date is not None
                and employee.end_date < organization_localdate()
                or supplied_email != invitation.email.casefold()
                or supplied_email != user.email.casefold()
            ):
                if employee.end_date is not None and employee.end_date < organization_localdate():
                    employee.account_status = EmployeeProfile.ACCOUNT_OFFBOARDED
                    user.is_active = False
                    user.save(update_fields=["is_active"])
                    employee.save(update_fields=["account_status"])
                    revoked = EmployeeInvitation.objects.filter(
                        employee=employee,
                        status__in=ACTIVE_INVITATION_STATUSES,
                    ).update(status=EmployeeInvitation.STATUS_REVOKED)
                    _audit(action="account.offboarded", employee_id=employee.pk)
                    if revoked:
                        _audit(
                            action="invitation.revoked",
                            employee_id=employee.pk,
                            result="ok",
                        )
                error_code = "invalid_invitation"
            else:
                try:
                    password_validation.validate_password(password, user)
                except ValidationError:
                    error_code = "password_invalid"

                if error_code is None:
                    consumed = EmployeeInvitation.objects.filter(
                        pk=invitation.pk,
                        status__in=ACTIVE_INVITATION_STATUSES,
                        expires_at__gt=now,
                    ).update(
                        status=EmployeeInvitation.STATUS_CONSUMED,
                        consumed_at=now,
                    )
                    if consumed != 1:
                        error_code = "invalid_invitation"
                    else:
                        user.set_password(password)
                        user.is_active = True
                        user.save(update_fields=["password", "is_active"])
                        employee.account_status = EmployeeProfile.ACCOUNT_ACTIVATED
                        employee.email_verified_at = now
                        employee.save(update_fields=["account_status", "email_verified_at"])
                        _audit(action="activation.succeeded", employee_id=employee.pk, actor=user)
                        queue_activation_confirmation(invitation)
                        _audit(action="password.setup.completed", employee_id=employee.pk, actor=user)

        if error_code is not None:
            _audit(
                action="activation.failed",
                employee_id=(
                    invitation.employee_id if invitation
                    else invitation_ref["employee_id"] if invitation_ref
                    else None
                ),
                result="failed",
            )
    return error_code


def synchronize_employment_access(*, employee: EmployeeProfile, actor) -> None:
    employee.user.refresh_from_db()
    today = organization_localdate()
    if employee.end_date is not None and employee.end_date < today:
        employee.account_status = EmployeeProfile.ACCOUNT_OFFBOARDED
        employee.user.is_active = False
        employee.user.save(update_fields=["is_active"])
        revoked = EmployeeInvitation.objects.filter(
            employee=employee, status__in=ACTIVE_INVITATION_STATUSES,
        ).update(status=EmployeeInvitation.STATUS_REVOKED)
        employee.save(update_fields=["account_status"])
        _audit(action="account.offboarded", employee_id=employee.pk, actor=actor)
        if revoked:
            _audit(action="invitation.revoked", employee_id=employee.pk, actor=actor)
    elif employee.account_status == EmployeeProfile.ACCOUNT_ACTIVATED:
        employee.user.is_active = True
        employee.user.save(update_fields=["is_active"])
