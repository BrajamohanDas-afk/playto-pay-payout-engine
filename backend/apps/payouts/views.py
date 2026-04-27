from django.db import transaction
from rest_framework import generics, status
from rest_framework.response import Response

from apps.merchants.auth import get_request_merchant
from apps.payouts.models import Payout
from apps.payouts.serializers import PayoutCreateSerializer, PayoutSerializer
from apps.payouts.services import (
    IdempotencyInProgressError,
    IdempotencyKeyReusedError,
    create_payout_with_idempotency,
    hash_idempotency_request,
)
from apps.payouts.tasks import enqueue_payout_processing


class PayoutListCreateView(generics.ListCreateAPIView):
    serializer_class = PayoutSerializer

    def get_queryset(self):
        return Payout.objects.filter(merchant=get_request_merchant(self.request))

    def create(self, request, *args, **kwargs):
        key = (request.headers.get("Idempotency-Key") or "").strip()
        if not key:
            return Response(
                {"error": "idempotency_key_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(key) > 255:
            return Response(
                {"error": "idempotency_key_too_long"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PayoutCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        merchant = get_request_merchant(request)
        request_hash = hash_idempotency_request(
            request.method,
            request.path,
            serializer.validated_data,
        )

        try:
            response_code, response_body, payout, replayed = create_payout_with_idempotency(
                merchant=merchant,
                idempotency_key=key,
                request_hash=request_hash,
                amount_paise=serializer.validated_data["amount_paise"],
                bank_account_id=serializer.validated_data["bank_account_id"],
            )
        except IdempotencyInProgressError:
            return Response(
                {"error": "request_in_progress"},
                status=status.HTTP_409_CONFLICT,
            )
        except IdempotencyKeyReusedError:
            return Response(
                {"error": "idempotency_key_reused"},
                status=status.HTTP_409_CONFLICT,
            )

        if payout is not None and not replayed:
            transaction.on_commit(lambda: enqueue_payout_processing(payout.id))
        return Response(response_body, status=response_code)


class PayoutDetailView(generics.RetrieveAPIView):
    serializer_class = PayoutSerializer

    def get_queryset(self):
        return Payout.objects.filter(merchant=get_request_merchant(self.request))
