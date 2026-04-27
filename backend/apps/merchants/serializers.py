from rest_framework import serializers

from apps.merchants.models import MerchantBalance


class MerchantBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MerchantBalance
        fields = [
            "available_paise",
            "held_paise",
            "total_credited_paise",
            "total_debited_paise",
            "updated_at",
        ]
