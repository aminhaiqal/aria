from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("artifacts", "0004_artifactobservation_content_changed"),
    ]

    operations = [
        migrations.AlterField(
            model_name="artifactderivative",
            name="transformation_type",
            field=models.CharField(
                choices=[
                    ("ocr_searchable_pdf", "OCR searchable PDF"),
                    ("ocr_text_sidecar", "OCR text sidecar"),
                    ("browser_rendered_dom", "Browser-rendered DOM"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
