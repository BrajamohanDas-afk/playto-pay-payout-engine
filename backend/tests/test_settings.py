from django.conf import settings


def test_cors_allows_idempotency_key_header():
    assert "idempotency-key" in settings.CORS_ALLOW_HEADERS
