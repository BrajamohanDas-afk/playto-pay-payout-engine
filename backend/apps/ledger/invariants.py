from dataclasses import dataclass

from django.db.models import Q, Sum

from apps.ledger.models import LedgerEntry
from apps.merchants.models import MerchantBalance
from apps.payouts.models import Payout


@dataclass(frozen=True)
class BalanceInvariantResult:
    expected_total_paise: int
    actual_total_paise: int
    expected_held_paise: int
    actual_held_paise: int
    expected_total_credited_paise: int
    actual_total_credited_paise: int
    expected_total_debited_paise: int
    actual_total_debited_paise: int


class BalanceInvariantError(AssertionError):
    pass


def calculate_balance_invariant(merchant) -> BalanceInvariantResult:
    balance = MerchantBalance.objects.get(merchant=merchant)
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

    # Only counts customer payment credits; update this if new credit types are added.
    expected_credited = (
        LedgerEntry.objects.filter(
            merchant=merchant,
            direction=LedgerEntry.Direction.CREDIT,
            entry_type=LedgerEntry.EntryType.CUSTOMER_PAYMENT_CREDIT,
            status=LedgerEntry.Status.POSTED,
        ).aggregate(total=Sum("amount_paise"))["total"]
        or 0
    )
    expected_debited = (
        LedgerEntry.objects.filter(
            merchant=merchant,
            direction=LedgerEntry.Direction.DEBIT,
            status=LedgerEntry.Status.SETTLED,
        ).aggregate(total=Sum("amount_paise"))["total"]
        or 0
    )

    return BalanceInvariantResult(
        expected_total_paise=expected_total,
        actual_total_paise=actual_total,
        expected_held_paise=expected_held,
        actual_held_paise=balance.held_paise,
        expected_total_credited_paise=expected_credited,
        actual_total_credited_paise=balance.total_credited_paise,
        expected_total_debited_paise=expected_debited,
        actual_total_debited_paise=balance.total_debited_paise,
    )


def check_balance_invariant(merchant) -> BalanceInvariantResult:
    result = calculate_balance_invariant(merchant)
    if result.expected_total_paise != result.actual_total_paise:
        raise BalanceInvariantError(
            "Total balance invariant violated: "
            f"expected {result.expected_total_paise}, got {result.actual_total_paise}"
        )
    if result.expected_held_paise != result.actual_held_paise:
        raise BalanceInvariantError(
            "Held balance invariant violated: "
            f"expected {result.expected_held_paise}, got {result.actual_held_paise}"
        )
    if result.expected_total_credited_paise != result.actual_total_credited_paise:
        raise BalanceInvariantError(
            "Total credited invariant violated: "
            f"expected {result.expected_total_credited_paise}, "
            f"got {result.actual_total_credited_paise}"
        )
    if result.expected_total_debited_paise != result.actual_total_debited_paise:
        raise BalanceInvariantError(
            "Total debited invariant violated: "
            f"expected {result.expected_total_debited_paise}, "
            f"got {result.actual_total_debited_paise}"
        )
    return result
