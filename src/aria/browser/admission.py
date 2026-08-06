from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from aria.artifacts.storage import get_artifact_store
from aria.browser.models import BrowserCapture, BrowserNetworkExchange
from aria.discovery.html_connector import normalize_publication_url
from aria.events.models import PipelineEvent
from aria.extraction.models import ExtractionRun
from aria.fetching.client import hostname_is_allowed
from aria.knowledge.models import GraphNode
from aria.sources.models import SourceEndpoint


@dataclass(frozen=True)
class AdmissionGate:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class BrowserAdmissionReport:
    endpoint_id: str
    endpoint_name: str
    required_captures: int
    evaluated_capture_ids: tuple[str, ...]
    gates: tuple[AdmissionGate, ...]

    @property
    def ready_for_promotion(self) -> bool:
        return all(gate.passed for gate in self.gates)

    def as_dict(self) -> dict:
        return {
            "endpoint_id": self.endpoint_id,
            "endpoint_name": self.endpoint_name,
            "required_captures": self.required_captures,
            "evaluated_capture_ids": list(self.evaluated_capture_ids),
            "ready_for_promotion": self.ready_for_promotion,
            "gates": [asdict(gate) for gate in self.gates],
        }


def _candidate_urls(source_run) -> tuple[str, ...]:
    return tuple(
        sorted(source_run.candidate_observations.values_list("candidate__canonical_url", flat=True))
    )


def _url_qualifies(url: str, endpoint: SourceEndpoint, configuration: dict) -> bool:
    parsed = urlsplit(url)
    include_path_prefixes = tuple(configuration.get("include_path_prefixes", []))
    extensions = {
        str(value).lower() for value in configuration.get("document_extensions", [".pdf"])
    }
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and hostname_is_allowed(parsed.hostname, endpoint.allowed_domains)
        and (
            not include_path_prefixes
            or any(parsed.path.startswith(prefix) for prefix in include_path_prefixes)
        )
        and PurePosixPath(parsed.path).suffix.lower() in extensions
    )


def _original_candidate_count(capture: BrowserCapture, configuration: dict) -> int:
    artifact = capture.original_artifact
    if artifact is None:
        return 0
    content = get_artifact_store(artifact.storage_backend).read(artifact.storage_key)
    soup = BeautifulSoup(content, "html.parser")
    urls = set()
    for link in soup.select(configuration.get("link_selector", "a[href]")):
        href = link.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        url = normalize_publication_url(capture.requested_url, href.strip())
        if _url_qualifies(url, capture.endpoint, configuration):
            urls.add(url)
    return len(urls)


def _capture_has_bounded_network(capture: BrowserCapture) -> bool:
    allowed_post_count = 0
    approved_paths = frozenset(capture.configuration.get("read_only_post_paths", []))
    max_body_bytes = int(capture.configuration.get("max_request_body_bytes", 0))
    fatal_block_reasons = {
        "request_limit_exceeded",
        "redirect_limit_exceeded",
        "request_body_limit_exceeded",
    }
    for exchange in capture.network_exchanges.all():
        if exchange.block_reason in fatal_block_reasons:
            return False
        if exchange.disposition != BrowserNetworkExchange.Disposition.ALLOWED:
            continue
        if exchange.method != "POST":
            continue
        parsed = urlsplit(exchange.requested_url)
        if not (
            parsed.hostname
            and hostname_is_allowed(parsed.hostname, capture.endpoint.allowed_domains)
            and parsed.path in approved_paths
            and not parsed.query
            and exchange.request_body_bytes <= max_body_bytes
            and bool(exchange.request_body_bytes) == bool(exchange.request_body_sha256)
        ):
            return False
        allowed_post_count += 1
    return allowed_post_count > 0


def _candidate_has_downstream_lineage(candidate) -> bool:
    for observation in candidate.artifact_observations.select_related("raw_artifact"):
        artifact = observation.raw_artifact
        if not artifact.extraction_runs.filter(status=ExtractionRun.Status.SUCCEEDED).exists():
            continue
        if GraphNode.objects.filter(
            node_type=GraphNode.NodeType.ARTIFACT,
            source_type="raw_artifact",
            source_id=artifact.id,
        ).exists():
            return True
    return False


def evaluate_browser_admission(
    endpoint: SourceEndpoint,
    *,
    required_captures: int = 2,
) -> BrowserAdmissionReport:
    if required_captures < 2 or required_captures > 5:
        raise ValueError("required_captures must be between 2 and 5.")
    captures = list(
        BrowserCapture.objects.filter(
            endpoint=endpoint,
            status=BrowserCapture.Status.COMPLETED,
            source_run__status="completed",
        )
        .select_related(
            "source_run",
            "original_artifact",
            "rendered_artifact",
            "rendered_derivative",
        )
        .prefetch_related("network_exchanges")
        .order_by("-finished_at", "-created_at")[:required_captures]
    )
    captures.reverse()
    candidate_sets = [_candidate_urls(capture.source_run) for capture in captures]
    configuration = (
        endpoint.connector_configurations.filter(
            version=endpoint.connector_configuration_version,
            is_active=True,
        )
        .values_list("configuration", flat=True)
        .first()
        or {}
    )

    enough_captures = len(captures) == required_captures
    immutable_evidence = enough_captures and all(
        capture.original_artifact_id
        and capture.rendered_artifact_id
        and (
            capture.original_artifact_id == capture.rendered_artifact_id
            or capture.rendered_derivative_id
        )
        for capture in captures
    )
    bounded_network = enough_captures and all(
        _capture_has_bounded_network(capture) for capture in captures
    )
    candidate_precision = enough_captures and all(
        urls and all(_url_qualifies(url, endpoint, configuration) for url in urls)
        for urls in candidate_sets
    )
    try:
        static_counts = [_original_candidate_count(capture, configuration) for capture in captures]
    except Exception:
        static_counts = [-1 for _capture in captures]
    browser_lift = (
        enough_captures and all(count == 0 for count in static_counts) and all(candidate_sets)
    )
    repeatable = enough_captures and len(set(candidate_sets)) == 1

    downstream_total = 0
    downstream_ready = 0
    if captures:
        candidates = [
            observation.candidate
            for observation in captures[-1].source_run.candidate_observations.select_related(
                "candidate"
            )
        ]
        downstream_total = len(candidates)
        downstream_ready = sum(
            _candidate_has_downstream_lineage(candidate) for candidate in candidates
        )
    downstream_lineage = bool(downstream_total) and downstream_ready == downstream_total
    controlled_activation = (
        not endpoint.is_enabled and endpoint.next_poll_at is None
    ) or PipelineEvent.objects.filter(
        event_type="browser.source.promoted",
        aggregate_type="source_endpoint",
        aggregate_id=endpoint.id,
    ).exists()

    gates = (
        AdmissionGate(
            "controlled_activation",
            controlled_activation,
            "source is disabled or has a recorded gate-backed promotion",
        ),
        AdmissionGate(
            "repeat_capture_count",
            enough_captures,
            f"{len(captures)}/{required_captures} completed captures",
        ),
        AdmissionGate(
            "immutable_evidence",
            immutable_evidence,
            "original/rendered artifacts and derivative lineage are complete",
        ),
        AdmissionGate(
            "bounded_network",
            bounded_network,
            "only the configured official POST was allowed; no resource limit fired",
        ),
        AdmissionGate(
            "browser_only_lift",
            browser_lift,
            f"raw qualifying counts={static_counts}; rendered counts="
            f"{[len(urls) for urls in candidate_sets]}",
        ),
        AdmissionGate(
            "candidate_precision",
            candidate_precision,
            "all candidates are HTTPS official-domain documents in the configured path",
        ),
        AdmissionGate(
            "repeatability",
            repeatable,
            "candidate URL sets match across the evaluated captures",
        ),
        AdmissionGate(
            "downstream_lineage",
            downstream_lineage,
            f"{downstream_ready}/{downstream_total} latest candidates have artifact, "
            "successful extraction, and knowledge-graph lineage",
        ),
    )
    return BrowserAdmissionReport(
        endpoint_id=str(endpoint.id),
        endpoint_name=endpoint.name,
        required_captures=required_captures,
        evaluated_capture_ids=tuple(str(capture.id) for capture in captures),
        gates=gates,
    )
