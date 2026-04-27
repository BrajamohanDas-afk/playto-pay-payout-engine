from django.contrib import admin

from apps.ledger.models import LedgerEntry


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "merchant",
        "direction",
        "entry_type",
        "status",
        "amount_paise",
        "payout",
        "created_at",
    ]
    list_filter = ["direction", "entry_type", "status"]
    search_fields = ["merchant__display_name", "description"]
