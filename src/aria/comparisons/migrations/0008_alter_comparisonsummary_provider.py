from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("comparisons", "0007_reviewedchangepublication_published_by")]

    operations = [
        migrations.AlterField(
            model_name="comparisonsummary",
            name="provider",
            field=models.CharField(default="openrouter", max_length=64),
        )
    ]
