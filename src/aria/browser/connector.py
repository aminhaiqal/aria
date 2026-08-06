from aria.browser.services import active_connector_configuration, capture_source_run
from aria.discovery.connectors import DiscoveryResult
from aria.discovery.html_connector import (
    ConfiguredHTMLListingConnector,
    ConnectorStructureChanged,
)


class _CapturedResponseClient:
    def __init__(self, response):
        self.response = response
        self.used = False

    def fetch(self, *_args, **_kwargs):
        if self.used:
            raise ConnectorStructureChanged(
                "JavaScript listings cannot follow detail pages in the browser capture task."
            )
        self.used = True
        return self.response

    def close(self) -> None:
        return None


class ConfiguredJavaScriptListingConnector:
    def __init__(self, *, runner=None, store=None):
        self.runner = runner
        self.store = store

    def discover(self, source_run) -> DiscoveryResult:
        configuration = active_connector_configuration(source_run.endpoint)
        if configuration.get("follow_detail_pages"):
            raise ValueError(
                "JavaScript listing captures cannot follow detail pages; reconcile them as "
                "independent monitored resources."
            )
        _, response = capture_source_run(
            source_run,
            runner=self.runner,
            store=self.store,
        )
        client = _CapturedResponseClient(response)
        parser = ConfiguredHTMLListingConnector(client_factory=lambda: client)
        parsed = parser.discover(source_run.endpoint, None)
        return DiscoveryResult(
            candidates=parsed.candidates,
            response=response,
            request_headers={},
        )
