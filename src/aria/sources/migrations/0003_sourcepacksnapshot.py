import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sources", "0002_sourceendpoint_consecutive_failures_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="SourcePackSnapshot",
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
                ("pack_slug", models.SlugField(max_length=128)),
                ("schema_version", models.PositiveIntegerField()),
                ("pack_version", models.PositiveIntegerField()),
                (
                    "checksum",
                    models.CharField(
                        max_length=64,
                        unique=True,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                ("definition", models.JSONField()),
                ("applied_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "endpoint",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="source_pack_snapshots",
                        to="sources.sourceendpoint",
                    ),
                ),
            ],
            options={
                "ordering": ("-applied_at", "-id"),
                "indexes": [
                    models.Index(
                        fields=["endpoint", "applied_at"],
                        name="sources_sou_endpoin_3b301e_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("pack_slug", "pack_version"),
                        name="unique_source_pack_version",
                    )
                ],
            },
        ),
    ]
