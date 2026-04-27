from django.core.management.base import BaseCommand

from apps.ledger.invariants import BalanceInvariantError, check_balance_invariant
from apps.merchants.models import Merchant


class Command(BaseCommand):
    help = "Check ledger/materialized balance invariants for every merchant."

    def handle(self, *args, **options):
        failures = []
        for merchant in Merchant.objects.select_related("balance").all():
            try:
                check_balance_invariant(merchant)
            except BalanceInvariantError as exc:
                failures.append((merchant, exc))

        if failures:
            for merchant, exc in failures:
                self.stderr.write(f"{merchant.id} {merchant.display_name}: {exc}")
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("All merchant balance invariants passed."))
