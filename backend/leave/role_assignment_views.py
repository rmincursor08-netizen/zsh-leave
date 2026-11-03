from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .authorization import MANAGE_ROLE_ASSIGNMENTS, authorization_context, has_permission
from .authorization_services import assignment_payload, grant_role_assignment, revoke_role_assignment
from .models import Company, Department, EmployeeProfile, OrganizationSite, RoleAssignment


def _failure(code: str, message: str, status: int, fields: dict | None = None) -> Response:
    body = {"code": code, "message_fa": message}
    if fields:
        body["fields"] = fields
    return Response(body, status=status)


def _positive_id(value, key: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{key} باید شناسه معتبر باشد")
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{key} باید شناسه معتبر باشد")
    return parsed


@api_view(["GET", "POST"])
def role_assignments(request):
    context = authorization_context(request.user)
    if not has_permission(context, MANAGE_ROLE_ASSIGNMENTS):
        return _failure("permission_denied", "مدیریت نقش‌ها فقط برای مدیر سامانه مجاز است", 403)

    if request.method == "GET":
        now = timezone.now()
        rows = RoleAssignment.objects.filter(is_active=True).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now),
        ).select_related("user").order_by("user_id", "role", "pk")
        return Response([assignment_payload(row) for row in rows])

    data = request.data
    allowed = {
        "userId", "role", "scope", "companyId", "siteId", "departmentId", "employeeId",
        "expiresAt",
    }
    if not isinstance(data, dict) or set(data) - allowed:
        return _failure("invalid_payload", "بدنه درخواست یا فیلدهای آن معتبر نیست", 400)
    if not isinstance(data.get("role"), str) or not isinstance(data.get("scope"), str):
        return _failure("invalid_payload", "نقش و محدوده الزامی است", 400)
    try:
        user_id = _positive_id(data.get("userId"), "userId")
        ids = {
            key: _positive_id(data[key], key) if data.get(key) is not None else None
            for key in ("companyId", "siteId", "departmentId", "employeeId")
        }
        expires_at = None
        if data.get("expiresAt") is not None:
            if not isinstance(data["expiresAt"], str):
                raise ValueError("expiresAt باید زمان ISO-8601 باشد")
            expires_at = parse_datetime(data["expiresAt"])
            if expires_at is None or timezone.is_naive(expires_at):
                raise ValueError("expiresAt باید زمان ISO-8601 همراه منطقه زمانی باشد")
        assignment = grant_role_assignment(
            actor=request.user,
            user_id=user_id,
            role=data["role"],
            scope=data["scope"],
            company_id=ids["companyId"],
            site_id=ids["siteId"],
            department_id=ids["departmentId"],
            employee_id=ids["employeeId"],
            expires_at=expires_at,
        )
    except (TypeError, ValueError) as exc:
        return _failure("invalid_payload", str(exc), 400)
    except (
        get_user_model().DoesNotExist,
        Company.DoesNotExist,
        Department.DoesNotExist,
        EmployeeProfile.DoesNotExist,
        OrganizationSite.DoesNotExist,
    ) as exc:
        raise Http404 from exc
    except DjangoValidationError as exc:
        fields = getattr(exc, "message_dict", {"fields": exc.messages})
        return _failure("invalid_assignment", "اطلاعات تخصیص نقش معتبر نیست", 400, fields)
    except IntegrityError:
        return _failure("duplicate_assignment", "این تخصیص نقش از قبل فعال است", 409)
    return Response(assignment_payload(assignment), status=201)


@api_view(["DELETE"])
def role_assignment_detail(request, assignment_id: int):
    context = authorization_context(request.user)
    if not has_permission(context, MANAGE_ROLE_ASSIGNMENTS):
        return _failure("permission_denied", "مدیریت نقش‌ها فقط برای مدیر سامانه مجاز است", 403)
    try:
        revoke_role_assignment(actor=request.user, assignment_id=assignment_id)
    except DjangoValidationError as exc:
        fields = getattr(exc, "message_dict", {"fields": exc.messages})
        return _failure("invalid_assignment", "لغو تخصیص نقش مجاز نیست", 400, fields)
    return Response(status=204)
