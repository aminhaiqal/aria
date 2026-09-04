import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("impacts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApplicabilityTaxonomy",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("slug", models.SlugField(max_length=128)),
                ("schema_version", models.PositiveIntegerField()),
                ("version", models.PositiveIntegerField()),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField()),
                ("jurisdiction", models.CharField(max_length=128)),
                ("disclaimer", models.TextField()),
                (
                    "checksum",
                    models.CharField(
                        max_length=64,
                        unique=True,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                ("definition", models.JSONField()),
                (
                    "applied_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
            ],
            options={"ordering": ("slug", "-version")},
        ),
        migrations.CreateModel(
            name="ApplicabilityTerm",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "dimension",
                    models.CharField(
                        choices=[
                            ("sector", "Sector"),
                            ("organization_type", "Organization type"),
                            ("regulated_role", "Regulated role"),
                            ("activity", "Activity"),
                            ("jurisdiction", "Jurisdiction"),
                            ("size", "Size"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("code", models.SlugField(max_length=128)),
                ("label", models.CharField(max_length=255)),
                ("description", models.TextField()),
                ("aliases", models.JSONField(blank=True, default=list)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "created_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="children",
                        to="impacts.applicabilityterm",
                    ),
                ),
                (
                    "taxonomy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="terms",
                        to="impacts.applicabilitytaxonomy",
                    ),
                ),
            ],
            options={"ordering": ("taxonomy", "dimension", "code")},
        ),
        migrations.CreateModel(
            name="ImpactTarget",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "disposition",
                    models.CharField(
                        choices=[("included", "Included"), ("excluded", "Excluded")],
                        max_length=16,
                    ),
                ),
                (
                    "origin",
                    models.CharField(
                        choices=[
                            ("deterministic", "Deterministic"),
                            ("gpt", "GPT candidate"),
                            ("human", "Human"),
                        ],
                        max_length=16,
                    ),
                ),
                ("rationale", models.TextField()),
                (
                    "created_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "impact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="targets",
                        to="impacts.regulatoryimpact",
                    ),
                ),
                (
                    "term",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="impact_targets",
                        to="impacts.applicabilityterm",
                    ),
                ),
            ],
            options={"ordering": ("impact", "term__dimension", "term__code")},
        ),
        migrations.AddConstraint(
            model_name="applicabilitytaxonomy",
            constraint=models.UniqueConstraint(
                fields=("slug", "version"),
                name="unique_applicability_taxonomy_version",
            ),
        ),
        migrations.AddIndex(
            model_name="applicabilityterm",
            index=models.Index(
                fields=["dimension", "code"], name="impact_term_dimension_code_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="applicabilityterm",
            constraint=models.UniqueConstraint(
                fields=("taxonomy", "dimension", "code"),
                name="unique_term_per_taxonomy_dimension",
            ),
        ),
        migrations.AddIndex(
            model_name="impacttarget",
            index=models.Index(
                fields=["term", "disposition", "created_at"],
                name="impact_target_term_state_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="impacttarget",
            constraint=models.UniqueConstraint(
                fields=("impact", "term"),
                name="unique_applicability_term_per_impact",
            ),
        ),
    ]
