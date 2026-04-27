from concurrent.futures import ThreadPoolExecutor, wait
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection, connections, transaction
from django.db.models import F
from django.utils import timezone

from apps.ledger.invariants import check_balance_invariant
from apps.ledger.models import LedgerEntry
from apps.merchants.models import Merchant, MerchantBalance
from apps.payouts.models import IdempotencyRecord, Payout
from apps.payouts.services import (
    IdempotencyInProgressError,
    IdempotencyKeyReusedError,
    InvalidTransitionError,
    StaleSettlementAttemptError,
    complete_payout,
    create_payout_with_idempotency,
    fail_payout,
    hash_idempotency_request,
    record_settlement_attempt,
    transition_to_processing,
)
from apps.payouts.settlement import SettlementOutcome, SettlementSimulator
from apps.payouts.tasks import process_payout
from apps.payouts.tasks import retry_stuck_payouts


def request_hash(amount=25_000, bank_account_id="bank_test_001"):
    return hash_idempotency_request(
        "POST",
        "/api/v1/payouts",
        {"amount_paise": amount, "bank_account_id": bank_account_id},
    )


@pytest.mark.django_db
def test_create_payout_holds_funds_and_records_idempotency(merchant):
    code, body, payout, replayed = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-1",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )

    merchant.balance.refresh_from_db()
    assert code == 201
    assert replayed is False
    assert body["id"] == payout.id
    assert merchant.balance.available_paise == 125_000
    assert merchant.balance.held_paise == 25_000
    assert LedgerEntry.objects.filter(payout=payout, status=LedgerEntry.Status.HELD).exists()
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_duplicate_idempotency_key_replays_exact_response(merchant):
    first = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-2",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )
    second = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-2",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )

    assert second[0] == first[0]
    assert second[1] == first[1]
    assert second[3] is True
    assert Payout.objects.count() == 1
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_same_idempotency_key_with_different_payload_is_rejected(merchant):
    create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-3",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )

    with pytest.raises(IdempotencyKeyReusedError):
        create_payout_with_idempotency(
            merchant=merchant,
            idempotency_key="key-3",
            request_hash=request_hash(amount=30_000),
            amount_paise=30_000,
            bank_account_id="bank_test_001",
        )
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_expired_idempotency_key_can_be_reused(merchant):
    create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-expired",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )
    record = IdempotencyRecord.objects.get(merchant=merchant, key="key-expired")
    record.expires_at = timezone.now() - timedelta(seconds=1)
    record.save(update_fields=["expires_at"])

    code, body, payout, replayed = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-expired",
        request_hash=request_hash(amount=10_000),
        amount_paise=10_000,
        bank_account_id="bank_test_001",
    )

    assert code == 201
    assert replayed is False
    assert body["id"] == payout.id
    assert Payout.objects.count() == 2
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_in_progress_duplicate_is_rejected(merchant):
    IdempotencyRecord.objects.create(
        merchant=merchant,
        key="key-in-flight",
        request_hash=request_hash(),
        expires_at=timezone.now() + timedelta(hours=24),
    )

    with pytest.raises(IdempotencyInProgressError):
        create_payout_with_idempotency(
            merchant=merchant,
            idempotency_key="key-in-flight",
            request_hash=request_hash(),
            amount_paise=25_000,
            bank_account_id="bank_test_001",
        )
    assert Payout.objects.count() == 0
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_insufficient_funds_returns_stored_409_response(merchant):
    code, body, payout, replayed = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-low-funds",
        request_hash=request_hash(amount=999_999),
        amount_paise=999_999,
        bank_account_id="bank_test_001",
    )

    assert code == 409
    assert body == {"error": "insufficient_funds"}
    assert payout is None
    assert replayed is False
    assert Payout.objects.count() == 0
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_successful_payout_settles_held_funds(merchant):
    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-success",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )
    transition_to_processing(payout)
    complete_payout(payout, settlement_reference="bank_ok")

    merchant.balance.refresh_from_db()
    payout.refresh_from_db()
    assert payout.status == Payout.Status.COMPLETED
    assert merchant.balance.available_paise == 125_000
    assert merchant.balance.held_paise == 0
    assert merchant.balance.total_debited_paise == 25_000
    assert LedgerEntry.objects.get(payout=payout).status == LedgerEntry.Status.SETTLED
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_failed_payout_releases_held_funds(merchant):
    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-fail",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )
    transition_to_processing(payout)
    fail_payout(payout, error="bank rejected")

    merchant.balance.refresh_from_db()
    payout.refresh_from_db()
    assert payout.status == Payout.Status.FAILED
    assert merchant.balance.available_paise == 150_000
    assert merchant.balance.held_paise == 0
    assert LedgerEntry.objects.filter(
        payout=payout,
        entry_type=LedgerEntry.EntryType.PAYOUT_REVERSAL,
        status=LedgerEntry.Status.REVERSED,
    ).exists()
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_completed_payout_cannot_fail(merchant):
    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-transition",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )
    transition_to_processing(payout)
    complete_payout(payout, settlement_reference="bank_ok")

    with pytest.raises(InvalidTransitionError):
        fail_payout(payout, error="too late")
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_stale_settlement_attempt_cannot_complete_newer_retry_attempt(merchant):
    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-stale-complete",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )
    transition_to_processing(payout)
    payout = record_settlement_attempt(payout)
    stale_attempt_count = payout.attempt_count

    payout.attempt_count = F("attempt_count") + 1
    payout.save(update_fields=["attempt_count"])
    payout.refresh_from_db()

    with pytest.raises(StaleSettlementAttemptError):
        complete_payout(
            payout,
            settlement_reference="late_success",
            expected_attempt_count=stale_attempt_count,
        )

    merchant.balance.refresh_from_db()
    payout.refresh_from_db()
    assert payout.status == Payout.Status.PROCESSING
    assert payout.attempt_count == stale_attempt_count + 1
    assert merchant.balance.held_paise == 25_000
    assert merchant.balance.total_debited_paise == 0
    assert LedgerEntry.objects.get(payout=payout).status == LedgerEntry.Status.HELD
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_process_payout_ignores_stale_success_after_newer_attempt_starts(merchant):
    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-task-stale-success",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )

    def settle_after_newer_attempt_started(active_payout):
        Payout.objects.filter(pk=active_payout.pk).update(
            attempt_count=F("attempt_count") + 1
        )
        return SettlementOutcome(
            status=SettlementSimulator.SUCCESS,
            reference="late_success",
        )

    with patch("apps.payouts.tasks.default_simulator.settle") as settle:
        settle.side_effect = settle_after_newer_attempt_started
        process_payout(payout.id)

    merchant.balance.refresh_from_db()
    payout.refresh_from_db()
    assert payout.status == Payout.Status.PROCESSING
    assert payout.attempt_count == 2
    assert payout.settlement_reference == ""
    assert merchant.balance.held_paise == 25_000
    assert merchant.balance.total_debited_paise == 0
    assert LedgerEntry.objects.get(payout=payout).status == LedgerEntry.Status.HELD
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_stuck_processing_payout_schedules_retry(merchant):
    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-stuck",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )
    transition_to_processing(payout)
    payout.attempt_count = 1
    payout.processing_started_at = timezone.now() - timedelta(seconds=31)
    payout.save(update_fields=["attempt_count", "processing_started_at"])

    enqueued = retry_stuck_payouts()

    payout.refresh_from_db()
    assert enqueued == 0
    assert payout.status == Payout.Status.PROCESSING
    assert payout.next_retry_at is not None
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_stuck_processing_payout_fails_after_max_attempts(merchant):
    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-max-attempts",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )
    transition_to_processing(payout)
    payout.attempt_count = payout.max_attempts
    payout.processing_started_at = timezone.now() - timedelta(seconds=31)
    payout.save(update_fields=["attempt_count", "processing_started_at"])

    retry_stuck_payouts()

    merchant.balance.refresh_from_db()
    payout.refresh_from_db()
    assert payout.status == Payout.Status.FAILED
    assert merchant.balance.available_paise == 150_000
    assert merchant.balance.held_paise == 0
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_duplicate_process_task_does_not_double_settle_active_attempt(merchant):
    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-worker-duplicate",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )

    with patch("apps.payouts.tasks.default_simulator.settle") as settle:
        settle.return_value = SettlementOutcome(status=SettlementSimulator.HANG)
        process_payout(payout.id)
        process_payout(payout.id)

    payout.refresh_from_db()
    assert settle.call_count == 1
    assert payout.status == Payout.Status.PROCESSING
    assert payout.attempt_count == 1
    assert payout.next_retry_at is None
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_due_retry_duplicate_task_only_attempts_once(merchant):
    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-retry-duplicate",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_test_001",
    )
    transition_to_processing(payout)
    payout.attempt_count = 1
    payout.processing_started_at = timezone.now() - timedelta(seconds=31)
    payout.next_retry_at = timezone.now() - timedelta(seconds=1)
    payout.save(update_fields=["attempt_count", "processing_started_at", "next_retry_at"])

    with patch("apps.payouts.tasks.default_simulator.settle") as settle:
        settle.return_value = SettlementOutcome(status=SettlementSimulator.HANG)
        process_payout(payout.id)
        process_payout(payout.id)

    payout.refresh_from_db()
    assert settle.call_count == 1
    assert payout.attempt_count == 2
    assert payout.next_retry_at is None
    check_balance_invariant(merchant)


def test_simulator_forced_success_mode(settings):
    settings.PAYOUT_SIMULATOR_MODE = "always_success"

    outcome = SettlementSimulator().settle(
        SimpleNamespace(bank_account_id="bank_demo_anything")
    )

    assert outcome.status == SettlementSimulator.SUCCESS
    assert outcome.reference.startswith("bank_")
    assert outcome.error == ""


def test_simulator_by_bank_account_markers(settings):
    settings.PAYOUT_SIMULATOR_MODE = "by_bank_account"
    simulator = SettlementSimulator()

    assert (
        simulator.settle(SimpleNamespace(bank_account_id="bank_success_demo")).status
        == SettlementSimulator.SUCCESS
    )
    assert (
        simulator.settle(SimpleNamespace(bank_account_id="bank_fail_demo")).status
        == SettlementSimulator.FAILURE
    )
    assert (
        simulator.settle(SimpleNamespace(bank_account_id="bank_hang_demo")).status
        == SettlementSimulator.HANG
    )


@pytest.mark.django_db
def test_process_payout_uses_configured_success_mode(settings, merchant):
    settings.PAYOUT_SIMULATOR_MODE = "always_success"
    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-success-mode",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_demo_anything",
    )

    process_payout(payout.id)

    merchant.balance.refresh_from_db()
    payout.refresh_from_db()
    assert payout.status == Payout.Status.COMPLETED
    assert payout.attempt_count == 1
    assert merchant.balance.available_paise == 125_000
    assert merchant.balance.held_paise == 0
    check_balance_invariant(merchant)


@pytest.mark.django_db(transaction=True)
def test_concurrent_payouts_cannot_overdraw_postgres(merchant):
    if connection.vendor != "postgresql":
        pytest.skip("requires PostgreSQL row-level locks")

    merchant_id = merchant.id
    amount_paise = 100_000
    start_barrier = Barrier(3)

    def create_competing_payout(key, bank_account_id):
        connections.close_all()
        try:
            local_merchant = Merchant.objects.get(pk=merchant_id)
            start_barrier.wait(timeout=5)
            return create_payout_with_idempotency(
                merchant=local_merchant,
                idempotency_key=key,
                request_hash=request_hash(
                    amount=amount_paise,
                    bank_account_id=bank_account_id,
                ),
                amount_paise=amount_paise,
                bank_account_id=bank_account_id,
            )
        finally:
            connections.close_all()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with transaction.atomic():
            MerchantBalance.objects.select_for_update().get(merchant_id=merchant_id)
            futures = [
                executor.submit(create_competing_payout, "key-race-a", "bank_race_a"),
                executor.submit(create_competing_payout, "key-race-b", "bank_race_b"),
            ]
            start_barrier.wait(timeout=5)

        done, not_done = wait(futures, timeout=10)
        assert len(done) == 2
        assert not not_done

        results = [future.result() for future in futures]
    finally:
        executor.shutdown(cancel_futures=True)

    success_count = sum(code == 201 for code, _, _, _ in results)
    insufficient_count = sum(
        code == 409 and body == {"error": "insufficient_funds"}
        for code, body, _, _ in results
    )
    balance = MerchantBalance.objects.get(merchant_id=merchant_id)

    assert success_count == 1
    assert insufficient_count == 1
    assert Payout.objects.filter(merchant_id=merchant_id).count() == 1
    assert balance.available_paise == 50_000
    assert balance.held_paise == 100_000
    check_balance_invariant(Merchant.objects.get(pk=merchant_id))


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_idempotency_key_replays_one_payout_postgres(merchant):
    if connection.vendor != "postgresql":
        pytest.skip("requires PostgreSQL row-level locks")

    merchant_id = merchant.id
    amount_paise = 25_000
    bank_account_id = "bank_duplicate_race"
    idempotency_key = "key-duplicate-race"
    shared_request_hash = request_hash(
        amount=amount_paise,
        bank_account_id=bank_account_id,
    )
    start_barrier = Barrier(3)

    def create_duplicate_request():
        connections.close_all()
        try:
            local_merchant = Merchant.objects.get(pk=merchant_id)
            start_barrier.wait(timeout=5)
            return create_payout_with_idempotency(
                merchant=local_merchant,
                idempotency_key=idempotency_key,
                request_hash=shared_request_hash,
                amount_paise=amount_paise,
                bank_account_id=bank_account_id,
            )
        finally:
            connections.close_all()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with transaction.atomic():
            MerchantBalance.objects.select_for_update().get(merchant_id=merchant_id)
            futures = [
                executor.submit(create_duplicate_request),
                executor.submit(create_duplicate_request),
            ]
            start_barrier.wait(timeout=5)

        done, not_done = wait(futures, timeout=10)
        assert len(done) == 2
        assert not not_done

        results = [future.result() for future in futures]
    finally:
        executor.shutdown(cancel_futures=True)

    response_codes = [code for code, _, _, _ in results]
    response_bodies = [body for _, body, _, _ in results]
    replayed_flags = [replayed for _, _, _, replayed in results]
    created_payouts = [payout for _, _, payout, _ in results if payout is not None]
    balance = MerchantBalance.objects.get(merchant_id=merchant_id)

    assert response_codes == [201, 201]
    assert response_bodies[0] == response_bodies[1]
    assert sorted(replayed_flags) == [False, True]
    assert len(created_payouts) == 1
    assert Payout.objects.filter(merchant_id=merchant_id).count() == 1
    assert IdempotencyRecord.objects.filter(
        merchant_id=merchant_id,
        key=idempotency_key,
    ).count() == 1
    assert balance.available_paise == 125_000
    assert balance.held_paise == 25_000
    check_balance_invariant(Merchant.objects.get(pk=merchant_id))


@pytest.mark.django_db(transaction=True)
def test_retry_stuck_payouts_skips_rows_locked_by_another_scanner_postgres(merchant):
    if connection.vendor != "postgresql":
        pytest.skip("requires PostgreSQL row-level locks")

    _, _, payout, _ = create_payout_with_idempotency(
        merchant=merchant,
        idempotency_key="key-skip-locked",
        request_hash=request_hash(),
        amount_paise=25_000,
        bank_account_id="bank_skip_locked",
    )
    transition_to_processing(payout)
    payout.attempt_count = 1
    payout.processing_started_at = timezone.now() - timedelta(seconds=31)
    payout.save(update_fields=["attempt_count", "processing_started_at"])

    def run_retry_scanner():
        connections.close_all()
        try:
            return retry_stuck_payouts()
        finally:
            connections.close_all()

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with transaction.atomic():
            Payout.objects.select_for_update().get(pk=payout.pk)
            future = executor.submit(run_retry_scanner)
            done, not_done = wait([future], timeout=5)

            assert len(done) == 1
            assert not not_done
            assert future.result() == 0
    finally:
        executor.shutdown(cancel_futures=True)

    payout.refresh_from_db()
    assert payout.next_retry_at is None
    check_balance_invariant(Merchant.objects.get(pk=merchant.id))
