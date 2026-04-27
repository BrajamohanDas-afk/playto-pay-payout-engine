from django.urls import path

from apps.merchants.views import BalanceView


urlpatterns = [
    path("balance", BalanceView.as_view(), name="merchant-balance"),
]
