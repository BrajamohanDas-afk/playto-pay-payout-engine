from django.db import models
from django.db.models import Q


class LedgerEntry(models.Model):
    class Direction(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class EntryType(models.TextChoices):
        CUSTOMER_PAYMENT_CREDIT = "customer_payment_credit", "Customer payment credit"
        PAYOUT_HOLD = "payout_hold", "Payout hold"
        PAYOUT_REVERSAL = "payout_reversal", "Payout reversal"

    class Status(models.TextChoices):
        POSTED = "posted", "Posted"
        HELD = "held", "Held"
        SETTLED = "settled", "Settled"
        REVERSED = "reversed", "Reversed"

    merchant = models.ForeignKey(
        "merchants.Merchant",
        on_delete=models.CASCADE,
        related_name="ledger_entries",
    )
    payout = models.ForeignKey(
        "payouts.Payout",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
        null=True,
        blank=True,
    )
    direction = models.CharField(max_length=16, choices=Direction.choices)
    entry_type = models.CharField(max_length=64, choices=EntryType.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    amount_paise = models.BigIntegerField()
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["merchant", "created_at"]),
            models.Index(fields=["merchant", "direction"]),
            models.Index(fields=["merchant", "entry_type"]),
            models.Index(fields=["payout"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(amount_paise__gt=0),
                name="ledger_entry_amount_positive",
            )
        ]

    def __str__(self) -> str:
        return f"{self.direction} {self.amount_paise} for {self.merchant_id}"
