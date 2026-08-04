from dataclasses import dataclass, field
from typing import Protocol

from aria.fetching.client import FetchResponse
from aria.sources.models import SourceEndpoint


@dataclass(frozen=True)
class CandidateData:
    discovered_url: str
    fingerprint: str
    canonical_url: str = ""
    external_identifier: str = ""
    metadata_hints: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveryResult:
    candidates: tuple[CandidateData, ...]
    response: FetchResponse
    request_headers: dict[str, str] = field(default_factory=dict)


class Connector(Protocol):
    def discover(
        self,
        endpoint: SourceEndpoint,
        cursor: dict | None,
    ) -> DiscoveryResult: ...


class ConnectorNotRegistered(LookupError):
    pass


_registry: dict[str, Connector] = {}


def register_connector(connector_type: str, connector: Connector) -> None:
    _registry[connector_type] = connector


def get_connector(connector_type: str) -> Connector:
    try:
        return _registry[connector_type]
    except KeyError as error:
        raise ConnectorNotRegistered(
            f"No connector is registered for type '{connector_type}'."
        ) from error
