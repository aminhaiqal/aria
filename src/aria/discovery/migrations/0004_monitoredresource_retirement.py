from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("discovery", "0003_monitoredresource_resourceobservation_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="monitoredresource",
            name="retired_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="monitoredresource",
            name="retirement_actor_identifier",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="monitoredresource",
            name="retirement_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddConstraint(
            model_name="monitoredresource",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        retired_at__isnull=True,
                        retirement_reason="",
                        retirement_actor_identifier="",
                    )
                    | (
                        models.Q(
                            is_enabled=False,
                            health_state="disabled",
                            next_poll_at__isnull=True,
                        )
                        & ~models.Q(retirement_reason="")
                        & ~models.Q(retirement_actor_identifier="")
                    )
                ),
                name="monitored_resource_retirement_contract",
            ),
        ),
    ]
