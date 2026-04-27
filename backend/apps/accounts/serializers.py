from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.ledger.models import LedgerEntry
from apps.merchants.models import Merchant, MerchantBalance


User = get_user_model()
SIGNUP_DEMO_CREDIT_PAISE = 250_000


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    display_name = serializers.CharField(max_length=255, required=False)
    merchant_name = serializers.CharField(max_length=255, required=False, write_only=True)

    def validate_email(self, value):
        email = value.lower()
        if User.objects.filter(username=email).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return email

    def create(self, validated_data):
        with transaction.atomic():
            email = validated_data["email"]
            display_name = (
                validated_data.get("display_name")
                or validated_data.get("merchant_name")
                or email
            )
            user = User.objects.create_user(
                username=email,
                email=email,
                password=validated_data["password"],
            )
            merchant = Merchant.objects.create(
                user=user,
                display_name=display_name,
            )
            MerchantBalance.objects.create(
                merchant=merchant,
                available_paise=SIGNUP_DEMO_CREDIT_PAISE,
                total_credited_paise=SIGNUP_DEMO_CREDIT_PAISE,
            )
            LedgerEntry.objects.create(
                merchant=merchant,
                direction=LedgerEntry.Direction.CREDIT,
                entry_type=LedgerEntry.EntryType.CUSTOMER_PAYMENT_CREDIT,
                status=LedgerEntry.Status.POSTED,
                amount_paise=SIGNUP_DEMO_CREDIT_PAISE,
                description="Initial signup demo credit.",
            )
            return user


class MeSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="merchant.id")
    email = serializers.EmailField()
    display_name = serializers.CharField(source="merchant.display_name")
    merchant_name = serializers.CharField(source="merchant.display_name")


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    email = serializers.EmailField(write_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("username", None)

    def validate(self, attrs):
        if "email" in attrs:
            attrs["username"] = attrs.pop("email").lower()
        return super().validate(attrs)
