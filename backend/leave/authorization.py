from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import Q, QuerySet
from django.utils import timezone

from .employment_services import is_employed_on
from .models import EmployeeProfile, LeaveRequest, RoleAssignment

EMPLOYEE = "employee"
MANAGER = "manager"
HR = "hr"
ADMIN = "admin"
EXEC = "exec"

READ_PERSONAL_CALENDAR = "calendar.personal.read"
READ_TEAM_CALENDAR = "calendar.team.read"
READ_COMPANY_CALENDAR = "calendar.company.read"
READ_WORKDAY_ESTIMATE = "workday.estimate.read"
READ_WORK_PATTERNS = "work-pattern.read"
READ_LEAVE_POLICY = "leave-policy.read"
MANAGE_SYSTEM_CONFIGURATION = "system-configuration.manage"
MANAGE_ROLE_ASSIGNMENTS = "role-assignment.manage"
READ_LEAVE_TYPES = "leave_type.read"
MANAGE_GLOBAL_LEAVE_TYPES = "leave_type.global.manage"


@dataclass(frozen=True)
class AuthorizationContext:
    user: Any
    employee: EmployeeProfile | None
    roles: frozenset[str]
    assignments: tuple[RoleAssignment, ...]

    @property
    def employee_id(self) -> int | None:
        return self.employee.pk if self.employee else None

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_scope(self, role: str, *, company_id: int, site_id: int | None = None,
                  department_id: int | None = None, employee_id: int | None = None) -> bool:
        for assignment in self.assignments:
            if assignment.role != role:
                continue
            if assignment.scope == RoleAssignment.SCOPE_COMPANY and assignment.company_id == company_id:
                return True
            if (assignment.scope == RoleAssignment.SCOPE_SITE
                    and assignment.company_id == company_id and assignment.site_id == site_id):
                return True
            if (assignment.scope == RoleAssignment.SCOPE_DEPARTMENT
                    and assignment.company_id == company_id
                    and assignment.department_id == department_id):
                return True
            if (assignment.scope == RoleAssignment.SCOPE_EMPLOYEE
                    and assignment.company_id == company_id
                    and assignment.employee_id == employee_id):
                return True
        return False


def authorization_context(user: Any) -> AuthorizationContext:
    """Resolve all permissions from the authenticated identity and current database state."""
    employee = None
    roles: set[str] = set()
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return AuthorizationContext(user, None, frozenset(), ())

    employee = EmployeeProfile.objects.select_related(
        "company", "site", "department",
    ).filter(user_id=user.pk, is_employee=True).first()
    if employee is not None and (
        not is_employed_on(employee)
        or employee.account_status != EmployeeProfile.ACCOUNT_ACTIVATED
    ):
        return AuthorizationContext(user, None, frozenset(), ())
    if employee:
        roles.add(EMPLOYEE)
        if EmployeeProfile.objects.filter(
            manager_id=employee.pk, company_id=employee.company_id, is_employee=True,
        ).exists():
            roles.add(MANAGER)

    assignments = tuple(
        RoleAssignment.objects.filter(user_id=user.pk, is_active=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .select_related("company", "site", "department", "employee")
        .order_by("pk")
    )
    roles.update(assignment.role for assignment in assignments)
    # Django superusers are trusted system operators, but remain separated from leave data.
    if getattr(user, "is_superuser", False):
        roles.add(ADMIN)
    return AuthorizationContext(user, employee, frozenset(roles), assignments)


def has_permission(context: AuthorizationContext, permission: str, *,
                   company_id: int | None = None, site_id: int | None = None,
                   department_id: int | None = None, employee_id: int | None = None) -> bool:
    if not getattr(context.user, "is_active", False):
        return False

    if permission in {
        MANAGE_SYSTEM_CONFIGURATION, MANAGE_ROLE_ASSIGNMENTS, MANAGE_GLOBAL_LEAVE_TYPES,
    }:
        return context.has_role(ADMIN)
    if permission == READ_LEAVE_TYPES:
        return any(context.has_role(role) for role in {ADMIN, HR, MANAGER, EMPLOYEE})
    if permission == READ_COMPANY_CALENDAR:
        if company_id is not None:
            return context.has_scope(
                HR, company_id=company_id, site_id=site_id, department_id=department_id,
                employee_id=employee_id,
            )
        return any(assignment.role == HR for assignment in context.assignments)
    if permission == READ_TEAM_CALENDAR:
        return context.employee_id is not None and context.has_role(MANAGER)
    if permission in {READ_PERSONAL_CALENDAR, READ_WORK_PATTERNS, READ_LEAVE_POLICY}:
        return context.employee_id is not None
    if permission == READ_WORKDAY_ESTIMATE:
        if employee_id is None:
            return context.employee_id is not None
        if employee_id == context.employee_id:
            return True
        target = EmployeeProfile.objects.filter(pk=employee_id, is_employee=True).values(
            "company_id", "site_id", "department_id",
        ).first()
        if target is None:
            return False
        if context.has_scope(
            HR, company_id=target["company_id"], site_id=target["site_id"],
            department_id=target["department_id"], employee_id=employee_id,
        ):
            return True
        return bool(
            context.employee_id
            and EmployeeProfile.objects.filter(
                pk=employee_id, is_employee=True, manager_id=context.employee_id,
                company_id=context.employee.company_id,
            ).exists()
        )
    return False


def scoped_hr_leave_requests(context: AuthorizationContext) -> QuerySet[LeaveRequest]:
    filters = Q()
    for assignment in context.assignments:
        if assignment.role != HR:
            continue
        if assignment.scope == RoleAssignment.SCOPE_COMPANY:
            filters |= Q(employee__company_id=assignment.company_id)
        elif assignment.scope == RoleAssignment.SCOPE_SITE:
            filters |= Q(
                employee__company_id=assignment.company_id,
                employee__site_id=assignment.site_id,
            )
        elif assignment.scope == RoleAssignment.SCOPE_DEPARTMENT:
            filters |= Q(
                employee__company_id=assignment.company_id,
                employee__department_id=assignment.department_id,
            )
        elif assignment.scope == RoleAssignment.SCOPE_EMPLOYEE:
            filters |= Q(
                employee__company_id=assignment.company_id,
                employee_id=assignment.employee_id,
            )
    return LeaveRequest.objects.filter(filters) if filters else LeaveRequest.objects.none()


def scoped_leave_requests(context: AuthorizationContext) -> QuerySet[LeaveRequest]:
    """Return only individual leave records the actor may see; admins/executives see none."""
    if not getattr(context.user, "is_active", False):
        return LeaveRequest.objects.none()
    filters = Q()
    if context.employee_id:
        filters |= Q(employee_id=context.employee_id)
        filters |= Q(
            manager_snapshot_id=context.employee_id,
            employee__company_id=context.employee.company_id,
        )

    hr_queryset = scoped_hr_leave_requests(context)
    if context.has_role(HR):
        filters |= Q(pk__in=hr_queryset.values("pk"))
    return LeaveRequest.objects.filter(filters) if filters else LeaveRequest.objects.none()


def can_view_leave_request(context: AuthorizationContext, leave_request: LeaveRequest) -> bool:
    return scoped_leave_requests(context).filter(pk=leave_request.pk).exists()


def can_submit_for_employee(
    context: AuthorizationContext,
    employee: EmployeeProfile,
) -> bool:
    if context.employee_id == employee.pk:
        return True
    if context.has_scope(
        HR,
        company_id=employee.company_id,
        site_id=employee.site_id,
        department_id=employee.department_id,
        employee_id=employee.pk,
    ):
        return True
    return bool(
        context.has_role(HR)
        and context.employee_id is not None
        and employee.company_id == context.employee.company_id
        and employee.manager_id == context.employee_id
    )


def can_access_request_attachments(
    context: AuthorizationContext,
    leave_request: LeaveRequest,
) -> bool:
    if context.employee_id == leave_request.employee_id:
        return True
    if context.has_scope(
        HR,
        company_id=leave_request.employee.company_id,
        site_id=leave_request.employee.site_id,
        department_id=leave_request.employee.department_id,
        employee_id=leave_request.employee_id,
    ):
        return True
    return bool(
        context.employee_id == leave_request.manager_snapshot_id
        and context.employee is not None
        and context.employee_id != leave_request.employee_id
        and context.employee.company_id == leave_request.employee.company_id
        and not leave_request.leave_type.confidential
    )


def can_decide_leave_request(context: AuthorizationContext, leave_request: LeaveRequest) -> bool:
    if not can_view_leave_request(context, leave_request):
        return False
    if context.has_scope(
        HR,
        company_id=leave_request.employee.company_id,
        site_id=leave_request.employee.site_id,
        department_id=leave_request.employee.department_id,
        employee_id=leave_request.employee_id,
    ):
        return True
    return bool(
        context.employee_id == leave_request.manager_snapshot_id
        and context.employee is not None
        and leave_request.employee.company_id == context.employee.company_id
        and context.employee_id != leave_request.employee_id
        and not leave_request.leave_type.confidential
    )


def scoped_employees(context: AuthorizationContext) -> QuerySet[EmployeeProfile]:
    """Return the union of self, same-company direct reports, and active HR assignments."""
    if not getattr(context.user, "is_active", False):
        return EmployeeProfile.objects.none()
    filters = Q()
    if context.employee_id:
        filters |= Q(pk=context.employee_id)
    filters |= Q(pk__in=manager_team_employees(context).values("pk"))
    filters |= Q(pk__in=scoped_hr_employees(context).values("pk"))
    return EmployeeProfile.objects.filter(filters, is_employee=True) if filters else (
        EmployeeProfile.objects.none()
    )


def manager_team_employees(context: AuthorizationContext) -> QuerySet[EmployeeProfile]:
    """Return only the current employee's direct reports in the same company."""
    if not getattr(context.user, "is_active", False) or context.employee_id is None:
        return EmployeeProfile.objects.none()
    return EmployeeProfile.objects.filter(
        manager_id=context.employee_id,
        company_id=context.employee.company_id,
        is_employee=True,
    )


def scoped_hr_employees(context: AuthorizationContext) -> QuerySet[EmployeeProfile]:
    """Return only employees inside active HR assignments, excluding personal/team access."""
    if not getattr(context.user, "is_active", False):
        return EmployeeProfile.objects.none()
    filters = Q()
    for assignment in context.assignments:
        if assignment.role != HR:
            continue
        if assignment.scope == RoleAssignment.SCOPE_COMPANY:
            filters |= Q(company_id=assignment.company_id)
        elif assignment.scope == RoleAssignment.SCOPE_SITE:
            filters |= Q(company_id=assignment.company_id, site_id=assignment.site_id)
        elif assignment.scope == RoleAssignment.SCOPE_DEPARTMENT:
            filters |= Q(company_id=assignment.company_id, department_id=assignment.department_id)
        elif assignment.scope == RoleAssignment.SCOPE_EMPLOYEE:
            filters |= Q(company_id=assignment.company_id, pk=assignment.employee_id)
    return EmployeeProfile.objects.filter(filters, is_employee=True) if filters else (
        EmployeeProfile.objects.none()
    )


def scoped_exec_employees(context: AuthorizationContext) -> QuerySet[EmployeeProfile]:
    filters = Q()
    for assignment in context.assignments:
        if assignment.role != "exec":
            continue
        if assignment.scope == RoleAssignment.SCOPE_COMPANY:
            filters |= Q(company_id=assignment.company_id)
        elif assignment.scope == RoleAssignment.SCOPE_SITE:
            filters |= Q(company_id=assignment.company_id, site_id=assignment.site_id)
        elif assignment.scope == RoleAssignment.SCOPE_DEPARTMENT:
            filters |= Q(company_id=assignment.company_id, department_id=assignment.department_id)
    return EmployeeProfile.objects.filter(filters, is_employee=True) if filters else (
        EmployeeProfile.objects.none()
    )


def hr_operation_employees(context: AuthorizationContext) -> QuerySet[EmployeeProfile]:
    """Return effective employee scope for HR operations, or global scope for admins."""
    if not getattr(context.user, "is_active", False):
        return EmployeeProfile.objects.none()
    if context.has_role(ADMIN):
        return EmployeeProfile.objects.filter(is_employee=True)
    if context.has_role(HR):
        return scoped_employees(context)
    return EmployeeProfile.objects.none()
