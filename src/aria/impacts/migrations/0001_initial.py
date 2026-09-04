import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("artifacts", "0005_artifactderivative_browser_rendered_dom"),
        ("comparisons", "0006_reviewedchangepublication"),
        ("documents", "0004_documentidentity_superseded_by_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="RegulatoryImpact",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "impact_type",
                    models.CharField(
                        choices=[
                            ("obligation", "Obligation"),
                            ("reporting", "Reporting"),
                            ("registration", "Registration"),
                            ("deadline", "Deadline"),
                            ("prohibition", "Prohibition"),
                            ("penalty", "Penalty"),
                            ("exemption", "Exemption"),
                            ("permission", "Permission"),
                            ("governance", "Governance"),
                            ("record_keeping", "Record keeping"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                (
                    "origin",
                    models.CharField(
                        choices=[
                            ("deterministic", "Deterministic"),
                            ("gpt", "GPT candidate"),
                            ("human", "Human candidate"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("title", models.CharField(max_length=512)),
                ("statement", models.TextField()),
                ("rationale", models.TextField(blank=True)),
                ("effective_date_text", models.CharField(blank=True, max_length=255)),
                ("legal_effect_assessed", models.BooleanField(default=False)),
                (
                    "input_fingerprint",
                    models.CharField(
                        max_length=64,
                        unique=True,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "comparison_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="regulatory_impacts",
                        to="comparisons.comparisonitem",
                    ),
                ),
                (
                    "confirmation_review",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="regulatory_impacts",
                        to="comparisons.comparisonreview",
                    ),
                ),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="ImpactEvidence",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "side",
                    models.CharField(
                        choices=[("before", "Before"), ("after", "After")], max_length=8
                    ),
                ),
                (
                    "artifact_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                ("anchor_text", models.TextField()),
                (
                    "anchor_text_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                (
                    "section_text_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                ("source_locator", models.JSONField(default=dict)),
                (
                    "created_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "impact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="evidence_records",
                        to="impacts.regulatoryimpact",
                    ),
                ),
                (
                    "normalized_section",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="impact_evidence_records",
                        to="documents.normalizedsection",
                    ),
                ),
                (
                    "source_artifact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="impact_evidence_records",
                        to="artifacts.rawartifact",
                    ),
                ),
                (
                    "structural_anchor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="impact_evidence_records",
                        to="comparisons.structuralanchor",
                    ),
                ),
            ],
            options={"ordering": ("impact", "side")},
        ),
        migrations.AddIndex(
            model_name="regulatoryimpact",
            index=models.Index(
                fields=["comparison_item", "impact_type", "created_at"],
                name="impact_item_type_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="regulatoryimpact",
            index=models.Index(
                fields=["confirmation_review", "created_at"],
                name="impact_review_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="impactevidence",
            index=models.Index(
                fields=["structural_anchor", "side"], name="impact_anchor_side_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="impactevidence",
            index=models.Index(
                fields=["source_artifact", "created_at"],
                name="impact_artifact_created_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="impactevidence",
            constraint=models.UniqueConstraint(
                fields=("impact", "side"), name="unique_evidence_side_per_impact"
            ),
        ),
    ]
