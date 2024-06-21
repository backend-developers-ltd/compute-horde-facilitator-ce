# Generated by Django 4.2.13 on 2024-06-26 19:56

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0021_remove_gpuspecs_unique_gpu_specs_gpuspecs_serial_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignatureInfo",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "signature_type",
                    models.CharField(
                        db_comment="type of the signature (e.g. 'bittensor')",
                        max_length=255,
                    ),
                ),
                (
                    "signatory",
                    models.CharField(
                        db_comment="identity of the signer (e.g. sa58 address if signature_type == bittensor",
                        max_length=1000,
                    ),
                ),
                (
                    "timestamp_ns",
                    models.BigIntegerField(
                        db_comment="UNIX timestamp in nanoseconds; required for signature verification"
                    ),
                ),
                (
                    "signature",
                    models.BinaryField(db_comment="signature of the payload"),
                ),
                (
                    "signed_payload",
                    models.JSONField(db_comment="raw payload that was signed"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="JobFeedback",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "result_correctness",
                    models.FloatField(default=1, help_text="<0-1> where 1 means 100% correct"),
                ),
                (
                    "expected_duration",
                    models.FloatField(
                        blank=True,
                        help_text="Expected duration of the job in seconds",
                        null=True,
                    ),
                ),
                (
                    "job",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="feedback",
                        to="core.job",
                    ),
                ),
                (
                    "signature_info",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="core.signatureinfo",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="feedback",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]