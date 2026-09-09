from django.apps import AppConfig


class HealthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aria.health"

    def ready(self) -> None:
        from aria.health import checks  # noqa: F401
