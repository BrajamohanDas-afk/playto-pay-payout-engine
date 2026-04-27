from rest_framework import generics

from apps.ledger.models import LedgerEntry
from apps.ledger.serializers import LedgerEntrySerializer
from apps.merchants.auth import get_request_merchant


class LedgerEntryListView(generics.ListAPIView):
    serializer_class = LedgerEntrySerializer

    def get_queryset(self):
        return LedgerEntry.objects.filter(merchant=get_request_merchant(self.request))
