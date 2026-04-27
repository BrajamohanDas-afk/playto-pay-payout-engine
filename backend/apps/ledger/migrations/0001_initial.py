from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("merchants", "0001_initial"),
        ("payouts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="LedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("direction", models.CharField(choices=[("credit", "Credit"), ("debit", "Debit")], max_length=16)),
                ("entry_type", models.CharField(choices=[("customer_payment_credit", "Customer payment credit"), ("payout_hold", "Payout hold"), ("payout_reversal", "Payout reversal")], max_length=64)),
                ("status", models.CharField(choices=[("posted", "Posted"), ("held", "Held"), ("settled", "Settled"), ("reversed", "Reversed")], max_length=16)),
                ("amount_paise", models.BigIntegerField()),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("merchant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ledger_entries", to="merchants.merchant")),
                ("payout", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="payouts.payout")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(model_name="ledgerentry", index=models.Index(fields=["merchant", "created_at"], name="ledger_ledg_merchan_21081f_idx")),
        migrations.AddIndex(model_name="ledgerentry", index=models.Index(fields=["merchant", "direction"], name="ledger_ledg_merchan_7dddbc_idx")),
        migrations.AddIndex(model_name="ledgerentry", index=models.Index(fields=["merchant", "entry_type"], name="ledger_ledg_merchan_8f05ad_idx")),
        migrations.AddIndex(model_name="ledgerentry", index=models.Index(fields=["payout"], name="ledger_ledg_payout__a49df9_idx")),
        migrations.AddConstraint(model_name="ledgerentry", constraint=models.CheckConstraint(check=models.Q(("amount_paise__gt", 0)), name="ledger_entry_amount_positive")),
    ]
