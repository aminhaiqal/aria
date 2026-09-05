import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0001_initial"),
        ("impacts", "0005_business_profile_matching"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReviewedImpactPublication",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "impact_review",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="publication",
                        to="impacts.impactreview",
                    ),
                ),
                (
                    "pipeline_event",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reviewed_impact_publication",
                        to="events.pipelineevent",
                    ),
                ),
                (
                    "published_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reviewed_impact_publications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
    ]
