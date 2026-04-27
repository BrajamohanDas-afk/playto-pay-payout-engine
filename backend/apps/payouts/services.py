from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status

from apps.ledger.models import LedgerEntry
from apps.merchants.models import MerchantBalance
from apps.payouts.models import IdempotencyRecord, Payout
from apps.payouts.serializers import PayoutSerializer


class PayoutError(Exception):
    code = "payout_error"


class InsufficientFundsError(PayoutError):
    code = "insufficient_funds"


class IdempotencyInProgressError(PayoutError):
    code = "request_in_progress"


class IdempotencyKeyReusedError(PayoutError):
    code = "idempotency_key_reused"


class InvalidTransitionError(PayoutError):
    code = "invalid_payout_transition"


class StaleSettlementAttemptError(PayoutError):
    code = "stale_settlement_attempt"


def hash_idempotency_request(method: str, path: str, body: dict) -> str:
    payload = {
        "method": method.upper(),
        "path": path,
        "body": body,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def create_payout_with_idempotency(
    *,
    merchant,
    idempotency_key: str,
    request_hash: str,
    amount_paise: int,
    bank_account_id: str,
) -> tuple[int, dict, Payout | None, bool]:
    expires_at = timezone.now() + timedelta(hours=24)

    with transaction.atomic():
        record, created = _acquire_idempotency_record(
            merchant=merchant,
            key=idempotency_key,
            request_hash=request_hash,
            expires_at=expires_at,
        )
        if not created and record.expires_at <= timezone.now():
            record.delete()
            record = IdempotencyRecord.objects.create(
                merchant=merchant,
                key=idempotency_key,
                request_hash=request_hash,
                expires_at=expires_at,
            )
            created = True

        if record.request_hash != request_hash:
            raise IdempotencyKeyReusedError()

        if record.status == IdempotencyRecord.Status.COMPLETED:
            return record.response_code, record.response_body, None, True

        if record.response_body is not None:
            return record.response_code, record.response_body, None, True

        if not created:
            raise IdempotencyInProgressError()

        balance = MerchantBalance.objects.select_for_update().get(merchant=merchant)
        if balance.available_paise < amount_paise:
            response_body = {"error": "insufficient_funds"}
            record.status = IdempotencyRecord.Status.COMPLETED
            record.response_code = status.HTTP_409_CONFLICT
            record.response_body = response_body
            record.save(
                update_fields=["status", "response_code", "response_body", "updated_at"]
            )
            return status.HTTP_409_CONFLICT, response_body, None, False

        MerchantBalance.objects.filter(pk=balance.pk).update(
            available_paise=F("available_paise") - amount_paise,
            held_paise=F("held_paise") + amount_paise,
        )
        payout = Payout.objects.create(
            merchant=merchant,
            amount_paise=amount_paise,
            bank_account_id=bank_account_id,
        )
        LedgerEntry.objects.create(
            merchant=merchant,
            payout=payout,
            direction=LedgerEntry.Direction.DEBIT,
            entry_type=LedgerEntry.EntryType.PAYOUT_HOLD,
            status=LedgerEntry.Status.HELD,
            amount_paise=amount_paise,
            description="Funds held for payout request.",
        )

        response_body = PayoutSerializer(payout).data
        record.status = IdempotencyRecord.Status.COMPLETED
        record.response_code = status.HTTP_201_CREATED
        record.response_body = response_body
        record.save(update_fields=["status", "response_code", "response_body", "updated_at"])
        return status.HTTP_201_CREATED, response_body, payout, False


def _acquire_idempotency_record(*, merchant, key, request_hash, expires_at):
    try:
        with transaction.atomic():
            record = IdempotencyRecord.objects.create(
                merchant=merchant,
                key=key,
                request_hash=request_hash,
                expires_at=expires_at,
            )
        return record, True
    except IntegrityError:
        record = IdempotencyRecord.objects.select_for_update().get(
            merchant=merchant,
            key=key,
        )
        return record, False


def transition_to_processing(payout: Payout) -> Payout:
    if payout.status != Payout.Status.PENDING:
        raise InvalidTransitionError(f"Cannot move {payout.status} to processing")
    payout.status = Payout.Status.PROCESSING
    payout.processing_started_at = timezone.now()
    payout.save(update_fields=["status", "processing_started_at", "updated_at"])
    return payout


def record_settlement_attempt(payout: Payout) -> Payout:
    payout.attempt_count = F("attempt_count") + 1
    payout.processing_started_at = timezone.now()
    payout.next_retry_at = None
    payout.save(
        update_fields=[
            "attempt_count",
            "processing_started_at",
            "next_retry_at",
            "updated_at",
        ]
    )
    payout.refresh_from_db()
    return payout


def complete_payout(
    payout: Payout,
    *,
    settlement_reference: str | None = None,
    expected_attempt_count: int | None = None,
) -> Payout:
    with transaction.atomic():
        payout = Payout.objects.select_for_update().get(pk=payout.pk)
        if payout.status != Payout.Status.PROCESSING:
            raise InvalidTransitionError(f"Cannot complete payout from {payout.status}")
        if (
            expected_attempt_count is not None
            and payout.attempt_count != expected_attempt_count
        ):
            raise StaleSettlementAttemptError(
                f"Cannot complete stale attempt {expected_attempt_count}; "
                f"current attempt is {payout.attempt_count}"
            )

        balance = MerchantBalance.objects.select_for_update().get(merchant=payout.merchant)
        MerchantBalance.objects.filter(pk=balance.pk).update(
            held_paise=F("held_paise") - payout.amount_paise,
            total_debited_paise=F("total_debited_paise") + payout.amount_paise,
        )
        LedgerEntry.objects.filter(
            payout=payout,
            entry_type=LedgerEntry.EntryType.PAYOUT_HOLD,
            status=LedgerEntry.Status.HELD,
        ).update(status=LedgerEntry.Status.SETTLED)

        payout.status = Payout.Status.COMPLETED
        payout.settlement_reference = settlement_reference or f"settle_{uuid4().hex[:16]}"
        payout.completed_at = timezone.now()
        payout.last_error = ""
        payout.save(
            update_fields=[
                "status",
                "settlement_reference",
                "completed_at",
                "last_error",
                "updated_at",
            ]
        )
        return payout


def fail_payout(
    payout: Payout,
    *,
    error: str,
    expected_attempt_count: int | None = None,
) -> Payout:
    with transaction.atomic():
        payout = Payout.objects.select_for_update().get(pk=payout.pk)
        if payout.status != Payout.Status.PROCESSING:
            raise InvalidTransitionError(f"Cannot fail payout from {payout.status}")
        if (
            expected_attempt_count is not None
            and payout.attempt_count != expected_attempt_count
        ):
            raise StaleSettlementAttemptError(
                f"Cannot fail stale attempt {expected_attempt_count}; "
                f"current attempt is {payout.attempt_count}"
            )

        balance = MerchantBalance.objects.select_for_update().get(merchant=payout.merchant)
        MerchantBalance.objects.filter(pk=balance.pk).update(
            available_paise=F("available_paise") + payout.amount_paise,
            held_paise=F("held_paise") - payout.amount_paise,
        )
        LedgerEntry.objects.filter(
            payout=payout,
            entry_type=LedgerEntry.EntryType.PAYOUT_HOLD,
            status=LedgerEntry.Status.HELD,
        ).update(status=LedgerEntry.Status.REVERSED)
        LedgerEntry.objects.create(
            merchant=payout.merchant,
            payout=payout,
            direction=LedgerEntry.Direction.CREDIT,
            entry_type=LedgerEntry.EntryType.PAYOUT_REVERSAL,
            status=LedgerEntry.Status.REVERSED,
            amount_paise=payout.amount_paise,
            description="Held payout funds returned after failed settlement.",
        )

        payout.status = Payout.Status.FAILED
        payout.failed_at = timezone.now()
        payout.last_error = error
        payout.save(update_fields=["status", "failed_at", "last_error", "updated_at"])
        return payout


def mark_retry_due(payout: Payout) -> Payout:
    delay_seconds = settings.PAYOUT_RETRY_BASE_SECONDS * (
        2 ** max(payout.attempt_count - 1, 0)
    )
    payout.next_retry_at = timezone.now() + timedelta(seconds=delay_seconds)
    payout.last_error = "Settlement attempt hung; retry scheduled."
    payout.save(update_fields=["next_retry_at", "last_error", "updated_at"])
    return payout
