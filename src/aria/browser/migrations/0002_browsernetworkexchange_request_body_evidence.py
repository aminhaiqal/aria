import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("browser", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="browsernetworkexchange",
            name="request_body_bytes",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="browsernetworkexchange",
            name="request_body_sha256",
            field=models.CharField(
                blank=True,
                max_length=64,
                validators=[django.core.validators.RegexValidator("^$|^[0-9a-f]{64}$")],
            ),
        ),
    ]
