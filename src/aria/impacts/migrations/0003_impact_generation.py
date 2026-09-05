import django.core.validators
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("impacts", "0002_applicability_taxonomy"),
    ]

    operations = [
        migrations.CreateModel(
            name="ImpactGeneration",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("provider", models.CharField(max_length=32)),
                ("model", models.CharField(max_length=128)),
                ("prompt_version", models.CharField(max_length=128)),
                (
                    "input_hash",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                ("input_snapshot", models.JSONField(default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("output", models.JSONField(blank=True, default=dict)),
                ("response_id", models.CharField(blank=True, max_length=255)),
                ("input_tokens", models.PositiveIntegerField(default=0)),
                ("output_tokens", models.PositiveIntegerField(default=0)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=128)),
                ("error_message", models.TextField(blank=True)),
                (
                    "comparison_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="impact_generations",
                        to="comparisons.comparisonitem",
                    ),
                ),
                (
                    "confirmation_review",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="impact_generations",
                        to="comparisons.comparisonreview",
                    ),
                ),
                (
                    "taxonomy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="impact_generations",
                        to="impacts.applicabilitytaxonomy",
                    ),
                ),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.AddField(
            model_name="regulatoryimpact",
            name="generation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="generated_impacts",
                to="impacts.impactgeneration",
            ),
        ),
        migrations.AddConstraint(
            model_name="impactgeneration",
            constraint=models.UniqueConstraint(
                fields=("provider", "model", "prompt_version", "input_hash"),
                name="unique_impact_generation_input",
            ),
        ),
        migrations.AddIndex(
            model_name="impactgeneration",
            index=models.Index(
                fields=["comparison_item", "status", "created_at"],
                name="impact_generation_item_idx",
            ),
        ),
    ]
