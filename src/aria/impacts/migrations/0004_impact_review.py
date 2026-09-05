import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("impacts", "0003_impact_generation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ImpactReview",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("approved", "Approved"),
                            ("amended", "Approved with amendments"),
                            ("rejected", "Rejected"),
                            ("needs_context", "Needs context"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("reviewed_title", models.CharField(max_length=512)),
                ("reviewed_statement", models.TextField()),
                (
                    "reviewed_effective_date_text",
                    models.CharField(blank=True, max_length=255),
                ),
                ("rationale", models.TextField(blank=True)),
                (
                    "created_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "impact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reviews",
                        to="impacts.regulatoryimpact",
                    ),
                ),
                (
                    "previous_review",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="superseding_reviews",
                        to="impacts.impactreview",
                    ),
                ),
                (
                    "reviewer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="impact_reviews",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="ImpactReviewTarget",
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
                ("rationale", models.TextField()),
                (
                    "created_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "impact_review",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reviewed_targets",
                        to="impacts.impactreview",
                    ),
                ),
                (
                    "source_target",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="review_snapshots",
                        to="impacts.impacttarget",
                    ),
                ),
                (
                    "term",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="impact_review_targets",
                        to="impacts.applicabilityterm",
                    ),
                ),
            ],
            options={"ordering": ("impact_review", "term__dimension", "term__code")},
        ),
        migrations.AddIndex(
            model_name="impactreview",
            index=models.Index(fields=["impact", "created_at"], name="impact_review_history_idx"),
        ),
        migrations.AddIndex(
            model_name="impactreview",
            index=models.Index(
                fields=["decision", "created_at"], name="impact_review_decision_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="impactreviewtarget",
            constraint=models.UniqueConstraint(
                fields=("impact_review", "term"),
                name="unique_term_per_impact_review",
            ),
        ),
    ]
