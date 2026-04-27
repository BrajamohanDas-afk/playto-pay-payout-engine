from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import MeView


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/me", MeView.as_view(), name="me"),
    path("api/v1/", include("apps.merchants.urls")),
    path("api/v1/", include("apps.ledger.urls")),
    path("api/v1/", include("apps.payouts.urls")),
]
