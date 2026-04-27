from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("merchants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Payout",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount_paise", models.BigIntegerField()),
                ("bank_account_id", models.CharField(max_length=128)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("processing", "Processing"), ("completed", "Completed"), ("failed", "Failed")], default="pending", max_length=16)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("max_attempts", models.PositiveIntegerField(default=3)),
                ("next_retry_at", models.DateTimeField(blank=True, null=True)),
                ("processing_started_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("settlement_reference", models.CharField(blank=True, max_length=128)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("failed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("merchant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payouts", to="merchants.merchant")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=255)),
                ("request_hash", models.CharField(max_length=64)),
                ("status", models.CharField(choices=[("processing", "Processing"), ("completed", "Completed"), ("failed", "Failed")], default="processing", max_length=16)),
                ("response_code", models.PositiveIntegerField(blank=True, null=True)),
                ("response_body", models.JSONField(blank=True, null=True)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("merchant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="idempotency_records", to="merchants.merchant")),
            ],
        ),
        migrations.AddIndex(model_name="payout", index=models.Index(fields=["merchant", "created_at"], name="payouts_pay_merchan_e463c2_idx")),
        migrations.AddIndex(model_name="payout", index=models.Index(fields=["status", "next_retry_at"], name="payouts_pay_status_5ec47b_idx")),
        migrations.AddIndex(model_name="payout", index=models.Index(fields=["status", "processing_started_at"], name="payouts_pay_status_c66233_idx")),
        migrations.AddConstraint(model_name="payout", constraint=models.CheckConstraint(check=models.Q(("amount_paise__gt", 0)), name="payout_amount_positive")),
        migrations.AddConstraint(model_name="payout", constraint=models.CheckConstraint(check=models.Q(("max_attempts__gt", 0)), name="payout_max_attempts_positive")),
        migrations.AddConstraint(model_name="idempotencyrecord", constraint=models.UniqueConstraint(fields=("merchant", "key"), name="unique_idempotency_key_per_merchant")),
        migrations.AddIndex(model_name="idempotencyrecord", index=models.Index(fields=["merchant", "key"], name="payouts_ide_merchan_311e86_idx")),
        migrations.AddIndex(model_name="idempotencyrecord", index=models.Index(fields=["expires_at"], name="payouts_ide_expires_463bfe_idx")),
    ]
