import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from django.db import IntegrityError, transaction
from django.utils import timezone

from aria.artifacts.models import ArtifactDerivative
from aria.artifacts.storage import get_artifact_store
from aria.browser.models import (
    BrowserCapture,
    BrowserNetworkExchange,
    SourceAdmissionAssessment,
    SourceAdmissionPromotion,
)
from aria.discovery.html_connector import configured_link_target, normalize_publication_url
from aria.events.models import PipelineEvent
from aria.events.services import record_audit_event, record_pipeline_event
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
    candidate_set_sha256: str
    candidate_count: int
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
            "candidate_set_sha256": self.candidate_set_sha256,
            "candidate_count": self.candidate_count,
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
        target = configured_link_target(link, configuration)
        if not target:
            continue
        url = normalize_publication_url(capture.requested_url, target)
        if _url_qualifies(url, capture.endpoint, configuration):
            urls.add(url)
    return len(urls)


def capture_has_bounded_network(capture: BrowserCapture) -> bool:
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
    return allowed_post_count > 0 if approved_paths else True


def _candidate_has_downstream_lineage(candidate) -> bool:
    for observation in candidate.artifact_observations.select_related("raw_artifact"):
        artifact = observation.raw_artifact
        artifact_ids = {artifact.id}
        artifact_ids.update(
            ArtifactDerivative.objects.filter(
                source_artifact=artifact,
                transformation_type=ArtifactDerivative.TransformationType.OCR_SEARCHABLE_PDF,
            ).values_list("derived_artifact_id", flat=True)
        )
        extracted = set(
            ExtractionRun.objects.filter(
                raw_artifact_id__in=artifact_ids,
                status=ExtractionRun.Status.SUCCEEDED,
            ).values_list("raw_artifact_id", flat=True)
        )
        graphed = set(
            GraphNode.objects.filter(
                node_type=GraphNode.NodeType.ARTIFACT,
                source_type="raw_artifact",
                source_id__in=artifact_ids,
            ).values_list("source_id", flat=True)
        )
        if extracted & graphed:
            return True
    return False


def _candidate_set_hash(urls: tuple[str, ...]) -> str:
    if not urls:
        return ""
    serialized = json.dumps(urls, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


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
    source_pack_installed = endpoint.source_pack_snapshots.exists()

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
        capture_has_bounded_network(capture) for capture in captures
    )
    candidate_precision = enough_captures and all(
        urls and all(_url_qualifies(url, endpoint, configuration) for url in urls)
        for urls in candidate_sets
    )
    try:
        static_counts = [_original_candidate_count(capture, configuration) for capture in captures]
    except Exception:
        static_counts = [-1 for _capture in captures]
    browser_lift = enough_captures and all(
        count >= 0 and len(urls) > count
        for count, urls in zip(static_counts, candidate_sets, strict=True)
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
            "source_pack_snapshot",
            source_pack_installed,
            "an immutable repository source-pack snapshot is installed",
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
            "network stayed inside configured allowlists and no resource limit fired",
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
        candidate_set_sha256=_candidate_set_hash(candidate_sets[-1] if candidate_sets else ()),
        candidate_count=len(candidate_sets[-1]) if candidate_sets else 0,
        gates=gates,
    )


def _report_signature(report: BrowserAdmissionReport, pack_checksum: str) -> str:
    payload = {**report.as_dict(), "source_pack_checksum": pack_checksum}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


@transaction.atomic
def assess_browser_admission(
    endpoint: SourceEndpoint,
    *,
    required_captures: int = 2,
) -> tuple[SourceAdmissionAssessment, bool]:
    report = evaluate_browser_admission(endpoint, required_captures=required_captures)
    snapshot = endpoint.source_pack_snapshots.order_by("-applied_at", "-id").first()
    signature = _report_signature(report, snapshot.checksum if snapshot else "")
    existing = SourceAdmissionAssessment.objects.filter(report_signature=signature).first()
    if existing:
        return existing, False
    assessment = SourceAdmissionAssessment(
        endpoint=endpoint,
        source_pack_snapshot=snapshot,
        admission_profile=SourceAdmissionAssessment.Profile.BROWSER_LISTING,
        status=(
            SourceAdmissionAssessment.Status.READY
            if report.ready_for_promotion
            else SourceAdmissionAssessment.Status.INCOMPLETE
        ),
        report_signature=signature,
        required_evidence_count=required_captures,
        evaluated_capture_ids=list(report.evaluated_capture_ids),
        candidate_set_sha256=report.candidate_set_sha256,
        candidate_count=report.candidate_count,
        gates=[asdict(gate) for gate in report.gates],
    )
    assessment.full_clean()
    try:
        assessment.save()
    except IntegrityError:
        return SourceAdmissionAssessment.objects.get(report_signature=signature), False
    payload = {
        "assessment_id": str(assessment.id),
        "source_pack_checksum": snapshot.checksum if snapshot else "",
        **report.as_dict(),
    }
    record_pipeline_event(
        event_type="browser.admission.evaluated",
        aggregate_type="source_endpoint",
        aggregate_id=endpoint.id,
        payload=payload,
    )
    return assessment, True


@transaction.atomic
def promote_admitted_source(
    endpoint: SourceEndpoint,
    assessment: SourceAdmissionAssessment,
    *,
    actor_identifier: str = "management_command",
) -> SourceAdmissionPromotion:
    endpoint = SourceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    assessment = SourceAdmissionAssessment.objects.select_related("source_pack_snapshot").get(
        pk=assessment.pk
    )
    if assessment.endpoint_id != endpoint.id:
        raise ValueError("Admission assessment does not belong to this endpoint.")
    if endpoint.is_enabled:
        raise ValueError("Source endpoint is already enabled.")
    current, _ = assess_browser_admission(
        endpoint,
        required_captures=assessment.required_evidence_count,
    )
    if current.report_signature != assessment.report_signature:
        raise ValueError("Admission evidence changed; inspect a fresh assessment.")
    if current.status != SourceAdmissionAssessment.Status.READY:
        failed = ", ".join(gate["name"] for gate in current.gates if not gate["passed"])
        raise ValueError(f"Admission gates failed: {failed}.")
    endpoint.is_enabled = True
    endpoint.next_poll_at = timezone.now() + timedelta(minutes=endpoint.polling_interval_minutes)
    endpoint.health_state = SourceEndpoint.HealthState.HEALTHY
    endpoint.save(update_fields=("is_enabled", "next_poll_at", "health_state", "updated_at"))
    promotion = SourceAdmissionPromotion(
        endpoint=endpoint,
        assessment=current,
        next_poll_at=endpoint.next_poll_at,
        actor_identifier=actor_identifier,
    )
    promotion.full_clean()
    promotion.save()
    payload = {
        "promotion_id": str(promotion.id),
        "assessment_id": str(current.id),
        "report_signature": current.report_signature,
        "source_pack_checksum": (
            current.source_pack_snapshot.checksum if current.source_pack_snapshot else ""
        ),
        "next_poll_at": endpoint.next_poll_at.isoformat(),
    }
    record_audit_event(
        action="browser.source.promoted",
        target_type="source_endpoint",
        target_id=endpoint.id,
        actor_type="system",
        actor_identifier=actor_identifier,
        details=payload,
    )
    record_pipeline_event(
        event_type="browser.source.promoted",
        aggregate_type="source_endpoint",
        aggregate_id=endpoint.id,
        payload=payload,
    )
    return promotion
