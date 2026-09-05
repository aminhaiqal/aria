import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.core import validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("impacts", "0004_impact_review"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BusinessProfile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="business_profiles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "taxonomy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="business_profiles",
                        to="impacts.applicabilitytaxonomy",
                    ),
                ),
            ],
            options={"ordering": ("owner", "name", "id")},
        ),
        migrations.CreateModel(
            name="BusinessProfileTerm",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile_terms",
                        to="impacts.businessprofile",
                    ),
                ),
                (
                    "term",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="profile_assignments",
                        to="impacts.applicabilityterm",
                    ),
                ),
            ],
            options={"ordering": ("profile", "term__dimension", "term__code")},
        ),
        migrations.AddField(
            model_name="businessprofile",
            name="terms",
            field=models.ManyToManyField(
                related_name="business_profiles",
                through="impacts.BusinessProfileTerm",
                to="impacts.applicabilityterm",
            ),
        ),
        migrations.CreateModel(
            name="ProfileImpactMatch",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("matched", "Matched"),
                            ("not_matched", "Not matched"),
                            ("insufficient_context", "Insufficient profile context"),
                        ],
                        db_index=True,
                        max_length=24,
                    ),
                ),
                ("ruleset", models.CharField(max_length=128)),
                (
                    "input_fingerprint",
                    models.CharField(
                        max_length=64,
                        unique=True,
                        validators=[validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                ("profile_snapshot", models.JSONField()),
                ("impact_review_snapshot", models.JSONField()),
                ("matched_terms", models.JSONField(blank=True, default=list)),
                ("excluded_terms", models.JSONField(blank=True, default=list)),
                ("unmet_dimensions", models.JSONField(blank=True, default=list)),
                ("unresolved_dimensions", models.JSONField(blank=True, default=list)),
                ("explanation", models.TextField()),
                (
                    "created_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "impact_review",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="profile_matches",
                        to="impacts.impactreview",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="impact_matches",
                        to="impacts.businessprofile",
                    ),
                ),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.AddConstraint(
            model_name="businessprofile",
            constraint=models.UniqueConstraint(
                fields=("owner", "name"),
                name="unique_business_profile_name_per_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="businessprofileterm",
            constraint=models.UniqueConstraint(
                fields=("profile", "term"),
                name="unique_term_per_business_profile",
            ),
        ),
        migrations.AddIndex(
            model_name="profileimpactmatch",
            index=models.Index(
                fields=["profile", "outcome", "created_at"],
                name="profile_match_outcome_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="profileimpactmatch",
            index=models.Index(
                fields=["impact_review", "outcome"],
                name="review_match_outcome_idx",
            ),
        ),
    ]
