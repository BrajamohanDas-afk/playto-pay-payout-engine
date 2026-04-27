from django.contrib import admin

from apps.merchants.models import Merchant, MerchantBalance


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ["id", "display_name", "user", "created_at"]
    search_fields = ["display_name", "user__email", "user__username"]


@admin.register(MerchantBalance)
class MerchantBalanceAdmin(admin.ModelAdmin):
    list_display = [
        "merchant",
        "available_paise",
        "held_paise",
        "total_credited_paise",
        "total_debited_paise",
    ]
