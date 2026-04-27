from django.urls import path

from apps.ledger.views import LedgerEntryListView


urlpatterns = [
    path("ledger", LedgerEntryListView.as_view(), name="ledger-list"),
]
