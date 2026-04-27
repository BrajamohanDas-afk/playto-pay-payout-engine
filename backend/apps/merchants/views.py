from rest_framework import generics

from apps.merchants.auth import get_request_merchant
from apps.merchants.serializers import MerchantBalanceSerializer


class BalanceView(generics.RetrieveAPIView):
    serializer_class = MerchantBalanceSerializer

    def get_object(self):
        return get_request_merchant(self.request).balance
