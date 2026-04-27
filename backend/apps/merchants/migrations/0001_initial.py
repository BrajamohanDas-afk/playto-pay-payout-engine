from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Merchant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("display_name", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="merchant", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="MerchantBalance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("available_paise", models.BigIntegerField(default=0)),
                ("held_paise", models.BigIntegerField(default=0)),
                ("total_credited_paise", models.BigIntegerField(default=0)),
                ("total_debited_paise", models.BigIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("merchant", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="balance", to="merchants.merchant")),
            ],
        ),
        migrations.AddConstraint(
            model_name="merchantbalance",
            constraint=models.CheckConstraint(check=models.Q(("available_paise__gte", 0)), name="merchant_balance_available_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="merchantbalance",
            constraint=models.CheckConstraint(check=models.Q(("held_paise__gte", 0)), name="merchant_balance_held_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="merchantbalance",
            constraint=models.CheckConstraint(check=models.Q(("total_credited_paise__gte", 0)), name="merchant_balance_total_credited_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="merchantbalance",
            constraint=models.CheckConstraint(check=models.Q(("total_debited_paise__gte", 0)), name="merchant_balance_total_debited_non_negative"),
        ),
    ]
