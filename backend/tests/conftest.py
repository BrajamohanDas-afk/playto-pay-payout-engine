import pytest
from django.contrib.auth import get_user_model
from django.db.models import F
from rest_framework.test import APIClient

from apps.ledger.invariants import check_balance_invariant
from apps.ledger.models import LedgerEntry
from apps.merchants.models import Merchant, MerchantBalance


User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def merchant(db):
    user = User.objects.create_user(
        username="merchant@example.com",
        email="merchant@example.com",
        password="password123",
    )
    merchant = Merchant.objects.create(user=user, display_name="Test Merchant")
    balance = MerchantBalance.objects.create(merchant=merchant)
    for amount in [100_000, 50_000]:
        LedgerEntry.objects.create(
            merchant=merchant,
            direction=LedgerEntry.Direction.CREDIT,
            entry_type=LedgerEntry.EntryType.CUSTOMER_PAYMENT_CREDIT,
            status=LedgerEntry.Status.POSTED,
            amount_paise=amount,
            description="Test customer payment.",
        )
        MerchantBalance.objects.filter(pk=balance.pk).update(
            available_paise=F("available_paise") + amount,
            total_credited_paise=F("total_credited_paise") + amount,
        )
    merchant.refresh_from_db()
    check_balance_invariant(merchant)
    return merchant


@pytest.fixture
def authed_client(api_client, merchant):
    api_client.force_authenticate(user=merchant.user)
    return api_client
