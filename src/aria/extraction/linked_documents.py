import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.db import transaction

from aria.collections.models import PublicationCollection
from aria.discovery.connectors import CandidateData
from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.discovery.services import (
    create_source_run,
    mark_source_run_completed,
    mark_source_run_started,
    observe_candidate,
)
from aria.documents.models import DocumentVersion
from aria.fetching.client import hostname_is_allowed
from aria.fetching.models import FetchAttempt
from aria.sources.models import SourceEndpoint


class LinkedDocumentRoutingError(RuntimeError):
    pass


@dataclass(frozen=True)
class LinkedDocumentRouteResult:
    source_run: SourceRun
    candidates: tuple[DiscoveredCandidate, ...]
    created: bool
    queued_count: int


def _latest_versions(collection: PublicationCollection) -> list[DocumentVersion]:
    return list(
        DocumentVersion.objects.filter(
            identity__collection=collection,
            identity__superseded_by__isnull=True,
        )
        .order_by("identity_id", "-created_at", "-id")
        .distinct("identity_id")
    )


def _route_specifications(
    collection: PublicationCollection,
    endpoint: SourceEndpoint,
) -> list[dict[str, str]]:
    specifications: list[dict[str, str]] = []
    for version in _latest_versions(collection):
        for link in version.normalized_metadata.get("primary_document_links", []):
            target_url = str(link.get("url", "")).strip()
            parsed = urlsplit(target_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise LinkedDocumentRoutingError(
                    f"Document version {version.id} has an unsafe primary-document URL."
                )
            if not hostname_is_allowed(parsed.hostname, endpoint.allowed_domains):
                raise LinkedDocumentRoutingError(
                    f"Document version {version.id} links outside the endpoint allowlist."
                )
            specifications.append(
                {
                    "identity_url": version.canonical_url,
                    "target_url": target_url,
                    "title": str(link.get("title", "")),
                    "source_version_id": str(version.id),
                }
            )
    specifications.sort(key=lambda item: (item["identity_url"], item["target_url"]))
    return specifications


@transaction.atomic
def route_linked_publications(
    collection: PublicationCollection,
    *,
    queue_fetches: bool = False,
) -> LinkedDocumentRouteResult:
    try:
        endpoint = collection.source_endpoints.get(
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            is_enabled=True,
        )
    except SourceEndpoint.DoesNotExist as error:
        raise LinkedDocumentRoutingError(
            "The collection has no enabled HTML listing endpoint."
        ) from error
    except SourceEndpoint.MultipleObjectsReturned as error:
        raise LinkedDocumentRoutingError(
            "The collection has multiple enabled HTML listing endpoints."
        ) from error

    specifications = _route_specifications(collection, endpoint)
    if not specifications:
        raise LinkedDocumentRoutingError(
            "The latest document versions contain no primary-document links."
        )
    serialized = json.dumps(specifications, sort_keys=True, separators=(",", ":"))
    route_fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    source_run, created = create_source_run(
        endpoint,
        trigger=SourceRun.Trigger.REPLAY,
        idempotency_key=f"linked-publications-v2:{endpoint.id}:{route_fingerprint}",
    )
    candidates: list[DiscoveredCandidate] = []
    if created:
        mark_source_run_started(source_run)
        for specification in specifications:
            fingerprint_basis = (
                f"{specification['identity_url']}\n{specification['target_url']}".encode()
            )
            candidate, _ = observe_candidate(
                source_run,
                CandidateData(
                    discovered_url=specification["target_url"],
                    canonical_url=specification["target_url"],
                    fingerprint=hashlib.sha256(fingerprint_basis).hexdigest(),
                    metadata_hints={
                        "title": specification["title"],
                        "source_listing": endpoint.discovery_url,
                        "source_detail_page": specification["identity_url"],
                        "source_document_version_id": specification["source_version_id"],
                        "document_identity_url": specification["identity_url"],
                    },
                ),
            )
            candidates.append(candidate)
        mark_source_run_completed(
            source_run,
            cursor_after={
                "strategy": "archived_primary_document_links_v2",
                "route_fingerprint": route_fingerprint,
            },
        )
    else:
        candidates = list(
            DiscoveredCandidate.objects.filter(observations__source_run=source_run)
            .distinct()
            .order_by("canonical_url")
        )

    queued_count = 0
    if queue_fetches:
        from aria.fetching.tasks import fetch_candidate

        for candidate in candidates:
            completed = FetchAttempt.objects.filter(
                candidate=candidate,
                source_run=source_run,
                status__in=(
                    FetchAttempt.Status.SUCCEEDED,
                    FetchAttempt.Status.NOT_MODIFIED,
                ),
            ).exists()
            if not completed:
                transaction.on_commit(
                    lambda candidate_id=str(candidate.id),
                    source_run_id=str(source_run.id): fetch_candidate.delay(
                        candidate_id, source_run_id
                    )
                )
                queued_count += 1
    return LinkedDocumentRouteResult(
        source_run=source_run,
        candidates=tuple(candidates),
        created=created,
        queued_count=queued_count,
    )
