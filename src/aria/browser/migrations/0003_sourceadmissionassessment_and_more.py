import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("browser", "0002_browsernetworkexchange_request_body_evidence"),
        ("sources", "0003_sourcepacksnapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceAdmissionAssessment",
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
                            ("incomplete", "Incomplete"),
                            ("ready", "Ready for promotion"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                (
                    "report_signature",
                    models.CharField(
                        max_length=64,
                        unique=True,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                ("required_captures", models.PositiveSmallIntegerField(default=2)),
                ("evaluated_capture_ids", models.JSONField(blank=True, default=list)),
                (
                    "candidate_set_sha256",
                    models.CharField(
                        blank=True,
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^$|^[0-9a-f]{64}$")],
                    ),
                ),
                ("candidate_count", models.PositiveIntegerField(default=0)),
                ("gates", models.JSONField(default=list)),
                ("assessed_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "endpoint",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="admission_assessments",
                        to="sources.sourceendpoint",
                    ),
                ),
                (
                    "source_pack_snapshot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="admission_assessments",
                        to="sources.sourcepacksnapshot",
                    ),
                ),
            ],
            options={
                "ordering": ("-assessed_at", "-id"),
                "indexes": [
                    models.Index(
                        fields=["endpoint", "status", "assessed_at"],
                        name="browser_sou_endpoin_e2c8ea_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="SourceAdmissionPromotion",
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
                ("next_poll_at", models.DateTimeField()),
                ("actor_identifier", models.CharField(blank=True, max_length=255)),
                ("promoted_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "assessment",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="promotion",
                        to="browser.sourceadmissionassessment",
                    ),
                ),
                (
                    "endpoint",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="admission_promotions",
                        to="sources.sourceendpoint",
                    ),
                ),
            ],
            options={"ordering": ("-promoted_at", "-id")},
        ),
    ]
