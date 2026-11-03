from django.db import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .authorization import authorization_context, hr_operation_employees
from .invitation_services import (
    InvitationError,
    activate_invitation,
    invite_employee,
    revoke_invitation,
    suspend_employee,
    synchronize_employment_access,
)
from .models import EmployeeProfile
from .throttling import rate_limited


def _employee_for_operator(request, employee_id: int) -> EmployeeProfile:
    context = authorization_context(request.user)
    if context.has_role("admin"):
        queryset = EmployeeProfile.objects.all()
    elif context.has_role("hr"):
        queryset = hr_operation_employees(context)
    else:
        raise PermissionError
    try:
        return queryset.select_related("user").get(pk=employee_id)
    except EmployeeProfile.DoesNotExist as exc:
        raise Http404 from exc


def _reason(request):
    data = request.data
    if not isinstance(data, dict) or set(data) != {"reason"}:
        return None
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        return None
    return reason.strip()


@rate_limited("employee_invitation")
@api_view(["POST"])
def employee_invitation(request, employee_id: int):
    try:
        employee = _employee_for_operator(request, employee_id)
    except PermissionError:
        return Response(
            {"code": "permission_denied", "message_fa": "ارسال دعوت مجاز نیست"},
            status=status.HTTP_403_FORBIDDEN,
        )
    if not isinstance(request.data, dict) or set(request.data) - {"email"}:
        return Response(
            {"code": "invalid_request", "message_fa": "درخواست معتبر نیست"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        synchronize_employment_access(employee=employee, actor=request.user)
        result = invite_employee(
            employee_id=employee.pk,
            actor=request.user,
            email=request.data.get("email"),
        )
    except InvitationError as exc:
        if exc.code == "email_in_use":
            return Response(
                {"code": "duplicate_email", "message_fa": "این ایمیل قبلاً برای حساب دیگری ثبت شده است"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {"code": "invalid_invitation_request", "message_fa": "ارسال دعوت ممکن نیست"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except IntegrityError:
        return Response(
            {"code": "invitation_conflict", "message_fa": "درخواست دعوت تکراری است"},
            status=status.HTTP_409_CONFLICT,
        )

    response_data = {
        "status": result.status,
        "message_fa": (
            "دعوت ارسال شد" if result.status == "sent"
            else "دعوت ذخیره شد اما سرویس ایمیل در دسترس نیست"
        ),
    }
    if result.development_url:
        response_data["developmentActivationUrl"] = result.development_url
    return Response(
        response_data,
        status=status.HTTP_200_OK if result.status == "sent" else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@rate_limited("activation")
@api_view(["POST"])
@permission_classes([AllowAny])
def activate_account(request):
    data = request.data
    if (
        not isinstance(data, dict)
        or set(data) != {"token", "email", "password"}
    ):
        error_code = activate_invitation(raw_token=None, email=None, password=None)
    else:
        error_code = activate_invitation(
            raw_token=data.get("token"),
            email=data.get("email"),
            password=data.get("password"),
        )

    if error_code == "password_invalid":
        return Response(
            {
                "code": "password_invalid",
                "message_fa": "رمز عبور با سیاست امنیتی سامانه مطابقت ندارد",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if error_code:
        return Response(
            {
                "code": "invalid_invitation",
                "message_fa": "دعوت معتبر نیست یا منقضی شده است",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(
        {"message_fa": "حساب فعال شد؛ اکنون می‌توانید وارد شوید"},
        status=status.HTTP_200_OK,
    )


@rate_limited("employee_invitation")
@api_view(["POST"])
def revoke_employee_invitation(request, employee_id: int):
    try:
        employee = _employee_for_operator(request, employee_id)
    except PermissionError:
        return Response(
            {"code": "permission_denied", "message_fa": "لغو دعوت مجاز نیست"},
            status=status.HTTP_403_FORBIDDEN,
        )
    reason = _reason(request)
    if reason is None:
        return Response(
            {"code": "invalid_reason", "message_fa": "دلیل لغو الزامی و حداکثر ۵۰۰ نویسه است"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    changed = revoke_invitation(employee_id=employee.pk, actor=request.user, reason=reason)
    return Response(
        {"status": "revoked" if changed else "unchanged"},
        status=status.HTTP_200_OK,
    )


@rate_limited("employee_invitation")
@api_view(["POST"])
def suspend_employee_account(request, employee_id: int):
    try:
        employee = _employee_for_operator(request, employee_id)
    except PermissionError:
        return Response(
            {"code": "permission_denied", "message_fa": "تعلیق حساب مجاز نیست"},
            status=status.HTTP_403_FORBIDDEN,
        )
    reason = _reason(request)
    if reason is None:
        return Response(
            {"code": "invalid_reason", "message_fa": "دلیل تعلیق الزامی و حداکثر ۵۰۰ نویسه است"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    suspend_employee(employee_id=employee.pk, actor=request.user, reason=reason)
    return Response({"status": EmployeeProfile.ACCOUNT_SUSPENDED}, status=status.HTTP_200_OK)
