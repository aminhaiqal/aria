import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("discovery", "0004_monitoredresource_retirement"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResourceStructureIncident",
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
                ("error_code", models.CharField(max_length=128)),
                ("error_message", models.TextField()),
                ("expected_selectors", models.JSONField(default=list)),
                ("requested_url", models.URLField(max_length=2048)),
                ("final_url", models.URLField(max_length=2048)),
                ("response_status", models.PositiveSmallIntegerField()),
                ("content_type", models.CharField(blank=True, max_length=255)),
                ("byte_size", models.PositiveBigIntegerField(default=0)),
                (
                    "content_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                (
                    "structure_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                ("structure_sample", models.JSONField(default=list)),
                ("redirect_chain", models.JSONField(blank=True, default=list)),
                ("pause_until", models.DateTimeField(db_index=True)),
                (
                    "detected_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "resource",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="structure_incidents",
                        to="discovery.monitoredresource",
                    ),
                ),
                (
                    "resource_run",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="structure_incident",
                        to="discovery.resourcerun",
                    ),
                ),
            ],
            options={
                "ordering": ("-detected_at", "-id"),
                "indexes": [
                    models.Index(
                        fields=["resource", "detected_at"],
                        name="discovery_r_resourc_45c543_idx",
                    ),
                    models.Index(
                        fields=["structure_sha256", "detected_at"],
                        name="discovery_r_structu_b7b24d_idx",
                    ),
                ],
            },
        ),
    ]
