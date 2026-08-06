import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation
from aria.browser.models import SourceAdmissionAssessment, SourceAdmissionPromotion
from aria.discovery.models import EndpointObservation, SourceRun
from aria.events.services import record_audit_event, record_pipeline_event
from aria.fetching.client import hostname_is_allowed
from aria.reliability.services import collect_source_reliability
from aria.sources.models import ConnectorConfiguration, SourceEndpoint


@dataclass(frozen=True)
class StaticAdmissionGate:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class StaticAdmissionReport:
    endpoint_id: str
    endpoint_name: str
    required_runs: int
    evaluated_source_run_ids: tuple[str, ...]
    candidate_set_sha256: str
    candidate_count: int
    gates: tuple[StaticAdmissionGate, ...]

    @property
    def ready_for_promotion(self) -> bool:
        return all(gate.passed for gate in self.gates)

    def as_dict(self) -> dict:
        return {
            "endpoint_id": self.endpoint_id,
            "endpoint_name": self.endpoint_name,
            "admission_profile": SourceAdmissionAssessment.Profile.STATIC_LISTING,
            "required_runs": self.required_runs,
            "evaluated_source_run_ids": list(self.evaluated_source_run_ids),
            "candidate_set_sha256": self.candidate_set_sha256,
            "candidate_count": self.candidate_count,
            "ready_for_promotion": self.ready_for_promotion,
            "gates": [asdict(gate) for gate in self.gates],
        }


def _candidate_urls(source_run: SourceRun) -> tuple[str, ...]:
    return tuple(
        sorted(source_run.candidate_observations.values_list("candidate__canonical_url", flat=True))
    )


def _candidate_set_hash(urls: tuple[str, ...]) -> str:
    if not urls:
        return ""
    serialized = json.dumps(urls, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _url_qualifies(url: str, endpoint: SourceEndpoint, configuration: dict) -> bool:
    parsed = urlsplit(url)
    include_path_prefixes = tuple(configuration.get("include_path_prefixes", []))
    extensions = {
        str(value).lower()
        for value in configuration.get(
            "document_extensions",
            [".pdf", ".doc", ".docx", ".csv", ".json", ".xml"],
        )
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


def _document_content_types(configuration: dict) -> set[str]:
    configured = configuration.get("document_content_types")
    if configured:
        return {str(value).split(";", 1)[0].strip().lower() for value in configured}
    extensions = {
        str(value).lower() for value in configuration.get("document_extensions", [".pdf"])
    }
    content_types = set()
    if ".pdf" in extensions:
        content_types.add("application/pdf")
    if extensions & {".doc", ".docx"}:
        content_types.update(
            {
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        )
    if ".csv" in extensions:
        content_types.add("text/csv")
    if ".json" in extensions:
        content_types.add("application/json")
    if ".xml" in extensions:
        content_types.update({"application/xml", "text/xml"})
    return content_types


def evaluate_static_admission(
    endpoint: SourceEndpoint,
    *,
    required_runs: int = 2,
) -> StaticAdmissionReport:
    if endpoint.connector_type != SourceEndpoint.ConnectorType.HTML_LISTING:
        raise ValueError("Static admission currently requires an HTML listing endpoint.")
    if endpoint.requires_javascript:
        raise ValueError("JavaScript endpoints must use browser admission.")
    if not 2 <= required_runs <= 5:
        raise ValueError("required_runs must be between 2 and 5.")
    runs = list(
        SourceRun.objects.filter(
            endpoint=endpoint,
            status=SourceRun.Status.COMPLETED,
            resource_run__isnull=True,
        ).order_by("-finished_at", "-created_at", "-id")[:required_runs]
    )
    runs.reverse()
    candidate_sets = [_candidate_urls(run) for run in runs]
    configuration_record = (
        ConnectorConfiguration.objects.filter(
            endpoint=endpoint,
            version=endpoint.connector_configuration_version,
            is_active=True,
        )
        .order_by("-created_at")
        .first()
    )
    configuration = configuration_record.configuration if configuration_record else {}
    observations = {
        observation.source_run_id: observation
        for observation in EndpointObservation.objects.filter(source_run__in=runs)
    }
    snapshot = endpoint.source_pack_snapshots.order_by("-applied_at", "-id").first()
    enough_runs = len(runs) == required_runs
    controlled_activation = (
        not endpoint.is_enabled and endpoint.next_poll_at is None
    ) or endpoint.admission_promotions.filter(
        assessment__admission_profile=SourceAdmissionAssessment.Profile.STATIC_LISTING
    ).exists()
    connector_evidence = bool(configuration_record) and enough_runs and all(
        run.connector_configuration_version == endpoint.connector_configuration_version
        for run in runs
    )
    endpoint_evidence = enough_runs and all(
        run.id in observations
        and (
            200 <= observations[run.id].response_status < 300
            or observations[run.id].response_status == 304
        )
        and urlsplit(observations[run.id].final_url).hostname
        and hostname_is_allowed(
            urlsplit(observations[run.id].final_url).hostname,
            endpoint.allowed_domains,
        )
        for run in runs
    )
    max_candidates = int(configuration.get("max_candidates", 20))
    bounded_candidates = enough_runs and all(
        0 < len(urls) <= max_candidates for urls in candidate_sets
    )
    candidate_precision = enough_runs and all(
        urls and all(_url_qualifies(url, endpoint, configuration) for url in urls)
        for urls in candidate_sets
    )
    repeatable = enough_runs and len(set(candidate_sets)) == 1
    disappeared = (
        sum(
            len(set(previous) - set(current))
            for previous, current in zip(candidate_sets, candidate_sets[1:], strict=False)
        )
        if enough_runs
        else 0
    )
    no_disappearance = enough_runs and disappeared == 0

    latest_run = runs[-1] if runs else None
    latest_urls = candidate_sets[-1] if candidate_sets else ()
    reliability = collect_source_reliability(endpoint)
    content_types = _document_content_types(configuration)
    latest_candidate_ids = (
        list(latest_run.candidate_observations.values_list("candidate_id", flat=True))
        if latest_run
        else []
    )
    content_ready_count = (
        ArtifactObservation.objects.filter(
            source_run=latest_run,
            candidate_id__in=latest_candidate_ids,
            raw_artifact__detected_content_type__in=content_types,
        )
        .values("candidate_id")
        .distinct()
        .count()
        if latest_run and content_types
        else 0
    )
    candidate_count = len(latest_urls)
    artifact_ready = bool(candidate_count) and (
        reliability["artifact_ready_count"] == candidate_count
    )
    content_ready = bool(candidate_count) and content_ready_count == candidate_count
    extraction_ready = (
        bool(candidate_count) and reliability["extraction_ready_count"] == candidate_count
    )
    graph_ready = bool(candidate_count) and reliability["graph_ready_count"] == candidate_count
    normalized_ready = bool(candidate_count) and (
        reliability["document_version_count"] >= candidate_count
        and reliability["section_count"] > 0
    )
    local_embeddings_ready = bool(reliability["section_count"]) and (
        reliability["local_embedding_count"] == reliability["section_count"]
    )
    configured_embeddings_ready = bool(reliability["section_count"]) and (
        reliability["configured_embedding_count"] == reliability["section_count"]
    )

    gates = (
        StaticAdmissionGate(
            "controlled_activation",
            controlled_activation,
            "source is disabled or has a recorded static gate-backed promotion",
        ),
        StaticAdmissionGate(
            "source_pack_snapshot",
            snapshot is not None,
            "an immutable repository source-pack snapshot is installed",
        ),
        StaticAdmissionGate(
            "completed_run_count",
            enough_runs,
            f"{len(runs)}/{required_runs} completed controlled source runs",
        ),
        StaticAdmissionGate(
            "connector_evidence",
            connector_evidence,
            "every run used the current immutable connector configuration",
        ),
        StaticAdmissionGate(
            "immutable_endpoint_evidence",
            endpoint_evidence,
            f"{len(observations)}/{required_runs} official endpoint observations",
        ),
        StaticAdmissionGate(
            "bounded_candidates",
            bounded_candidates,
            f"candidate counts={[len(urls) for urls in candidate_sets]}; maximum={max_candidates}",
        ),
        StaticAdmissionGate(
            "candidate_precision",
            candidate_precision,
            "all candidates are HTTPS official-domain documents in configured paths",
        ),
        StaticAdmissionGate(
            "repeatability",
            repeatable,
            "candidate URL sets match across the controlled runs",
        ),
        StaticAdmissionGate(
            "no_unexplained_disappearance",
            no_disappearance,
            f"{disappeared} previously observed candidate URLs disappeared",
        ),
        StaticAdmissionGate(
            "artifact_preservation",
            artifact_ready,
            f"{reliability['artifact_ready_count']}/{candidate_count} latest candidates archived",
        ),
        StaticAdmissionGate(
            "document_content_types",
            content_ready,
            f"{content_ready_count}/{candidate_count} latest artifacts have approved types",
        ),
        StaticAdmissionGate(
            "extraction_coverage",
            extraction_ready,
            f"{reliability['extraction_ready_count']}/{candidate_count} candidates extracted",
        ),
        StaticAdmissionGate(
            "knowledge_graph_coverage",
            graph_ready and normalized_ready,
            f"{reliability['graph_ready_count']}/{candidate_count} graphed; "
            f"{reliability['document_version_count']} versions and "
            f"{reliability['section_count']} sections",
        ),
        StaticAdmissionGate(
            "local_embedding_coverage",
            local_embeddings_ready,
            f"{reliability['local_embedding_count']}/{reliability['section_count']} sections",
        ),
        StaticAdmissionGate(
            "configured_embedding_coverage",
            configured_embeddings_ready,
            f"{reliability['configured_embedding_count']}/{reliability['section_count']} "
            f"{reliability['configured_embedding_provider']} sections",
        ),
    )
    return StaticAdmissionReport(
        endpoint_id=str(endpoint.id),
        endpoint_name=endpoint.name,
        required_runs=required_runs,
        evaluated_source_run_ids=tuple(str(run.id) for run in runs),
        candidate_set_sha256=_candidate_set_hash(latest_urls),
        candidate_count=candidate_count,
        gates=gates,
    )


def _report_signature(report: StaticAdmissionReport, pack_checksum: str) -> str:
    payload = {**report.as_dict(), "source_pack_checksum": pack_checksum}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


@transaction.atomic
def assess_static_admission(
    endpoint: SourceEndpoint,
    *,
    required_runs: int = 2,
) -> tuple[SourceAdmissionAssessment, bool]:
    report = evaluate_static_admission(endpoint, required_runs=required_runs)
    snapshot = endpoint.source_pack_snapshots.order_by("-applied_at", "-id").first()
    signature = _report_signature(report, snapshot.checksum if snapshot else "")
    existing = SourceAdmissionAssessment.objects.filter(report_signature=signature).first()
    if existing:
        return existing, False
    assessment = SourceAdmissionAssessment(
        endpoint=endpoint,
        source_pack_snapshot=snapshot,
        admission_profile=SourceAdmissionAssessment.Profile.STATIC_LISTING,
        status=(
            SourceAdmissionAssessment.Status.READY
            if report.ready_for_promotion
            else SourceAdmissionAssessment.Status.INCOMPLETE
        ),
        report_signature=signature,
        required_evidence_count=required_runs,
        evaluated_source_run_ids=list(report.evaluated_source_run_ids),
        candidate_set_sha256=report.candidate_set_sha256,
        candidate_count=report.candidate_count,
        gates=[asdict(gate) for gate in report.gates],
    )
    assessment.full_clean()
    try:
        with transaction.atomic():
            assessment.save()
    except IntegrityError:
        return SourceAdmissionAssessment.objects.get(report_signature=signature), False
    record_pipeline_event(
        event_type="source.admission.evaluated",
        aggregate_type="source_endpoint",
        aggregate_id=endpoint.id,
        payload={
            "assessment_id": str(assessment.id),
            "source_pack_checksum": snapshot.checksum if snapshot else "",
            **report.as_dict(),
        },
    )
    return assessment, True


@transaction.atomic
def promote_static_source(
    endpoint: SourceEndpoint,
    assessment: SourceAdmissionAssessment,
    *,
    actor_type: str = "system",
    actor_identifier: str = "management_command",
) -> SourceAdmissionPromotion:
    endpoint = SourceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    assessment = SourceAdmissionAssessment.objects.select_related("source_pack_snapshot").get(
        pk=assessment.pk
    )
    if assessment.endpoint_id != endpoint.id:
        raise ValueError("Admission assessment does not belong to this endpoint.")
    if assessment.admission_profile != SourceAdmissionAssessment.Profile.STATIC_LISTING:
        raise ValueError("Static promotion requires a static admission assessment.")
    if endpoint.is_enabled:
        raise ValueError("Source endpoint is already enabled.")
    current, _ = assess_static_admission(
        endpoint,
        required_runs=assessment.required_evidence_count,
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
        "admission_profile": current.admission_profile,
        "report_signature": current.report_signature,
        "source_pack_checksum": (
            current.source_pack_snapshot.checksum if current.source_pack_snapshot else ""
        ),
        "next_poll_at": endpoint.next_poll_at.isoformat(),
    }
    record_audit_event(
        action="source.admission.promoted",
        target_type="source_endpoint",
        target_id=endpoint.id,
        actor_type=actor_type,
        actor_identifier=actor_identifier,
        details=payload,
    )
    record_pipeline_event(
        event_type="source.admission.promoted",
        aggregate_type="source_endpoint",
        aggregate_id=endpoint.id,
        payload=payload,
    )
    return promotion
