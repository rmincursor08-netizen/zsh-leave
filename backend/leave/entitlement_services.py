"""Transactional entitlement journal operations."""
from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Sum

from zsh_core.balance import RequestLine, allocate_by_year, compute_carry_over
from zsh_core.policy import leave_year_of
from zsh_core.settings_schema import build_policy
from . import audit_services
from .models import (
    EntitlementEvent,
    LeaveEntitlement,
    LeaveRequest,
    LeaveType,
)

ZERO = Decimal("0.0")
_DELTA_FIELDS = (
    "base_delta",
    "carry_over_delta",
    "manual_adjustment_delta",
    "reserved_delta",
    "used_delta",
)


class EntitlementLedgerError(ValueError):
    pass


class EntitlementConflict(EntitlementLedgerError):
    pass


class EntitlementReconciliationError(EntitlementLedgerError):
    pass


def _decimal(value) -> Decimal:
    try:
        result = Decimal(str(value))
        rounded = result.quantize(Decimal("0.1"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EntitlementLedgerError("مقدار دفترکل معتبر نیست") from exc
    if not result.is_finite() or result != rounded:
        raise EntitlementLedgerError("مقدار دفترکل باید مضربی از ۰٫۱ باشد")
    return result


def _validate_key(key: str) -> str:
    if not isinstance(key, str) or not key or len(key) > 128:
        raise EntitlementLedgerError("کلید Idempotency الزامی و حداکثر ۱۲۸ نویسه است")
    return key


def _event_key(entitlement_id: int, key: str) -> str:
    value = f"{entitlement_id}:{_validate_key(key)}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def entitlement_event_idempotency_key(entitlement_id: int, key: str) -> str:
    return _event_key(entitlement_id, key)


def _sum_events(entitlement_id: int, request_id: int | None = None) -> dict[str, Decimal]:
    rows = EntitlementEvent.objects.filter(entitlement_id=entitlement_id)
    if request_id is not None:
        rows = rows.filter(request_id=request_id)
    values = rows.aggregate(**{
        field: Sum(field) for field in _DELTA_FIELDS
    })
    return {field: values[field] or ZERO for field in _DELTA_FIELDS}


def entitlement_balance(entitlement: LeaveEntitlement) -> dict[str, Decimal]:
    """Return ledger-derived totals; allocation columns are checked as a projection."""
    totals = _sum_events(entitlement.pk)
    projected = {
        "base_days": entitlement.base_days,
        "carry_over_days": entitlement.carry_over_days,
        "manual_adjustment": entitlement.manual_adjustment,
    }
    journal = {
        "base_days": totals["base_delta"],
        "carry_over_days": totals["carry_over_delta"],
        "manual_adjustment": totals["manual_adjustment_delta"],
    }
    if projected != journal:
        raise EntitlementReconciliationError(
            f"سهمیه {entitlement.pk} با دفترکل تطبیق ندارد"
        )
    total = sum(journal.values(), ZERO)
    used = totals["used_delta"]
    reserved = totals["reserved_delta"]
    return {
        "total": total,
        "used": used,
        "reserved": reserved,
        "remaining": total - used,
        "available": total - used - reserved,
    }


def _snapshot(entitlement: LeaveEntitlement, totals: dict[str, Decimal]) -> dict:
    return {
        "base_days": str(entitlement.base_days),
        "carry_over_days": str(entitlement.carry_over_days),
        "manual_adjustment": str(entitlement.manual_adjustment),
        "reserved_days": str(totals["reserved_delta"]),
        "used_days": str(totals["used_delta"]),
    }


def _append_event(
    *,
    entitlement: LeaveEntitlement,
    kind: str,
    key: str,
    reason: str,
    actor=None,
    request: LeaveRequest | None = None,
    reversal_of: EntitlementEvent | None = None,
    deltas: dict[str, Decimal] | None = None,
    metadata: dict | None = None,
) -> EntitlementEvent:
    deltas = {field: _decimal((deltas or {}).get(field, ZERO)) for field in _DELTA_FIELDS}
    event_key = _event_key(entitlement.pk, key)
    safe_reason = "" if request is not None else reason
    existing = EntitlementEvent.objects.filter(idempotency_key=event_key).first()
    if existing is not None:
        same = (
            existing.entitlement_id == entitlement.pk
            and existing.kind == kind
            and existing.reason == safe_reason
            and existing.request_id == (request.pk if request else None)
            and existing.reversal_of_id == (reversal_of.pk if reversal_of else None)
            and all(getattr(existing, field) == deltas[field] for field in _DELTA_FIELDS)
        )
        if not same:
            raise EntitlementConflict("Idempotency-Key قبلاً برای تغییر دیگری استفاده شده است")
        return existing

    totals = _sum_events(entitlement.pk)
    balance = entitlement_balance(entitlement)
    if balance["reserved"] + deltas["reserved_delta"] < ZERO:
        raise EntitlementConflict("رزرو دفترکل نمی‌تواند منفی شود")
    if balance["used"] + deltas["used_delta"] < ZERO:
        raise EntitlementConflict("مصرف دفترکل نمی‌تواند منفی شود")
    before = _snapshot(entitlement, totals)
    after = {
        "base_days": str(entitlement.base_days + deltas["base_delta"]),
        "carry_over_days": str(entitlement.carry_over_days + deltas["carry_over_delta"]),
        "manual_adjustment": str(
            entitlement.manual_adjustment + deltas["manual_adjustment_delta"]
        ),
        "reserved_days": str(balance["reserved"] + deltas["reserved_delta"]),
        "used_days": str(balance["used"] + deltas["used_delta"]),
    }
    before.update(metadata or {})
    after.update(metadata or {})
    event = EntitlementEvent.objects.create(
        entitlement=entitlement,
        actor=actor,
        kind=kind,
        before=before,
        after=after,
        reason=safe_reason,
        idempotency_key=event_key,
        request=request,
        reversal_of=reversal_of,
        **deltas,
    )
    audit_before = {
        **event.before,
        "entitlementId": entitlement.pk,
        "employeeId": entitlement.employee_id,
        "leaveTypeId": entitlement.leave_type_id,
        "year": entitlement.year,
    }
    audit_after = {
        **event.after,
        "entitlementId": entitlement.pk,
        "employeeId": entitlement.employee_id,
        "leaveTypeId": entitlement.leave_type_id,
        "year": entitlement.year,
    }
    audit_services.record_audit(
        actor=actor,
        origin="system" if actor is None else "user",
        action=f"entitlement.{kind}",
        entity="EntitlementEvent",
        entity_id=event.pk,
        before=audit_before,
        after=audit_after,
        reason=safe_reason,
    )
    allocation_updates = {}
    for field in ("base_days", "carry_over_days", "manual_adjustment"):
        delta_field = {
            "base_days": "base_delta",
            "carry_over_days": "carry_over_delta",
            "manual_adjustment": "manual_adjustment_delta",
        }[field]
        value = getattr(entitlement, field) + deltas[delta_field]
        if field in {"base_days", "carry_over_days"} and value < ZERO:
            raise EntitlementLedgerError(f"{field} نمی‌تواند منفی شود")
        setattr(entitlement, field, value)
        allocation_updates[field] = value
    if allocation_updates:
        entitlement._ledger_write = True
        try:
            entitlement.save(update_fields=list(allocation_updates))
        finally:
            del entitlement._ledger_write
    if kind in {"opening", "adjustment", "carry_forward", "expiration"}:
        from .notification_services import queue_entitlement_event_delivery

        queue_entitlement_event_delivery(event)
    return event


def _locked_entitlements(shares: dict[int, Decimal]) -> dict[int, LeaveEntitlement]:
    ids = sorted(shares)
    rows = list(
        LeaveEntitlement.objects.select_for_update().filter(pk__in=ids).order_by("pk")
    )
    found = {row.pk: row for row in rows}
    if len(found) != len(ids):
        raise EntitlementLedgerError("برای همه سال‌های درخواست سهمیه تعریف نشده است")
    return found


def _request_shares(request: LeaveRequest) -> dict[LeaveEntitlement, Decimal]:
    policy = build_policy(request.policy_version.values) if request.policy_version_id else build_policy({})
    year_of = lambda value: leave_year_of(value, policy)
    shares = allocate_by_year(
        RequestLine(
            status=request.status,
            start=request.start_date,
            end=request.end_date,
            working_days=request.working_days,
        ),
        year_of=year_of,
    )
    entitlements = list(LeaveEntitlement.objects.filter(
        employee_id=request.employee_id,
        leave_type_id=request.leave_type_id,
        year__in=shares,
    ))
    by_year = {item.year: item for item in entitlements}
    missing = set(shares) - set(by_year)
    if missing:
        raise EntitlementLedgerError(
            f"برای سال‌های {', '.join(map(str, sorted(missing)))} سهمیه تعریف نشده است"
        )
    return {by_year[year]: amount for year, amount in shares.items()}


def _request_total(entitlement_id: int, request_id: int, field: str) -> Decimal:
    value = EntitlementEvent.objects.filter(
        entitlement_id=entitlement_id, request_id=request_id,
    ).aggregate(value=Sum(field))["value"]
    return value or ZERO


@transaction.atomic
def create_entitlement(
    *,
    employee,
    leave_type,
    year: int,
    base_days=ZERO,
    carry_over_days=ZERO,
    manual_adjustment=ZERO,
    actor=None,
    reason: str,
    idempotency_key: str,
) -> LeaveEntitlement:
    return _create_entitlement(
        employee=employee,
        leave_type=leave_type,
        year=year,
        base_days=base_days,
        carry_over_days=carry_over_days,
        manual_adjustment=manual_adjustment,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
        reject_existing=False,
    )


@transaction.atomic
def create_entitlement_for_import(
    *,
    employee,
    leave_type,
    year: int,
    base_days,
    actor=None,
    reason: str,
    idempotency_key: str,
) -> LeaveEntitlement:
    return _create_entitlement(
        employee=employee,
        leave_type=leave_type,
        year=year,
        base_days=base_days,
        carry_over_days=ZERO,
        manual_adjustment=ZERO,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
        reject_existing=True,
    )


def _create_entitlement(
    *,
    employee,
    leave_type,
    year: int,
    base_days,
    carry_over_days,
    manual_adjustment,
    actor,
    reason: str,
    idempotency_key: str,
    reject_existing: bool,
) -> LeaveEntitlement:
    leave_type = LeaveType.objects.select_for_update().get(pk=leave_type.pk)
    _validate_key(idempotency_key)
    if not (reason or "").strip():
        raise EntitlementLedgerError("دلیل ایجاد سهمیه الزامی است")
    amounts = {
        "base_days": _decimal(base_days),
        "carry_over_days": _decimal(carry_over_days),
        "manual_adjustment": _decimal(manual_adjustment),
    }
    if amounts["base_days"] < ZERO or amounts["carry_over_days"] < ZERO:
        raise EntitlementLedgerError("سهمیه پایه و انتقالی نمی‌توانند منفی باشند")
    try:
        with transaction.atomic():
            entitlement = LeaveEntitlement.objects.create(
                employee=employee,
                leave_type=leave_type,
                year=year,
                base_days=ZERO,
                carry_over_days=ZERO,
                manual_adjustment=ZERO,
            )
    except IntegrityError:
        entitlement = LeaveEntitlement.objects.select_for_update().filter(
            employee=employee, leave_type=leave_type, year=year,
        ).first()
        if entitlement is None:
            raise
        if reject_existing:
            raise EntitlementConflict("برای این کارمند/نوع/سال قبلاً سهمیه ثبت شده است")
        balance = entitlement_balance(entitlement)
        if (
            balance["total"] == sum(amounts.values(), ZERO)
            and entitlement.base_days == amounts["base_days"]
            and entitlement.carry_over_days == amounts["carry_over_days"]
            and entitlement.manual_adjustment == amounts["manual_adjustment"]
        ):
            return entitlement
        raise EntitlementConflict("برای این کارمند/نوع/سال قبلاً سهمیه ثبت شده است")

    _append_event(
        entitlement=entitlement,
        kind="opening",
        key=idempotency_key,
        reason=reason,
        actor=actor,
        deltas={
            "base_delta": amounts["base_days"],
            "carry_over_delta": amounts["carry_over_days"],
            "manual_adjustment_delta": amounts["manual_adjustment"],
        },
    )
    return entitlement


@transaction.atomic
def adjust_entitlement(
    *,
    entitlement_id: int,
    component: str,
    delta,
    actor,
    reason: str,
    idempotency_key: str,
) -> EntitlementEvent:
    if component not in {"base_days", "carry_over_days", "manual_adjustment"}:
        raise EntitlementLedgerError("مولفه اصلاح سهمیه نامعتبر است")
    reason = (reason or "").strip()
    if not reason:
        raise EntitlementLedgerError("دلیل اصلاح سهمیه الزامی است")
    value = _decimal(delta)
    entitlement = LeaveEntitlement.objects.select_for_update().get(pk=entitlement_id)
    field = {
        "base_days": "base_delta",
        "carry_over_days": "carry_over_delta",
        "manual_adjustment": "manual_adjustment_delta",
    }[component]
    event = _append_event(
        entitlement=entitlement,
        kind="adjustment",
        key=idempotency_key,
        reason=reason,
        actor=actor,
        deltas={field: value},
    )
    return event


def _change_request_bucket(
    request: LeaveRequest,
    *,
    kind: str,
    key: str,
    actor=None,
    reason: str = "",
    reserved_delta=ZERO,
    used_delta=ZERO,
    approval: bool = False,
) -> list[EntitlementEvent]:
    shares = _request_shares(request)
    locked = _locked_entitlements({item.pk: amount for item, amount in shares.items()})
    events = []
    for original, amount in sorted(shares.items(), key=lambda pair: pair[0].pk):
        entitlement = locked[original.pk]
        event_key = _event_key(entitlement.pk, key)
        existing = EntitlementEvent.objects.filter(idempotency_key=event_key).first()
        if existing is not None:
            events.append(_append_event(
                entitlement=entitlement,
                kind=kind,
                key=key,
                reason=reason,
                actor=actor,
                request=request,
                deltas={field: getattr(existing, field) for field in _DELTA_FIELDS},
            ))
            continue
        reserved = _request_total(entitlement.pk, request.pk, "reserved_delta")
        used = _request_total(entitlement.pk, request.pk, "used_delta")
        if approval:
            if reserved not in (ZERO, amount):
                raise EntitlementConflict("رزرو ناقص درخواست قابل تأیید نیست")
            change_reserved = -amount if reserved == amount else ZERO
        else:
            change_reserved = _decimal(reserved_delta) * amount
        change_used = _decimal(used_delta) * amount
        if change_reserved < ZERO and reserved < -change_reserved:
            raise EntitlementConflict("رزرو دفترکل با وضعیت درخواست سازگار نیست")
        if change_used < ZERO and used < -change_used:
            raise EntitlementConflict("مصرف دفترکل با وضعیت درخواست سازگار نیست")
        events.append(_append_event(
            entitlement=entitlement,
            kind=kind,
            key=key,
            reason=reason,
            actor=actor,
            request=request,
            deltas={
                "reserved_delta": change_reserved,
                "used_delta": change_used,
            },
        ))
    return events


@transaction.atomic
def reserve_request(*, request: LeaveRequest, actor=None, reason="", idempotency_key: str):
    _validate_key(idempotency_key)
    if not request.leave_type.counts_against_balance:
        return []
    return _change_request_bucket(
        request, kind="reservation", key=idempotency_key, actor=actor,
        reason=reason, reserved_delta=Decimal("1"),
    )


@transaction.atomic
def approve_request(*, request: LeaveRequest, actor=None, reason="", idempotency_key: str):
    _validate_key(idempotency_key)
    if not request.leave_type.counts_against_balance:
        return []
    return _change_request_bucket(
        request, kind="approval", key=idempotency_key, actor=actor,
        reason=reason,
        used_delta=Decimal("1"),
        approval=True,
    )


@transaction.atomic
def release_request(*, request: LeaveRequest, actor=None, reason="", idempotency_key: str):
    _validate_key(idempotency_key)
    if not request.leave_type.counts_against_balance:
        return []
    shares = _request_shares(request)
    if not any(
        _request_total(entitlement.pk, request.pk, "reserved_delta") > ZERO
        for entitlement in shares
    ):
        return []
    return _change_request_bucket(
        request, kind="release", key=idempotency_key, actor=actor,
        reason=reason, reserved_delta=Decimal("-1"),
    )


@transaction.atomic
def reverse_request(*, request: LeaveRequest, actor=None, reason="", idempotency_key: str):
    _validate_key(idempotency_key)
    if not request.leave_type.counts_against_balance:
        return []
    shares = _request_shares(request)
    if not any(
        _request_total(entitlement.pk, request.pk, "used_delta") > ZERO
        for entitlement in shares
    ):
        return []
    return _change_request_bucket(
        request, kind="reversal", key=idempotency_key, actor=actor,
        reason=reason, used_delta=Decimal("-1"),
    )


@transaction.atomic
def transition_request_ledger(
    *,
    request: LeaveRequest,
    old_status: str,
    new_status: str,
    actor=None,
    reason="",
    idempotency_key: str,
):
    if not request.leave_type.counts_against_balance:
        return []
    key = _validate_key(idempotency_key)
    if new_status in {"PENDING", "ESCALATED"} and old_status == "":
        return reserve_request(
            request=request, actor=actor, reason=reason, idempotency_key=key,
        )
    if new_status == "APPROVED" and old_status in {"", "PENDING", "ESCALATED"}:
        return approve_request(
            request=request, actor=actor, reason=reason, idempotency_key=key,
        )
    if old_status in {"PENDING", "ESCALATED"} and new_status in {"REJECTED", "CANCELLED"}:
        return release_request(
            request=request, actor=actor, reason=reason, idempotency_key=key,
        )
    if old_status in {"APPROVED", "WITHDRAWAL_PENDING"} and new_status == "CANCELLED":
        return reverse_request(
            request=request, actor=actor, reason=reason, idempotency_key=key,
        )
    return []


@transaction.atomic
def reverse_event(*, event_id: int, actor, reason: str, idempotency_key: str) -> EntitlementEvent:
    reason = (reason or "").strip()
    if not reason:
        raise EntitlementLedgerError("دلیل برگشت دفترکل الزامی است")
    original = EntitlementEvent.objects.select_related("entitlement").get(pk=event_id)
    entitlement = LeaveEntitlement.objects.select_for_update().get(pk=original.entitlement_id)
    if original.reversal_of_id is not None:
        raise EntitlementConflict("رویداد برگشت‌خورده را نمی‌توان دوباره برگشت زد")
    existing = EntitlementEvent.objects.filter(reversal_of=original).first()
    if existing:
        if existing.idempotency_key != _event_key(entitlement.pk, idempotency_key):
            raise EntitlementConflict("رویداد قبلاً باطل شده است")
        return _append_event(
            entitlement=entitlement,
            kind="reversal",
            key=idempotency_key,
            reason=reason,
            actor=actor,
            request=original.request,
            reversal_of=original,
            deltas={field: -getattr(original, field) for field in _DELTA_FIELDS},
        )
    event = _append_event(
        entitlement=entitlement,
        kind="reversal",
        key=idempotency_key,
        reason=reason,
        actor=actor,
        request=original.request,
        reversal_of=original,
        deltas={field: -getattr(original, field) for field in _DELTA_FIELDS},
    )
    return event


@transaction.atomic
def carry_forward(
    *,
    previous_entitlement_id: int,
    next_year: int,
    policy: str,
    cap=None,
    actor=None,
    reason: str,
    idempotency_key: str,
) -> LeaveEntitlement:
    previous = LeaveEntitlement.objects.select_for_update().get(pk=previous_entitlement_id)
    if next_year != previous.year + 1:
        raise EntitlementLedgerError("انتقال فقط به سال بعد مجاز است")
    if not (reason or "").strip():
        raise EntitlementLedgerError("دلیل انتقال سهمیه الزامی است")
    balance = entitlement_balance(previous)
    amount = compute_carry_over(balance["available"], policy, _decimal(cap) if cap is not None else None)
    try:
        with transaction.atomic():
            target = LeaveEntitlement.objects.create(
                employee_id=previous.employee_id,
                leave_type_id=previous.leave_type_id,
                year=next_year,
                base_days=ZERO,
                carry_over_days=ZERO,
                manual_adjustment=ZERO,
            )
    except IntegrityError:
        target = LeaveEntitlement.objects.select_for_update().filter(
            employee_id=previous.employee_id,
            leave_type_id=previous.leave_type_id,
            year=next_year,
        ).first()
        if target is None:
            raise
    prior = EntitlementEvent.objects.filter(
        entitlement=target, kind="carry_forward",
        before__source_entitlement_id=previous.pk,
    ).first()
    if prior:
        if prior.carry_over_delta != amount:
            raise EntitlementConflict("انتقال این سال قبلاً با مبلغ دیگری ثبت شده است")
        return target
    _append_event(
        entitlement=target,
        kind="carry_forward",
        key=idempotency_key,
        reason=reason,
        actor=actor,
        deltas={"carry_over_delta": amount},
        metadata={"source_entitlement_id": previous.pk},
    )
    return target


@transaction.atomic
def expire_carry_over(
    *,
    entitlement_id: int,
    actor=None,
    reason: str,
    idempotency_key: str,
) -> EntitlementEvent:
    if not (reason or "").strip():
        raise EntitlementLedgerError("دلیل انقضای سهمیه الزامی است")
    entitlement = LeaveEntitlement.objects.select_for_update().get(pk=entitlement_id)
    balance = entitlement_balance(entitlement)
    amount = min(
        entitlement.carry_over_days,
        max(balance["available"], ZERO),
    )
    event = _append_event(
        entitlement=entitlement,
        kind="expiration",
        key=idempotency_key,
        reason=reason,
        actor=actor,
        deltas={"carry_over_delta": -amount},
    )
    return event


def reconcile_entitlement(entitlement_id: int) -> dict:
    entitlement = LeaveEntitlement.objects.get(pk=entitlement_id)
    rows = reconcile_entitlements(employee_id=entitlement.employee_id)
    return next(row for row in rows if row["entitlement_id"] == entitlement_id)


def reconcile_entitlements(*, employee_id: int | None = None) -> list[dict]:
    entitlements = LeaveEntitlement.objects.all().order_by("pk")
    if employee_id is not None:
        entitlements = entitlements.filter(employee_id=employee_id)
    results = []
    for entitlement in entitlements:
        totals = _sum_events(entitlement.pk)
        projection = {
            "base_days": entitlement.base_days,
            "carry_over_days": entitlement.carry_over_days,
            "manual_adjustment": entitlement.manual_adjustment,
        }
        journal = {
            "base_days": totals["base_delta"],
            "carry_over_days": totals["carry_over_delta"],
            "manual_adjustment": totals["manual_adjustment_delta"],
        }
        request_mismatches = []
        event_mismatches = []
        ledger_kinds = {
            "opening", "reservation", "approval", "release", "reversal",
            "adjustment", "carry_forward", "expiration",
        }
        for event in EntitlementEvent.objects.filter(entitlement=entitlement).order_by("pk"):
            if event.kind not in ledger_kinds:
                continue
            snapshot_fields = {
                "base_days": "base_delta",
                "carry_over_days": "carry_over_delta",
                "manual_adjustment": "manual_adjustment_delta",
                "reserved_days": "reserved_delta",
                "used_days": "used_delta",
            }
            try:
                valid_snapshot = all(
                    Decimal(str(event.after[field]))
                    == Decimal(str(event.before[field])) + getattr(event, delta_field)
                    for field, delta_field in snapshot_fields.items()
                )
            except (KeyError, InvalidOperation, TypeError, ValueError):
                valid_snapshot = False
            if not valid_snapshot:
                event_mismatches.append(event.pk)
        requests = LeaveRequest.objects.filter(
            employee_id=entitlement.employee_id,
            leave_type_id=entitlement.leave_type_id,
            status__in=["PENDING", "ESCALATED", "APPROVED", "WITHDRAWAL_PENDING"],
            leave_type__counts_against_balance=True,
        ).select_related("policy_version", "leave_type")
        expected_reserved = expected_used = ZERO
        for request in requests:
            try:
                share = _request_shares(request).get(entitlement, ZERO)
            except EntitlementLedgerError:
                request_mismatches.append(request.pk)
                continue
            if request.status in {"PENDING", "ESCALATED"}:
                expected_reserved += share
            else:
                expected_used += share
        mismatch = (
            projection != journal
            or totals["reserved_delta"] != expected_reserved
            or totals["used_delta"] != expected_used
            or totals["reserved_delta"] < ZERO
            or totals["used_delta"] < ZERO
            or bool(request_mismatches)
            or bool(event_mismatches)
        )
        results.append({
            "entitlement_id": entitlement.pk,
            "ok": not mismatch,
            "projection": {key: str(value) for key, value in projection.items()},
            "journal": {key: str(value) for key, value in journal.items()},
            "reserved": str(totals["reserved_delta"]),
            "expected_reserved": str(expected_reserved),
            "used": str(totals["used_delta"]),
            "expected_used": str(expected_used),
            "requests_missing_entitlements": request_mismatches,
            "events_with_invalid_snapshots": event_mismatches,
        })
    return results


def internal_idempotency_key(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4().hex}"
