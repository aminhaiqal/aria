import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("artifacts", "0005_artifactderivative_browser_rendered_dom"),
        ("discovery", "0003_monitoredresource_resourceobservation_and_more"),
        ("sources", "0002_sourceendpoint_consecutive_failures_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="BrowserCapture",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                            ("quarantined", "Quarantined"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("profile", models.CharField(max_length=128)),
                ("configuration", models.JSONField(default=dict)),
                (
                    "configuration_hash",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                ("toolchain", models.JSONField(blank=True, default=dict)),
                ("requested_url", models.URLField(max_length=2048)),
                ("final_url", models.URLField(blank=True, max_length=2048)),
                ("response_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("response_headers", models.JSONField(blank=True, default=dict)),
                ("redirect_chain", models.JSONField(blank=True, default=list)),
                ("resolved_addresses", models.JSONField(blank=True, default=list)),
                ("attempt_count", models.PositiveSmallIntegerField(default=0)),
                ("request_count", models.PositiveIntegerField(default=0)),
                ("blocked_request_count", models.PositiveIntegerField(default=0)),
                ("response_bytes", models.PositiveBigIntegerField(default=0)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=128)),
                ("error_message", models.TextField(blank=True)),
                (
                    "endpoint",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="browser_captures",
                        to="sources.sourceendpoint",
                    ),
                ),
                (
                    "original_artifact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="browser_captures_as_original",
                        to="artifacts.rawartifact",
                    ),
                ),
                (
                    "rendered_artifact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="browser_captures_as_rendered",
                        to="artifacts.rawartifact",
                    ),
                ),
                (
                    "rendered_derivative",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="browser_captures",
                        to="artifacts.artifactderivative",
                    ),
                ),
                (
                    "source_run",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="browser_capture",
                        to="discovery.sourcerun",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "indexes": [
                    models.Index(
                        fields=["endpoint", "status", "created_at"],
                        name="browser_bro_endpoin_b2e3a2_idx",
                    ),
                    models.Index(
                        fields=["status", "updated_at"],
                        name="browser_bro_status_5f1933_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="BrowserNetworkExchange",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("attempt_number", models.PositiveSmallIntegerField()),
                ("sequence", models.PositiveIntegerField()),
                ("requested_url", models.URLField(max_length=2048)),
                ("method", models.CharField(max_length=16)),
                ("resource_type", models.CharField(max_length=32)),
                (
                    "disposition",
                    models.CharField(
                        choices=[("allowed", "Allowed"), ("blocked", "Blocked")],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("block_reason", models.CharField(blank=True, max_length=255)),
                ("response_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("content_type", models.CharField(blank=True, max_length=255)),
                ("byte_size", models.PositiveBigIntegerField(default=0)),
                (
                    "body_sha256",
                    models.CharField(
                        blank=True,
                        max_length=64,
                        validators=[
                            django.core.validators.RegexValidator("^$|^[0-9a-f]{64}$")
                        ],
                    ),
                ),
                ("resolved_addresses", models.JSONField(blank=True, default=list)),
                ("occurred_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "body_artifact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="browser_network_exchanges",
                        to="artifacts.rawartifact",
                    ),
                ),
                (
                    "capture",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="network_exchanges",
                        to="browser.browsercapture",
                    ),
                ),
            ],
            options={
                "ordering": ("capture", "attempt_number", "sequence"),
                "indexes": [
                    models.Index(
                        fields=["capture", "disposition", "sequence"],
                        name="browser_bro_capture_01c66b_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("capture", "attempt_number", "sequence"),
                        name="unique_browser_exchange_sequence",
                    )
                ],
            },
        ),
    ]
