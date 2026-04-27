import pytest
from unittest.mock import patch
from django.contrib.auth import get_user_model

from apps.ledger.invariants import check_balance_invariant
from apps.ledger.models import LedgerEntry
from apps.merchants.models import Merchant, MerchantBalance
from apps.payouts.models import Payout


User = get_user_model()


@pytest.mark.django_db
def test_balance_api_is_scoped_to_authenticated_merchant(authed_client, merchant):
    response = authed_client.get("/api/v1/balance")

    assert response.status_code == 200
    assert response.data["available_paise"] == 150_000
    assert response.data["held_paise"] == 0


@pytest.mark.django_db
def test_login_with_email_returns_jwt_tokens(api_client, merchant):
    response = api_client.post(
        "/api/v1/auth/login",
        {"email": "merchant@example.com", "password": "password123"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["access"]
    assert response.data["refresh"]


@pytest.mark.django_db
def test_register_returns_jwt_tokens(api_client):
    response = api_client.post(
        "/api/v1/auth/register",
        {
            "email": "new@example.com",
            "password": "password123",
            "merchant_name": "New Merchant",
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["access"]
    assert response.data["refresh"]

    merchant = Merchant.objects.get(user__email="new@example.com")
    balance = MerchantBalance.objects.get(merchant=merchant)
    assert balance.available_paise == 250_000
    assert balance.held_paise == 0
    assert balance.total_credited_paise == 250_000
    assert LedgerEntry.objects.filter(
        merchant=merchant,
        direction=LedgerEntry.Direction.CREDIT,
        entry_type=LedgerEntry.EntryType.CUSTOMER_PAYMENT_CREDIT,
        status=LedgerEntry.Status.POSTED,
        amount_paise=250_000,
    ).exists()
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_authenticated_user_without_merchant_gets_403(api_client):
    user = User.objects.create_user(
        username="orphan@example.com",
        email="orphan@example.com",
        password="password123",
    )
    api_client.force_authenticate(user=user)

    response = api_client.get("/api/v1/balance")

    assert response.status_code == 403


@pytest.mark.django_db
def test_payout_create_api_requires_idempotency_key(authed_client):
    response = authed_client.post(
        "/api/v1/payouts",
        {"amount_paise": 10_000, "bank_account_id": "bank_test_001"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["error"] == "idempotency_key_required"


@pytest.mark.django_db
def test_payout_create_api_rejects_long_idempotency_key(authed_client):
    response = authed_client.post(
        "/api/v1/payouts",
        {"amount_paise": 10_000, "bank_account_id": "bank_test_001"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="x" * 256,
    )

    assert response.status_code == 400
    assert response.data["error"] == "idempotency_key_too_long"


@pytest.mark.django_db
def test_payout_create_api_holds_funds(authed_client, merchant):
    with patch("apps.payouts.views.enqueue_payout_processing"):
        response = authed_client.post(
            "/api/v1/payouts",
            {"amount_paise": 10_000, "bank_account_id": "bank_test_001"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="api-key-1",
        )

    merchant.balance.refresh_from_db()
    assert response.status_code == 201
    assert Payout.objects.filter(merchant=merchant).count() == 1
    assert merchant.balance.available_paise == 140_000
    assert merchant.balance.held_paise == 10_000
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_payout_create_api_still_returns_201_when_enqueue_fails(authed_client, merchant):
    with patch("apps.payouts.tasks.process_payout.delay", side_effect=Exception("redis down")):
        response = authed_client.post(
            "/api/v1/payouts",
            {"amount_paise": 10_000, "bank_account_id": "bank_test_001"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="api-key-enqueue-fail",
        )

    merchant.balance.refresh_from_db()
    assert response.status_code == 201
    assert merchant.balance.available_paise == 140_000
    assert merchant.balance.held_paise == 10_000
    check_balance_invariant(merchant)


@pytest.mark.django_db
def test_merchant_cannot_fetch_another_merchants_payout(api_client, merchant):
    other_user = User.objects.create_user(
        username="other@example.com",
        email="other@example.com",
        password="password123",
    )
    other = Merchant.objects.create(user=other_user, display_name="Other Merchant")
    MerchantBalance.objects.create(merchant=other, available_paise=50_000, total_credited_paise=50_000)
    payout = Payout.objects.create(
        merchant=other,
        amount_paise=10_000,
        bank_account_id="bank_other",
    )
    api_client.force_authenticate(user=merchant.user)

    response = api_client.get(f"/api/v1/payouts/{payout.id}")

    assert response.status_code == 404
