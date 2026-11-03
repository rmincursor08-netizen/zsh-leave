from __future__ import annotations

import smtplib
from datetime import timedelta

from django.conf import settings
from django.core.mail import BadHeaderError, send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .authorization import HR, authorization_context, can_view_leave_request
from .employment_services import is_employed_on, organization_localdate
from .models import (
    EmployeeProfile,
    EntitlementEvent,
    EmployeeInvitation,
    LeaveRequestEvent,
    NotificationDelivery,
    RoleAssignment,
)

MAX_DELIVERY_ATTEMPTS = 3
RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=15))
SENDING_LEASE = timedelta(minutes=15)
REQUEST_NOTIFICATION_EVENTS = {
    "submit",
    "approve",
    "reject",
    "cancel",
    "hr_cancel",
    "request_withdraw",
    "approve_withdraw",
    "reject_withdraw",
    "correct",
    "amend",
    "amend_approve",
    "amendment_approve",
    "amendment_reject",
    "escalate",
    "reminder",
}
ENTITLEMENT_NOTIFICATION_EVENTS = {"opening", "adjustment", "carry_forward", "expiration"}


class EmailProviderUnavailable(Exception):
    pass


class EmailProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool, outcome_unknown: bool = False):
        self.code = code[:64]
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown
        super().__init__(self.code)


class DjangoEmailProvider:
    def is_configured(self) -> bool:
        if not getattr(settings, "ZSH_EMAIL_ENABLED", False):
            return False
        if not getattr(settings, "DEFAULT_FROM_EMAIL", ""):
            return False
        backend = getattr(settings, "EMAIL_BACKEND", "")
        if backend.endswith("smtp.EmailBackend") and not getattr(settings, "EMAIL_HOST", ""):
            return False
        return True

    def send(self, *, subject: str, body: str, recipient: str) -> None:
        if not self.is_configured():
            raise EmailProviderUnavailable
        try:
            sent_count = send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )
        except BadHeaderError as exc:
            raise EmailProviderError("BadHeaderError", retryable=False) from exc
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailProviderError("SMTPAuthenticationError", retryable=False) from exc
        except smtplib.SMTPResponseException as exc:
            retryable = 400 <= exc.smtp_code < 500
            raise EmailProviderError(
                "SMTPTransientError" if retryable else "SMTPRejected",
                retryable=retryable,
            ) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailProviderError(
                "ProviderOutcomeUnknown", retryable=False, outcome_unknown=True,
            ) from exc
        if sent_count != 1:
            raise EmailProviderError("ProviderRejected", retryable=True)


def get_email_provider() -> DjangoEmailProvider:
    return DjangoEmailProvider()


def email_provider_configured() -> bool:
    return get_email_provider().is_configured()


def send_email(*, subject: str, body: str, recipient: str) -> None:
    get_email_provider().send(subject=subject, body=body, recipient=recipient)


def _hr_user_ids(request) -> set[int]:
    filters = Q(scope=RoleAssignment.SCOPE_COMPANY, company_id=request.employee.company_id)
    if request.employee.site_id:
        filters |= Q(
            scope=RoleAssignment.SCOPE_SITE,
            company_id=request.employee.company_id,
            site_id=request.employee.site_id,
        )
    if request.employee.department_id:
        filters |= Q(
            scope=RoleAssignment.SCOPE_DEPARTMENT,
            company_id=request.employee.company_id,
            department_id=request.employee.department_id,
        )
    filters |= Q(
        scope=RoleAssignment.SCOPE_EMPLOYEE,
        company_id=request.employee.company_id,
        employee_id=request.employee_id,
    )
    now = timezone.now()
    return set(RoleAssignment.objects.filter(
        role=RoleAssignment.ROLE_HR,
        is_active=True,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now),
    ).filter(filters).values_list("user_id", flat=True))


def queue_request_event_delivery(event: LeaveRequestEvent) -> int:
    if event.event not in REQUEST_NOTIFICATION_EVENTS:
        return 0
    request = event.request
    recipient_ids = {request.employee.user_id}
    if request.manager_snapshot_id and not request.leave_type.confidential:
        manager_user_id = EmployeeProfile.objects.filter(
            pk=request.manager_snapshot_id,
            is_employee=True,
        ).values_list("user_id", flat=True).first()
        if manager_user_id:
            recipient_ids.add(manager_user_id)
    recipient_ids.update(_hr_user_ids(request))
    recipient_ids.discard(event.actor_id)

    created = 0
    for user_id in recipient_ids:
        _, was_created = NotificationDelivery.objects.get_or_create(
            idempotency_key=f"request-event:{event.pk}:user:{user_id}",
            defaults={
                "recipient_id": user_id,
                "source_type": NotificationDelivery.SOURCE_REQUEST_EVENT,
                "request_event": event,
            },
        )
        created += int(was_created)
    return created


def queue_entitlement_event_delivery(event: EntitlementEvent) -> int:
    if event.kind not in ENTITLEMENT_NOTIFICATION_EVENTS:
        return 0
    user_id = EmployeeProfile.objects.filter(
        pk=event.entitlement.employee_id,
        is_employee=True,
    ).values_list("user_id", flat=True).first()
    if user_id is None:
        return 0
    _, created = NotificationDelivery.objects.get_or_create(
        idempotency_key=f"entitlement-event:{event.pk}:user:{user_id}",
        defaults={
            "recipient_id": user_id,
            "source_type": NotificationDelivery.SOURCE_ENTITLEMENT_EVENT,
            "entitlement_event": event,
        },
    )
    return int(created)


def queue_activation_confirmation(invitation: EmployeeInvitation) -> bool:
    user_id = invitation.employee.user_id
    _, created = NotificationDelivery.objects.get_or_create(
        idempotency_key=f"activation:{invitation.pk}",
        defaults={
            "recipient_id": user_id,
            "source_type": NotificationDelivery.SOURCE_ACTIVATION,
            "invitation": invitation,
        },
    )
    return created


def _recipient_is_active_for_delivery(user) -> bool:
    if not user.is_active or not user.email:
        return False
    profile = EmployeeProfile.objects.filter(user_id=user.pk, is_employee=True).first()
    if profile is None:
        return True
    return (
        profile.account_status == EmployeeProfile.ACCOUNT_ACTIVATED
        and is_employed_on(profile, organization_localdate())
    )


def _request_is_authorized_now(user, event: LeaveRequestEvent) -> bool:
    request = event.request
    context = authorization_context(user)
    is_employee = context.employee_id == request.employee_id
    is_hr = context.has_scope(
        HR,
        company_id=request.employee.company_id,
        site_id=request.employee.site_id,
        department_id=request.employee.department_id,
        employee_id=request.employee_id,
    )
    is_current_manager = bool(
        context.employee_id
        and EmployeeProfile.objects.filter(
            pk=request.employee_id,
            manager_id=context.employee_id,
            company_id=request.employee.company_id,
            is_employee=True,
        ).exists()
    )
    if not (is_employee or is_hr or is_current_manager):
        return False
    if not can_view_leave_request(context, request):
        return False
    if is_current_manager and request.leave_type.confidential:
        return is_hr
    return True


def _delivery_content(delivery: NotificationDelivery) -> tuple[str, str, bool]:
    if delivery.source_type == NotificationDelivery.SOURCE_REQUEST_EVENT:
        event = LeaveRequestEvent.objects.select_related(
            "request__employee", "request__leave_type",
        ).get(pk=delivery.request_event_id)
        return (
            "به‌روزرسانی درخواست مرخصی",
            "یک رویداد مرتبط با درخواست مرخصی در سامانه ثبت شده است. برای مشاهده، وارد سامانه شوید.",
            _request_is_authorized_now(delivery.recipient, event),
        )

    if delivery.source_type == NotificationDelivery.SOURCE_ENTITLEMENT_EVENT:
        event = EntitlementEvent.objects.select_related("entitlement__employee").get(
            pk=delivery.entitlement_event_id,
        )
        context = authorization_context(delivery.recipient)
        authorized = context.employee_id == event.entitlement.employee_id
        return (
            "به‌روزرسانی سوابق مرخصی",
            "تغییری در سوابق سهمیه مرخصی شما در سامانه ثبت شده است.",
            authorized,
        )

    invitation = EmployeeInvitation.objects.select_related("employee__user").get(
        pk=delivery.invitation_id,
    )
    employee = invitation.employee
    authorized = (
        invitation.status == EmployeeInvitation.STATUS_CONSUMED
        and employee.user_id == delivery.recipient_id
        and employee.user.email.casefold() == invitation.email.casefold()
        and _recipient_is_active_for_delivery(delivery.recipient)
    )
    return (
        "فعال‌سازی حساب انجام شد",
        "حساب شما با موفقیت فعال شد. برای استفاده از سامانه وارد شوید.",
        authorized,
    )


def _set_status(delivery_id: int, *, status: str, error_code: str = "", retry_after=None) -> None:
    NotificationDelivery.objects.filter(pk=delivery_id).update(
        status=status,
        last_error_code=error_code,
        retry_after=retry_after,
        updated_at=timezone.now(),
        sent_at=timezone.now() if status == NotificationDelivery.STATUS_SENT else None,
    )


def deliver_pending_notifications(*, now=None, limit: int | None = None) -> dict:
    now = now or timezone.now()
    limit = limit or getattr(settings, "ZSH_NOTIFICATION_BATCH_LIMIT", 50)
    limit = max(1, min(int(limit), 500))
    counts = {"processed": 0, "sent": 0, "retrying": 0, "failed": 0, "unavailable": 0}

    NotificationDelivery.objects.filter(
        status=NotificationDelivery.STATUS_SENDING,
        updated_at__lt=now - SENDING_LEASE,
    ).update(
        status=NotificationDelivery.STATUS_UNAVAILABLE,
        last_error_code="DeliveryOutcomeUnknown",
        updated_at=now,
    )

    provider = get_email_provider()
    if not provider.is_configured():
        counts["unavailable"] = NotificationDelivery.objects.filter(
            status=NotificationDelivery.STATUS_QUEUED,
        ).update(
            status=NotificationDelivery.STATUS_UNAVAILABLE,
            last_error_code="EmailProviderNotConfigured",
            updated_at=now,
        )
        return counts

    NotificationDelivery.objects.filter(
        status=NotificationDelivery.STATUS_UNAVAILABLE,
        last_error_code="EmailProviderNotConfigured",
    ).update(status=NotificationDelivery.STATUS_QUEUED, last_error_code="", updated_at=now)

    due = NotificationDelivery.objects.filter(status=NotificationDelivery.STATUS_QUEUED).filter(
        Q(retry_after__isnull=True) | Q(retry_after__lte=now),
    ).order_by("created_at", "pk").values_list("pk", flat=True)[:limit]

    for delivery_id in list(due):
        with transaction.atomic():
            delivery = NotificationDelivery.objects.select_for_update().select_related(
                "recipient",
            ).filter(pk=delivery_id).first()
            if (
                delivery is None
                or delivery.status != NotificationDelivery.STATUS_QUEUED
                or delivery.retry_after is not None and delivery.retry_after > now
            ):
                continue
            delivery.status = NotificationDelivery.STATUS_SENDING
            delivery.attempt_count += 1
            delivery.retry_after = None
            delivery.updated_at = now
            delivery.save(update_fields=[
                "status", "attempt_count", "retry_after", "updated_at",
            ])

        counts["processed"] += 1
        try:
            if not _recipient_is_active_for_delivery(delivery.recipient):
                raise EmailProviderUnavailable("recipient_unavailable")
            subject, body, authorized = _delivery_content(delivery)
            if not authorized:
                _set_status(
                    delivery.pk,
                    status=NotificationDelivery.STATUS_UNAVAILABLE,
                    error_code="RecipientNoLongerAuthorized",
                )
                counts["unavailable"] += 1
                continue
            recipient = delivery.recipient.email.strip()
            if not recipient:
                _set_status(
                    delivery.pk,
                    status=NotificationDelivery.STATUS_UNAVAILABLE,
                    error_code="RecipientEmailMissing",
                )
                counts["unavailable"] += 1
                continue
            provider.send(subject=subject, body=body, recipient=recipient)
        except EmailProviderUnavailable as exc:
            code = str(exc) if str(exc) == "recipient_unavailable" else "EmailProviderNotConfigured"
            _set_status(
                delivery.pk,
                status=NotificationDelivery.STATUS_UNAVAILABLE,
                error_code="RecipientUnavailable" if code == "recipient_unavailable" else code,
            )
            counts["unavailable"] += 1
        except EmailProviderError as exc:
            if exc.outcome_unknown:
                _set_status(
                    delivery.pk,
                    status=NotificationDelivery.STATUS_UNAVAILABLE,
                    error_code="DeliveryOutcomeUnknown",
                )
                counts["unavailable"] += 1
            elif exc.retryable and delivery.attempt_count < MAX_DELIVERY_ATTEMPTS:
                retry_after = now + RETRY_DELAYS[min(delivery.attempt_count - 1, len(RETRY_DELAYS) - 1)]
                _set_status(
                    delivery.pk,
                    status=NotificationDelivery.STATUS_QUEUED,
                    error_code=exc.code,
                    retry_after=retry_after,
                )
                counts["retrying"] += 1
            else:
                _set_status(
                    delivery.pk,
                    status=NotificationDelivery.STATUS_FAILED,
                    error_code=exc.code,
                )
                counts["failed"] += 1
        else:
            _set_status(delivery.pk, status=NotificationDelivery.STATUS_SENT)
            counts["sent"] += 1

    return counts
