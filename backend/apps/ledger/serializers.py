from rest_framework import serializers

from apps.ledger.models import LedgerEntry


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = [
            "id",
            "direction",
            "entry_type",
            "status",
            "amount_paise",
            "description",
            "payout_id",
            "created_at",
        ]
