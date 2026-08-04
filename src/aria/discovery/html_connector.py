import hashlib
from collections.abc import Callable
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from aria.discovery.connectors import CandidateData, DiscoveryResult
from aria.discovery.models import DiscoveredCandidate
from aria.discovery.services import conditional_headers_from_cursor
from aria.fetching.client import SafeHttpClient, get_default_http_client, hostname_is_allowed
from aria.sources.models import ConnectorConfiguration, SourceEndpoint


class ConnectorStructureChanged(RuntimeError):
    pass


def normalize_publication_url(base_url: str, href: str) -> str:
    absolute = urljoin(base_url, href)
    parsed = urlsplit(absolute)
    hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    return urlunsplit((parsed.scheme.lower(), hostname, parsed.path or "/", parsed.query, ""))


class ConfiguredHTMLListingConnector:
    def __init__(
        self,
        client_factory: Callable[[], SafeHttpClient] = get_default_http_client,
    ):
        self.client_factory = client_factory

    def discover(
        self,
        endpoint: SourceEndpoint,
        cursor: dict | None,
    ) -> DiscoveryResult:
        connector_configuration = (
            ConnectorConfiguration.objects.filter(
                endpoint=endpoint,
                version=endpoint.connector_configuration_version,
                is_active=True,
            )
            .order_by("-created_at")
            .first()
        )
        if not connector_configuration:
            raise ConnectorStructureChanged(
                f"Endpoint {endpoint.id} has no active connector configuration version "
                f"{endpoint.connector_configuration_version}."
            )
        configuration = connector_configuration.configuration
        selector = configuration.get("link_selector", "a[href]")
        include_path_prefixes = tuple(configuration.get("include_path_prefixes", []))
        exclude_path_suffixes = tuple(configuration.get("exclude_path_suffixes", ["/feed/"]))
        document_extensions = {
            extension.lower()
            for extension in configuration.get(
                "document_extensions",
                [".pdf", ".doc", ".docx", ".csv", ".json", ".xml"],
            )
        }
        upload_path_prefixes = tuple(
            configuration.get("upload_path_prefixes", ["/wp-content/uploads/"])
        )
        max_candidates = int(configuration.get("max_candidates", 20))
        follow_detail_pages = bool(configuration.get("follow_detail_pages", False))
        detail_content_selector = configuration.get(
            "detail_content_selector", ".betterdocs-entry-content"
        )
        max_detail_pages = int(configuration.get("max_detail_pages", max_candidates))

        client = self.client_factory()
        conditional_headers = conditional_headers_from_cursor(cursor)
        try:
            response = client.fetch(
                endpoint.discovery_url,
                allowed_domains=endpoint.allowed_domains,
                headers=conditional_headers,
            )
            if response.status_code == 304:
                candidates = self._reuse_previous_candidates(endpoint, cursor)
                if not candidates:
                    raise ConnectorStructureChanged(
                        "The endpoint returned HTTP 304 without reusable prior candidates."
                    )
                return DiscoveryResult(
                    candidates=tuple(candidates),
                    response=response,
                    request_headers=conditional_headers,
                )
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                raise ConnectorStructureChanged(
                    f"Expected HTML discovery response, received '{content_type or 'unknown'}'."
                )

            soup = BeautifulSoup(response.content, "html.parser")
            source_url = normalize_publication_url(endpoint.discovery_url, endpoint.discovery_url)
            candidates_by_url: dict[str, CandidateData] = {}
            for link in soup.select(selector):
                href = link.get("href")
                if not isinstance(href, str) or not href.strip():
                    continue
                canonical_url = normalize_publication_url(endpoint.discovery_url, href.strip())
                parsed = urlsplit(canonical_url)
                if parsed.scheme != "https" or not parsed.hostname:
                    continue
                if not hostname_is_allowed(parsed.hostname, endpoint.allowed_domains):
                    continue
                if canonical_url == source_url:
                    continue
                if include_path_prefixes and not any(
                    parsed.path.startswith(prefix) for prefix in include_path_prefixes
                ):
                    continue
                if any(parsed.path.endswith(suffix) for suffix in exclude_path_suffixes):
                    continue
                if any(parsed.path.startswith(prefix) for prefix in upload_path_prefixes):
                    if PurePosixPath(parsed.path).suffix.lower() not in document_extensions:
                        continue
                title = " ".join(link.get_text(" ", strip=True).split())
                candidates_by_url.setdefault(
                    canonical_url,
                    CandidateData(
                        discovered_url=canonical_url,
                        canonical_url=canonical_url,
                        fingerprint=hashlib.sha256(canonical_url.encode("utf-8")).hexdigest(),
                        metadata_hints={
                            "title": title,
                            "source_listing": endpoint.discovery_url,
                        },
                    ),
                )

            candidates = list(candidates_by_url.values())
            candidates.sort(
                key=lambda item: (
                    PurePosixPath(urlsplit(item.canonical_url).path).suffix.lower()
                    not in document_extensions,
                )
            )
            candidates = candidates[:max_candidates]
            if follow_detail_pages:
                expanded: list[CandidateData] = []
                detail_count = 0
                for candidate in candidates:
                    suffix = PurePosixPath(urlsplit(candidate.canonical_url).path).suffix.lower()
                    if suffix in document_extensions or detail_count >= max_detail_pages:
                        expanded.append(candidate)
                        continue
                    detail_count += 1
                    detail_response = client.fetch(
                        candidate.canonical_url,
                        allowed_domains=endpoint.allowed_domains,
                    )
                    detail_type = detail_response.headers.get("content-type", "").lower()
                    if "html" not in detail_type:
                        expanded.append(candidate)
                        continue
                    detail_soup = BeautifulSoup(detail_response.content, "html.parser")
                    detail_root = detail_soup.select_one(detail_content_selector)
                    linked_by_url: dict[str, CandidateData] = {}
                    if detail_root is not None:
                        for link in detail_root.select("a[href]"):
                            href = link.get("href")
                            if not isinstance(href, str) or not href.strip():
                                continue
                            linked_url = normalize_publication_url(
                                candidate.canonical_url, href.strip()
                            )
                            parsed = urlsplit(linked_url)
                            if (
                                parsed.scheme != "https"
                                or not parsed.hostname
                                or not hostname_is_allowed(
                                    parsed.hostname, endpoint.allowed_domains
                                )
                                or PurePosixPath(parsed.path).suffix.lower()
                                not in document_extensions
                            ):
                                continue
                            title = " ".join(link.get_text(" ", strip=True).split())
                            fingerprint_basis = f"{candidate.canonical_url}\n{linked_url}".encode()
                            linked_by_url.setdefault(
                                linked_url,
                                CandidateData(
                                    discovered_url=linked_url,
                                    canonical_url=linked_url,
                                    fingerprint=hashlib.sha256(fingerprint_basis).hexdigest(),
                                    metadata_hints={
                                        "title": title or candidate.metadata_hints.get("title", ""),
                                        "source_listing": endpoint.discovery_url,
                                        "source_detail_page": candidate.canonical_url,
                                        "document_identity_url": candidate.canonical_url,
                                    },
                                ),
                            )
                    expanded.extend(list(linked_by_url.values()) or [candidate])
                candidates = expanded[:max_candidates]
        finally:
            client.close()

        if not candidates:
            raise ConnectorStructureChanged(
                f"Selector '{selector}' produced no qualifying publication links."
            )
        return DiscoveryResult(
            candidates=tuple(candidates),
            response=response,
            request_headers=conditional_headers,
        )

    @staticmethod
    def _reuse_previous_candidates(
        endpoint: SourceEndpoint,
        cursor: dict | None,
    ) -> list[CandidateData]:
        if not cursor or not cursor.get("source_run_id"):
            return []
        candidates = (
            DiscoveredCandidate.objects.filter(
                endpoint=endpoint,
                observations__source_run_id=cursor["source_run_id"],
            )
            .distinct()
            .order_by("canonical_url", "id")
        )
        return [
            CandidateData(
                discovered_url=candidate.discovered_url,
                canonical_url=candidate.canonical_url,
                external_identifier=candidate.external_identifier,
                fingerprint=candidate.fingerprint,
                metadata_hints=candidate.metadata_hints,
            )
            for candidate in candidates
        ]
