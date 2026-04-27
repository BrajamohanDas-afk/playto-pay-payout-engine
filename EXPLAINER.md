# Playto Pay Design Explainer

This document explains the design decisions for the Playto Pay payout engine. The assignment is small, but the critical behaviors are the same ones that matter in a real payout system: tenant isolation, money representation, concurrency, idempotency, state transitions, retries, and reconciliation.

## Reviewer-Focused Answers

### Q1: The Ledger Invariant

The invariant command recomputes accounting truth from the ledger and active payout state, then compares it with the materialized balance row.

```python
ledger_net = LedgerEntry.objects.filter(
    merchant=merchant,
    status__in=[LedgerEntry.Status.POSTED, LedgerEntry.Status.SETTLED],
).aggregate(
    credits=Sum(
        "amount_paise",
        filter=Q(direction=LedgerEntry.Direction.CREDIT),
    ),
    debits=Sum(
        "amount_paise",
        filter=Q(direction=LedgerEntry.Direction.DEBIT),
    ),
)
expected_total = (ledger_net["credits"] or 0) - (ledger_net["debits"] or 0)
actual_total = balance.available_paise + balance.held_paise

active_holds = Payout.objects.filter(
    merchant=merchant,
    status__in=[Payout.Status.PENDING, Payout.Status.PROCESSING],
).aggregate(total=Sum("amount_paise"))
expected_held = active_holds["total"] or 0
```

The key checks are: posted credits minus settled debits must equal available plus held balance, and active pending/processing payouts must equal held balance.

### Q2: The Lock

Payout creation locks the merchant balance row before checking available funds, then uses database-side `F()` updates to move money from available to held.

```python
balance = MerchantBalance.objects.select_for_update().get(merchant=merchant)
if balance.available_paise < amount_paise:
    response_body = {"error": "insufficient_funds"}
```

```python
MerchantBalance.objects.filter(pk=balance.pk).update(
    available_paise=F("available_paise") - amount_paise,
    held_paise=F("held_paise") + amount_paise,
)
```

On PostgreSQL, `select_for_update()` issues `SELECT ... FOR UPDATE`. That acquires a row-level lock on the merchant balance row. A second concurrent payout for the same merchant blocks at this line until the first transaction commits. It then re-reads the updated balance and correctly sees insufficient funds instead of overdrawing.

### Q3: Idempotency Locking

The idempotency record is acquired with an insert-first path. If another transaction already owns the `(merchant, key)` pair, the unique constraint forces the loser into the `IntegrityError` path, where it locks the existing row before applying the idempotency checks.

```python
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
```

The inner `transaction.atomic()` is deliberate: it gives the insert attempt its own savepoint, so catching `IntegrityError` does not poison the outer payout transaction.

After the row is locked, the service checks in this order:

1. Request hash mismatch: reject with `idempotency_key_reused`.
2. `status == completed`: replay the original stored response.
3. `response_body is not None`: defensive replay if a future code path writes a response body before updating status.
4. Existing unfinished record: reject with `request_in_progress`.

The `response_body is not None` branch is intentionally defensive. In the current implementation, `status`, `response_code`, and `response_body` are written together in one transaction, so completed records normally hit the `status == completed` branch first. Keeping the response-body check makes replay behavior safer if a future refactor writes the response body before flipping status.

### Q4: State Transition Guard

State transitions are explicit service functions. For example, a payout can only enter `processing` from `pending`.

```python
def transition_to_processing(payout: Payout) -> Payout:
    if payout.status != Payout.Status.PENDING:
        raise InvalidTransitionError(f"Cannot move {payout.status} to processing")
    payout.status = Payout.Status.PROCESSING
    payout.processing_started_at = timezone.now()
    payout.save(update_fields=["status", "processing_started_at", "updated_at"])
    return payout
```

The same pattern exists for `complete_payout()` and `fail_payout()`: terminal or invalid transitions raise `InvalidTransitionError`, and financial side effects live inside the transition function that needs them.

### Q5: AI Audit Finding

An audit found a subtle idempotency race in the original implementation:

```python
record, created = _get_or_create_idempotency_record(...)
record = IdempotencyRecord.objects.select_for_update().get(pk=record.pk)
```

The problem was that creation and locking were separated. A concurrent request could interact with the idempotency row before the winner had clearly established ownership through the unique insert path. The fix was to remove `get_or_create()` and use the insert-first sequence shown in Q3: try to insert, catch `IntegrityError`, then `select_for_update()` the existing row. That makes the database uniqueness constraint the serialization point for idempotency ownership.

## JWT-Only Merchant Identity

The API uses real login with password hashing and JWT tokens. Merchant identity comes from the authenticated user, via `request.user.merchant`.

I chose not to use `X-Merchant-Id` for normal APIs. A merchant header is easy to spoof from a browser or API client, and it duplicates identity that should already be established by authentication. Removing it keeps the multi-tenant model clean: every balance, ledger, and payout query is filtered by the logged-in merchant.

Tests can still create authenticated clients for different merchants, so JWT-only identity does not make testing harder.

## Integer Paise For Money

All money is stored as integer paise, using large integer database fields. The system does not use floats for money because floating point representation can introduce rounding errors. For this assignment, integer paise are also simpler than decimal arithmetic because every amount is already expected in the smallest currency unit.

## Materialized Balance Plus Ledger

The design uses both:

- A materialized `MerchantBalance` row for fast reads and concurrency control.
- Ledger entries for audit history.

The balance row makes payout checks efficient. A merchant has one financial balance row, so payout creation can lock that row with `select_for_update()`, check available funds, and move funds from available to held inside one transaction. This avoids scanning a growing ledger on every request and prevents check-then-deduct races.

The tradeoff is that ledger and balance can drift if updates are not handled carefully. The answer is not to ignore that risk; it is to make the invariant explicit and testable.

## Invariant Utility Story

The implementation includes `apps/ledger/invariants.py`, which recomputes financial truth from ledger and payout state and compares it with `MerchantBalance`.

The two key invariants are:

```text
posted credits - settled debits = available + held
active held payouts = held_paise
```

This is the main safety story for the accounting model. The materialized balance exists for performance and locking. The ledger exists for auditability. The invariant utility proves that the fast representation still agrees with the auditable representation.

## Held Funds And Ledger Semantics

A payout request immediately reserves money. That means the API moves the amount from `available_paise` to `held_paise` before settlement completes.

Ledger entries distinguish held debits from settled debits. The model uses:

- `direction`: `credit` or `debit`
- `entry_type`: `customer_payment_credit`, `payout_hold`, `payout_reversal`
- `status`: `posted`, `held`, `settled`, `reversed`

On payout creation, the system creates a debit ledger entry in held status. On success, that debit becomes settled. On failure, the held amount is released back to available and the original hold is marked reversed.

The implementation also writes a `payout_reversal` credit entry with `status=reversed`. That is intentionally an audit marker, not a posted financial credit. If the reversal credit were posted while the original held debit stayed outside the posted/settled invariant, the ledger-derived total would overstate the merchant balance. A different valid design would post both the original debit and a posted reversal credit, but this implementation keeps held/reversed entries outside realized balance math.

## PostgreSQL

PostgreSQL is the right database for this assignment because the design depends on transactional integrity, row-level locks, constraints, and reliable concurrent updates. Payout creation should use `transaction.atomic()` and row-level locking around the merchant balance row.

## Explicit Payout State Machine

Payouts have a small explicit state machine:

```text
pending -> processing -> completed
pending -> processing -> failed
```

Terminal states should not move backward. A completed payout should not become failed. A failed payout should not later become completed.

The state transitions live in service functions rather than being scattered across views and Celery tasks. That keeps financial side effects attached to the transition that requires them. In particular, the transition to `failed` atomically releases held funds.

## Idempotency Table

Payout creation requires an `Idempotency-Key` header. The backend stores idempotency records in a table scoped by merchant and key.

The record should include:

- Merchant
- Key
- Request hash
- Status
- Response code
- Response body
- Expiry time

Expected behavior:

- Same key and same body after completion returns the exact original response.
- Same key and same body while the first request is still processing returns `409 request_in_progress`.
- Same key with a different body returns `409 idempotency_key_reused`.
- Keys expire after 24 hours.

This protects clients from accidental retries creating duplicate payouts.

## Celery For Settlement And Retry

Settlement runs in Celery, not synchronously inside the API request. The API reserves funds and creates a pending payout quickly; the worker handles external settlement simulation.

Retry state is explicit database state on the payout, not only Celery retry metadata. Useful fields include:

- `attempt_count`
- `max_attempts`
- `next_retry_at`
- `processing_started_at`
- `last_error`
- `completed_at`
- `failed_at`

A periodic task finds payouts stuck in `processing` for more than 30 seconds. It retries with exponential backoff and fails the payout after the maximum number of attempts, releasing held funds atomically.

The settlement simulator is isolated so tests can deterministically force success, failure, and hang scenarios.

For demos, the simulator can also be controlled with `PAYOUT_SIMULATOR_MODE`. The default `random` mode preserves the assignment-style 70/20/10 split. `always_success`, `always_failed`, and `always_hang` make the full Docker stack predictable, while `by_bank_account` lets a demo operator choose outcomes with IDs such as `bank_success_demo`, `bank_fail_demo`, or `bank_hang_demo`.

## Polling Instead Of WebSockets

The frontend polls payout status every few seconds. WebSockets are not necessary for this assignment because payout status does not require sub-second updates, and polling is easier to deploy on Render and Vercel.

Polling also keeps the frontend and backend contract simple:

- `GET /balance`
- `GET /payouts`
- `GET /payouts/{id}`

## Simple `bank_account_id`

The payout request accepts a simple `bank_account_id` string. A full bank-account vault, verification flow, and tokenization model would be overbuilt for the assignment. The important part here is payout correctness, not bank account lifecycle management.

## API Shape

Merchant APIs are authenticated with JWT:

```text
Authorization: Bearer <access_token>
```

Payout creation:

```text
POST /api/v1/payouts
Idempotency-Key: <client-generated-key>
```

```json
{
  "amount_paise": 250000,
  "bank_account_id": "bank_acc_demo_001"
}
```

The API returns `409` for insufficient funds, duplicate in-flight idempotency keys, and idempotency key reuse with a different body.

## Testing Strategy

The highest-value tests are around the financial edge cases:

- Concurrent payouts cannot overdraw.
- Idempotency creates only one payout.
- Failed payouts release held funds.
- Invalid state transitions are rejected.
- Stuck payouts retry and eventually fail.
- The invariant utility confirms that ledger-derived truth matches materialized balance.

The normal local test suite runs on SQLite for speed, so the true concurrent overdraw test is PostgreSQL-only and skipped unless `DATABASE_URL` points to PostgreSQL. That split keeps fast tests convenient while still documenting and exercising the row-locking behavior the design depends on.

These tests are more important than broad UI tests because the assignment is primarily evaluating backend correctness and system design.
