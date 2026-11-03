from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone

from . import audit_services
from .authorization import MANAGE_ROLE_ASSIGNMENTS, authorization_context, has_permission
from .models import (
    Company,
    Department,
    EmployeeProfile,
    OrganizationSite,
    RoleAssignment,
)


@transaction.atomic
def grant_role_assignment(*, actor, user_id: int, role: str, scope: str,
                          company_id: int | None = None, site_id: int | None = None,
                          department_id: int | None = None,
                          employee_id: int | None = None,
                          expires_at=None) -> RoleAssignment:
    if not has_permission(
        authorization_context(actor), MANAGE_ROLE_ASSIGNMENTS,
    ):
        raise PermissionDenied("مدیریت نقش‌ها فقط برای مدیر سامانه مجاز است")
    user_model = get_user_model()
    target_user = user_model.objects.select_for_update().get(pk=user_id, is_active=True)
    if target_user.pk == actor.pk:
        raise ValidationError({"userId": "نقش‌های خود را نمی‌توانید تغییر دهید"})
    if expires_at is not None and expires_at <= timezone.now():
        raise ValidationError({"expiresAt": "زمان انقضا باید در آینده باشد"})

    expired_assignments = RoleAssignment.objects.select_for_update().filter(
        user=target_user, is_active=True, expires_at__lte=timezone.now(),
    ).order_by("pk")
    for expired in expired_assignments:
        before = audit_services.snapshot(expired)
        expired.is_active = False
        expired.save(update_fields=["is_active", "updated_at"])
        audit_services.record_model_change(
            actor=actor,
            action="role.assignment.expired",
            instance=expired,
            origin="authorization",
            before=before,
        )

    company = Company.objects.get(pk=company_id) if company_id is not None else None
    site = OrganizationSite.objects.get(pk=site_id) if site_id is not None else None
    department = Department.objects.get(pk=department_id) if department_id is not None else None
    employee = EmployeeProfile.objects.select_related("company").get(
        pk=employee_id,
    ) if employee_id is not None else None
    assignment = RoleAssignment(
        user=target_user,
        role=role,
        scope=scope,
        company=company,
        site=site,
        department=department,
        employee=employee,
        created_by=actor,
        expires_at=expires_at,
    )
    assignment.save()
    audit_services.record_model_change(
        actor=actor,
        action="role.assignment.granted",
        instance=assignment,
        origin="authorization",
    )
    return assignment


@transaction.atomic
def revoke_role_assignment(*, actor, assignment_id: int) -> None:
    if not has_permission(
        authorization_context(actor), MANAGE_ROLE_ASSIGNMENTS,
    ):
        raise PermissionDenied("مدیریت نقش‌ها فقط برای مدیر سامانه مجاز است")
    now = timezone.now()
    active_assignments = list(
        RoleAssignment.objects.select_for_update().filter(is_active=True).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now),
        ).order_by("pk")
    )
    assignment = next((item for item in active_assignments if item.pk == assignment_id), None)
    if assignment is None:
        raise Http404
    if assignment.user_id == actor.pk:
        raise ValidationError({"assignment": "نقش‌های خود را نمی‌توانید لغو کنید"})
    before = audit_services.snapshot(assignment)
    if assignment.role == RoleAssignment.ROLE_ADMIN:
        active_admins = [
            item for item in active_assignments
            if item.role == RoleAssignment.ROLE_ADMIN
            and item.scope == RoleAssignment.SCOPE_GLOBAL
        ]
        if len(active_admins) == 1 and not actor.is_superuser:
            raise ValidationError({"assignment": "آخرین مدیر سامانه را نمی‌توان لغو کرد"})
    assignment.is_active = False
    assignment.revoked_by = actor
    assignment.save(update_fields=["is_active", "revoked_by", "updated_at"])
    audit_services.record_model_change(
        actor=actor,
        action="role.assignment.revoked",
        instance=assignment,
        origin="authorization",
        before=before,
    )


def assignment_payload(assignment: RoleAssignment) -> dict:
    return {
        "id": assignment.pk,
        "userId": assignment.user_id,
        "role": assignment.role,
        "scope": assignment.scope,
        "companyId": assignment.company_id,
        "siteId": assignment.site_id,
        "departmentId": assignment.department_id,
        "employeeId": assignment.employee_id,
        "isActive": assignment.is_active,
        "expiresAt": assignment.expires_at.isoformat() if assignment.expires_at else None,
    }
