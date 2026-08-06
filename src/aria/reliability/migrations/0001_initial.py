import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("discovery", "0003_monitoredresource_resourceobservation_and_more"),
        ("sources", "0002_sourceendpoint_consecutive_failures_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceReliabilityAssessment",
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
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("healthy", "Healthy"),
                            ("processing", "Processing"),
                            ("warning", "Warning"),
                            ("critical", "Critical"),
                            ("disabled", "Disabled"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                (
                    "assessment_signature",
                    models.CharField(
                        max_length=64,
                        unique=True,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                ("freshness_deadline", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "candidate_set_sha256",
                    models.CharField(
                        blank=True,
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^$|^[0-9a-f]{64}$")],
                    ),
                ),
                (
                    "previous_candidate_set_sha256",
                    models.CharField(
                        blank=True,
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^$|^[0-9a-f]{64}$")],
                    ),
                ),
                ("candidate_set_changed", models.BooleanField(default=False)),
                ("candidate_count", models.PositiveIntegerField(default=0)),
                ("artifact_ready_count", models.PositiveIntegerField(default=0)),
                ("extraction_ready_count", models.PositiveIntegerField(default=0)),
                ("graph_ready_count", models.PositiveIntegerField(default=0)),
                ("document_version_count", models.PositiveIntegerField(default=0)),
                ("section_count", models.PositiveIntegerField(default=0)),
                ("local_embedding_count", models.PositiveIntegerField(default=0)),
                ("configured_embedding_count", models.PositiveIntegerField(default=0)),
                ("configured_embedding_provider", models.CharField(blank=True, max_length=64)),
                ("configured_embedding_model", models.CharField(blank=True, max_length=128)),
                ("browser_capture_ready", models.BooleanField(default=False)),
                ("network_policy_ready", models.BooleanField(default=False)),
                ("findings", models.JSONField(blank=True, default=list)),
                ("assessed_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "endpoint",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reliability_assessments",
                        to="sources.sourceendpoint",
                    ),
                ),
                (
                    "previous_assessment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="next_assessments",
                        to="reliability.sourcereliabilityassessment",
                    ),
                ),
                (
                    "source_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reliability_assessments",
                        to="discovery.sourcerun",
                    ),
                ),
            ],
            options={
                "ordering": ("-assessed_at", "-id"),
                "indexes": [
                    models.Index(
                        fields=["endpoint", "status", "assessed_at"],
                        name="reliability_endpoin_7a0cc7_idx",
                    ),
                    models.Index(
                        fields=["status", "freshness_deadline"],
                        name="reliability_status_fc1b8c_idx",
                    ),
                ],
            },
        ),
    ]
