from datetime import timedelta
import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.payouts.models import IdempotencyRecord, Payout
from apps.payouts.services import (
    complete_payout,
    fail_payout,
    mark_retry_due,
    record_settlement_attempt,
    StaleSettlementAttemptError,
    transition_to_processing,
)
from apps.payouts.settlement import SettlementSimulator, default_simulator


logger = logging.getLogger(__name__)


def enqueue_payout_processing(payout_id: int) -> bool:
    try:
        process_payout.delay(payout_id)
    except Exception:
        logger.exception("Failed to enqueue payout processing for payout_id=%s", payout_id)
        return False
    return True


@shared_task
def process_payout(payout_id: int) -> None:
    with transaction.atomic():
        payout = Payout.objects.select_for_update().get(pk=payout_id)
        if payout.status in [Payout.Status.COMPLETED, Payout.Status.FAILED]:
            return

        now = timezone.now()
        if payout.status == Payout.Status.PENDING:
            payout = transition_to_processing(payout)
        elif payout.status == Payout.Status.PROCESSING:
            if payout.next_retry_at is None or payout.next_retry_at > now:
                return
        else:
            return

        if payout.attempt_count >= payout.max_attempts:
            fail_payout(payout, error="Max settlement attempts exceeded.")
            return

        payout = record_settlement_attempt(payout)
        attempt_count = payout.attempt_count

    outcome = default_simulator.settle(payout)
    try:
        if outcome.status == SettlementSimulator.SUCCESS:
            complete_payout(
                payout,
                settlement_reference=outcome.reference,
                expected_attempt_count=attempt_count,
            )
        elif outcome.status == SettlementSimulator.FAILURE:
            fail_payout(
                payout,
                error=outcome.error,
                expected_attempt_count=attempt_count,
            )
    except StaleSettlementAttemptError:
        logger.info(
            "Ignored stale settlement outcome for payout_id=%s attempt=%s",
            payout_id,
            attempt_count,
        )


@shared_task
def enqueue_pending_payouts() -> int:
    payout_ids = list(
        Payout.objects.filter(status=Payout.Status.PENDING)
        .order_by("created_at")
        .values_list("id", flat=True)[:100]
    )
    enqueued = 0
    for payout_id in payout_ids:
        if enqueue_payout_processing(payout_id):
            enqueued += 1
    return enqueued


@shared_task
def retry_stuck_payouts() -> int:
    now = timezone.now()
    cutoff = now - timedelta(seconds=settings.PAYOUT_STUCK_AFTER_SECONDS)
    enqueued = 0

    with transaction.atomic():
        stuck = (
            Payout.objects.select_for_update(skip_locked=True)
            .filter(
                status=Payout.Status.PROCESSING,
                processing_started_at__lt=cutoff,
            )
            .order_by("id")[:100]
        )
        for payout in stuck:
            if payout.attempt_count >= payout.max_attempts:
                fail_payout(payout, error="Max settlement attempts exceeded.")
                continue
            if payout.next_retry_at is None:
                mark_retry_due(payout)
                continue
            if payout.next_retry_at <= now:
                if enqueue_payout_processing(payout.id):
                    enqueued += 1
    return enqueued


@shared_task
def cleanup_expired_idempotency_records() -> int:
    deleted, _ = IdempotencyRecord.objects.filter(expires_at__lt=timezone.now()).delete()
    return deleted
