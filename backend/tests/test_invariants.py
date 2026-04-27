import pytest

from apps.ledger.invariants import BalanceInvariantError, check_balance_invariant


@pytest.mark.django_db
def test_seeded_merchant_satisfies_balance_invariant(merchant):
    result = check_balance_invariant(merchant)

    assert result.expected_total_paise == 150_000
    assert result.actual_total_paise == 150_000
    assert result.expected_held_paise == 0


@pytest.mark.django_db
def test_invariant_detects_balance_drift(merchant):
    merchant.balance.available_paise += 1
    merchant.balance.save(update_fields=["available_paise"])

    with pytest.raises(BalanceInvariantError):
        check_balance_invariant(merchant)


@pytest.mark.django_db
def test_invariant_detects_credit_total_drift(merchant):
    merchant.balance.total_credited_paise += 1
    merchant.balance.save(update_fields=["total_credited_paise"])

    with pytest.raises(BalanceInvariantError):
        check_balance_invariant(merchant)
