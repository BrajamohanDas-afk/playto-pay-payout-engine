from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import PermissionDenied


def get_request_merchant(request):
    try:
        return request.user.merchant
    except ObjectDoesNotExist as exc:
        raise PermissionDenied("Authenticated user is not linked to a merchant.") from exc
