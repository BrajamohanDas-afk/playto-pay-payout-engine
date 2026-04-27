from rest_framework import serializers

from apps.payouts.models import Payout


class PayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payout
        fields = [
            "id",
            "amount_paise",
            "bank_account_id",
            "status",
            "attempt_count",
            "max_attempts",
            "next_retry_at",
            "processing_started_at",
            "last_error",
            "settlement_reference",
            "completed_at",
            "failed_at",
            "created_at",
            "updated_at",
        ]


class PayoutCreateSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(min_value=1)
    bank_account_id = serializers.CharField(max_length=128, allow_blank=False)
