from __future__ import annotations

import datetime as dt
import logging
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .job_services import scheduler_state
from zsh_core import jalali

logger = logging.getLogger("leave")


def _cache_readiness_check():
    key = f"readiness:{uuid.uuid4().hex}"
    value = uuid.uuid4().hex
    missing = object()
    failure = None

    try:
        cache.set(key, value, timeout=30)
        if cache.get(key, missing) != value:
            raise RuntimeError("Cache probe value could not be read.")
        cache.delete(key)
        if cache.get(key, missing) is not missing:
            raise RuntimeError("Cache probe value was not deleted.")
    except Exception as exc:  # Readiness must report backend failures as not ready.
        failure = exc
    finally:
        try:
            cache.delete(key)
        except Exception as exc:  # A failed cleanup also means the cache is unhealthy.
            failure = failure or exc

    if failure:
        logger.warning(
            "event=readiness_cache_check_failed error_type=%s",
            type(failure).__name__,
        )
        return False
    return True


def _attachment_scanner_readiness_check():
    command = settings.ZSH_ATTACHMENT_SCANNER_COMMAND
    executable = command[0] if command else ""
    return bool(
        settings.ZSH_ATTACHMENT_PRLIMIT
        and executable
        and Path(executable).is_absolute()
        and Path(executable).is_file()
        and os.access(executable, os.X_OK)
    )


@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([])
def health_check(request):
    return JsonResponse({
        "status": "ok",
        "service": "zsh-leave",
        "timestamp": timezone.now().isoformat(),
    })


@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([])
def readiness_check(request):
    checks = []
    database_ok = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        database_ok = True
    except Exception:  # pragma: no cover - defensive check for infra availability.
        database_ok = False
        checks.append({"name": "database", "ok": False, "message": "database unavailable"})
    else:
        checks.append({"name": "database", "ok": True, "message": "database reachable"})

    cache_ok = _cache_readiness_check()
    checks.append({
        "name": "cache",
        "ok": cache_ok,
        "message": "cache operations succeeded" if cache_ok else "cache unavailable",
    })

    scheduler_required = settings.ZSH_JOB_SCHEDULER_REQUIRED or settings.ZSH_JOB_SCHEDULER_ENABLED
    scheduler_ok = True
    if scheduler_required and database_ok:
        state = scheduler_state()
        scheduler_ok = state["status"] == "healthy"
        checks.append({
            "name": "scheduler",
            "ok": scheduler_ok,
            "status": state["status"],
            "message": "scheduler heartbeat current" if scheduler_ok else "scheduler heartbeat unavailable",
        })
    else:
        checks.append({
            "name": "scheduler",
            "ok": True,
            "status": "not_required",
            "message": "scheduler is not required in this environment",
        })

    scanner_required = settings.ZSH_ATTACHMENT_SCANNER_REQUIRED
    scanner_ok = not scanner_required or _attachment_scanner_readiness_check()
    checks.append({
        "name": "attachment_scanner",
        "ok": scanner_ok,
        "message": (
            "scanner command configured"
            if scanner_ok and scanner_required
            else "scanner is not required in this environment"
            if scanner_ok
            else "scanner command unavailable"
        ),
    })

    ready = database_ok and cache_ok and scheduler_ok and scanner_ok
    overall = "ready" if ready else "unavailable"
    status_code = 200 if ready else 503
    return JsonResponse({
        "status": overall,
        "checks": checks,
        "timestamp": timezone.now().isoformat(),
    }, status=status_code)


@api_view(["GET"])
@permission_classes([AllowAny])
def convert_calendar_date(request):
    calendar = request.query_params.get("calendar")
    value = request.query_params.get("date")
    if calendar not in {"jalali", "gregorian"}:
        return Response({
            "code": "invalid_calendar",
            "message_fa": "تقویم باید jalali یا gregorian باشد",
        }, status=400)
    if not value:
        return Response({
            "code": "missing_date",
            "message_fa": "تاریخ الزامی است",
        }, status=400)

    try:
        if calendar == "jalali":
            gregorian_date = jalali.parse_jalali(value)
        else:
            gregorian_date = dt.date.fromisoformat(value)
            if gregorian_date.isoformat() != value:
                raise ValueError("تاریخ میلادی باید به قالب YYYY-MM-DD باشد")
        jalali_date = jalali.format_jalali(gregorian_date)
    except ValueError as exc:
        return Response({
            "code": "invalid_date",
            "message_fa": str(exc),
        }, status=400)

    return Response({
        "gregorian": gregorian_date.isoformat(),
        "jalali": jalali_date,
    })
