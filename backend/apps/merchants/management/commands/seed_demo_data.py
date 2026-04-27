from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F

from apps.ledger.invariants import check_balance_invariant
from apps.ledger.models import LedgerEntry
from apps.merchants.models import Merchant, MerchantBalance


User = get_user_model()


DEMO_MERCHANTS = [
    {
        "email": "merchant1@example.com",
        "display_name": "Aarav Games",
        "credits": [125_000, 75_000, 50_000],
    },
    {
        "email": "merchant2@example.com",
        "display_name": "Pixel Kart",
        "credits": [250_000, 40_000, 20_000],
    },
    {
        "email": "merchant3@example.com",
        "display_name": "Quiz Arena",
        "credits": [90_000, 35_000, 15_000],
    },
]


class Command(BaseCommand):
    help = "Seed demo merchants with credit ledger history and balances."

    def handle(self, *args, **options):
        with transaction.atomic():
            for item in DEMO_MERCHANTS:
                user, created = User.objects.get_or_create(
                    username=item["email"],
                    defaults={"email": item["email"]},
                )
                if created:
                    user.set_password("password123")
                    user.save(update_fields=["password"])

                merchant, _ = Merchant.objects.get_or_create(
                    user=user,
                    defaults={"display_name": item["display_name"]},
                )
                balance, _ = MerchantBalance.objects.get_or_create(merchant=merchant)

                if not LedgerEntry.objects.filter(
                    merchant=merchant,
                    entry_type=LedgerEntry.EntryType.CUSTOMER_PAYMENT_CREDIT,
                ).exists():
                    for idx, amount in enumerate(item["credits"], start=1):
                        LedgerEntry.objects.create(
                            merchant=merchant,
                            direction=LedgerEntry.Direction.CREDIT,
                            entry_type=LedgerEntry.EntryType.CUSTOMER_PAYMENT_CREDIT,
                            status=LedgerEntry.Status.POSTED,
                            amount_paise=amount,
                            description=f"Seeded customer payment credit {idx}.",
                        )
                        MerchantBalance.objects.filter(pk=balance.pk).update(
                            available_paise=F("available_paise") + amount,
                            total_credited_paise=F("total_credited_paise") + amount,
                        )
                    balance.refresh_from_db()

                check_balance_invariant(merchant)

        self.stdout.write(self.style.SUCCESS("Seeded demo merchants."))
        self.stdout.write("Demo password for all merchants: password123")
