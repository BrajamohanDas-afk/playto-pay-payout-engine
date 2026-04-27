from django.contrib import admin

from apps.payouts.models import IdempotencyRecord, Payout


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "merchant",
        "amount_paise",
        "status",
        "attempt_count",
        "created_at",
    ]
    list_filter = ["status"]
    search_fields = ["merchant__display_name", "bank_account_id"]


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    list_display = ["merchant", "key", "status", "response_code", "expires_at"]
    list_filter = ["status"]
    search_fields = ["merchant__display_name", "key"]
