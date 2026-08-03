from django.apps import AppConfig


class DiscoveryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aria.discovery"

    def ready(self) -> None:
        from aria.discovery.connectors import register_connector
        from aria.discovery.html_connector import ConfiguredHTMLListingConnector
        from aria.sources.models import SourceEndpoint

        register_connector(
            SourceEndpoint.ConnectorType.HTML_LISTING,
            ConfiguredHTMLListingConnector(),
        )
