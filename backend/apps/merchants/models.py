from django.conf import settings
from django.db import models
from django.db.models import Q


class Merchant(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="merchant",
    )
    display_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.display_name


class MerchantBalance(models.Model):
    merchant = models.OneToOneField(
        Merchant,
        on_delete=models.CASCADE,
        related_name="balance",
    )
    available_paise = models.BigIntegerField(default=0)
    held_paise = models.BigIntegerField(default=0)
    total_credited_paise = models.BigIntegerField(default=0)
    total_debited_paise = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(available_paise__gte=0),
                name="merchant_balance_available_non_negative",
            ),
            models.CheckConstraint(
                check=Q(held_paise__gte=0),
                name="merchant_balance_held_non_negative",
            ),
            models.CheckConstraint(
                check=Q(total_credited_paise__gte=0),
                name="merchant_balance_total_credited_non_negative",
            ),
            models.CheckConstraint(
                check=Q(total_debited_paise__gte=0),
                name="merchant_balance_total_debited_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.merchant} balance"
