from django.apps import AppConfig


class AccessConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aria.access"

    def ready(self) -> None:
        from aria.access import signals  # noqa: F401
