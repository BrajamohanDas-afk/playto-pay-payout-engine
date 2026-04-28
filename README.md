# Playto Pay Payout Engine

This repository is for the Playto Pay Founding Engineer Challenge payout engine. It includes a Django REST Framework backend, React + Tailwind dashboard, PostgreSQL-ready database configuration, Redis broker, and Celery worker.

The goal is not just to make a happy-path demo. The design emphasizes financial correctness: integer paise, transaction boundaries, row-level locking, idempotency, an explicit payout state machine, retry handling, and ledger/balance invariants.

## Stack

- Backend: Django + Django REST Framework
- Frontend: React + Vite + Tailwind
- Database: PostgreSQL
- Background jobs: Celery
- Broker: Redis
- Local orchestration: Docker Compose
- Backend deployment target: Render
- Frontend deployment target: Vercel

## Core Product Flow

1. A merchant logs in with email/password and receives JWT tokens.
2. The dashboard loads merchant-scoped balance, ledger, and payout history.
3. The merchant requests a payout with an `Idempotency-Key`.
4. The API atomically moves funds from available to held, creates a payout, creates a ledger entry, and stores the idempotent response.
5. A Celery worker processes settlement asynchronously.
6. Completed payouts settle the held debit. Failed payouts release held funds back to available.

## Key Design Choices

- Merchant identity comes from JWT auth, not `X-Merchant-Id`.
- Money is stored as integer paise.
- Balances use a materialized row for fast reads and safe row-level locking.
- Ledger entries preserve audit history and distinguish held versus settled debits.
- Payout state transitions are centralized and explicit.
- Idempotency is stored in a database table scoped by merchant and key.
- Frontend uses polling for payout updates instead of WebSockets.

## Financial Invariants

The backend includes `apps/ledger/invariants.py`, which recomputes truth from ledger and payout state, then compares it with the materialized balance row:

```text
posted credits - settled debits = available + held
active held payouts = held_paise
```

This is the safety story behind using both a ledger and materialized balance. The balance row gives speed; the invariant check proves it has not drifted from auditable records.

## API

Base path:

```text
/api/v1
```

Auth:

```text
POST /auth/register
POST /auth/login
POST /auth/refresh
GET /me
```

Merchant APIs:

```text
GET /balance
GET /ledger
GET /payouts
POST /payouts
GET /payouts/{id}
```

Payout request:

```json
{
  "amount_paise": 250000,
  "bank_account_id": "bank_acc_demo_001"
}
```

Required header:

```text
Idempotency-Key: <client-generated-key>
```

Expected conflict responses include `insufficient_funds`, `request_in_progress`, and `idempotency_key_reused`.

## Local Development

Backend only:

```powershell
cd backend
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo_data
python manage.py runserver
```

Frontend only:

```powershell
cd frontend
npm install
npm run dev
```

Demo merchants:

```text
merchant1@example.com / password123
merchant2@example.com / password123
merchant3@example.com / password123
```

Newly registered merchants receive an initial demo credit of `250000` paise (Rs. 2,500) as a posted ledger entry, so they can test payouts immediately.

Full stack:

```powershell
docker compose down
docker compose up --build
```

Expected services:

- API server
- PostgreSQL
- Redis
- Celery worker
- Scheduled retry worker or Celery beat
- Frontend dev server

Configuration can be copied from `.env.example`.

For deterministic demos, set `PAYOUT_SIMULATOR_MODE` before starting the stack:

```env
PAYOUT_SIMULATOR_MODE=always_success
```

Supported simulator modes:

- `random`: default 70% success, 20% failure, 10% hang.
- `always_success`: every settlement completes.
- `always_failed`: every settlement fails and releases held funds.
- `always_hang`: every settlement attempt hangs until retry logic handles it.
- `by_bank_account`: `bank_account_id` containing `success`, `fail`/`reject`, or `hang`/`timeout` forces that outcome; other IDs use a stable deterministic bucket.

## Verification

Backend:

```powershell
cd backend
pytest -q
```

The suite includes a PostgreSQL-only concurrent overdraw test. It is skipped on SQLite and runs when `DATABASE_URL` points to PostgreSQL.

Check all merchant ledger/balance invariants:

```powershell
cd backend
python manage.py check_invariants
```

Frontend:

```powershell
cd frontend
npm run build
```

## Tests To Prioritize

- PostgreSQL concurrent payout requests cannot overdraw.
- Duplicate idempotency key replays the original response.
- Same idempotency key with a different body returns `409`.
- In-flight duplicate request returns `409 request_in_progress`.
- Expired idempotency keys can be reused after 24 hours.
- Duplicate Celery deliveries do not double-settle the same payout.
- Failed payout releases held funds atomically.
- Invalid payout state transitions are rejected.
- Stuck processing payouts retry and eventually fail after max attempts.
- Balance invariant utility validates ledger/materialized balance consistency.

## Live Demo

- **Frontend dashboard:** https://payout-engine-two.vercel.app
- **Backend API:** https://playto-pay-payout-engine-nybe.onrender.com

### Demo credentials

- `merchant1@example.com` / `password123`
- `merchant2@example.com` / `password123`
- `merchant3@example.com` / `password123`

Each seeded merchant starts with ₹2,500 (250,000 paise) of credit history.

### Forcing settlement outcomes

The deployed backend runs the simulator in `by_bank_account` mode, so reviewers can deterministically test each path. Use these `bank_account_id` values when submitting a payout:

- `bank_success_demo` → settlement succeeds, payout completes
- `bank_fail_demo` → settlement fails, held funds returned to balance
- `bank_hang_demo` → settlement hangs (would trigger retry logic if Celery beat were running)

Any other `bank_account_id` falls into a deterministic outcome bucket based on the ID string.

### Free-tier deployment notes

- **Cold start:** Render free tier puts services to sleep after 15 minutes of inactivity. The first request after a sleep period may take 30-60 seconds while the service wakes up.
- **Worker process:** Render no longer offers free Background Worker instances, so the Celery worker runs as a detached process inside the web service rather than as a dedicated service. The worker handles all `process_payout` tasks normally — payouts go pending → processing → completed/failed as expected.
- **Beat scheduler:** Celery beat is **not running** on the deployed instance. This means periodic tasks (`retry_stuck_payouts`, `cleanup_expired_idempotency_records`) don't fire automatically in production. The retry logic itself is fully implemented and exercised in tests (`test_stuck_processing_payout_*`), and `docker-compose.yml` reflects the proper three-service architecture (web + worker + beat) for self-hosted deployment.

## Production Deployment Reference

For reference, the production deployment uses:

- **Backend on Render:** Python 3 web service, root directory `backend`, build command `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate && python manage.py seed_demo_data`, start command runs Celery worker as a detached process alongside gunicorn.
- **Frontend on Vercel:** Vite preset, root directory `frontend`, env var `VITE_API_BASE_URL` pointing to the Render backend.
- **Required backend env vars:** `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS`, `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CORS_ALLOWED_ORIGINS`, `PAYOUT_SIMULATOR_MODE=by_bank_account`.
