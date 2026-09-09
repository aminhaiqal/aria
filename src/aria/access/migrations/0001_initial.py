from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    operations = [
        migrations.CreateModel(
            name="OperatorBoundary",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
            ],
            options={
                "managed": False,
                "default_permissions": (),
                "permissions": (
                    ("access_console", "Can access the operator console"),
                    ("operate_sources", "Can operate source admission and polling"),
                    ("operate_workflows", "Can retry workflows and request summaries"),
                    ("review_changes", "Can review textual changes"),
                    ("review_impacts", "Can review regulatory impacts"),
                    ("publish_changes", "Can publish reviewed textual changes"),
                    ("publish_impacts", "Can publish reviewed regulatory impacts"),
                    ("view_audit_log", "Can view the operator audit log"),
                ),
            },
        ),
    ]
