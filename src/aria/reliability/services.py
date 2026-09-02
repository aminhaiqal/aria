import hashlib
import json
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from aria.artifacts.models import ArtifactDerivative, ArtifactObservation
from aria.browser.admission import capture_has_bounded_network
from aria.browser.models import BrowserCapture
from aria.discovery.models import SourceRun
from aria.documents.models import NormalizedSection, VersionEvidence
from aria.events.services import record_pipeline_event
from aria.extraction.models import ExtractionRun
from aria.knowledge.embeddings import embedding_configuration
from aria.knowledge.models import GraphNode, SectionEmbedding
from aria.reliability.models import SourceReliabilityAssessment
from aria.sources.models import SourceEndpoint


def _hash_values(values) -> str:
    serialized = json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _latest_endpoint_runs(endpoint: SourceEndpoint, limit: int = 2) -> list[SourceRun]:
    return list(
        SourceRun.objects.filter(
            endpoint=endpoint,
            status=SourceRun.Status.COMPLETED,
            resource_run__isnull=True,
        ).order_by("-finished_at", "-created_at")[:limit]
    )


def _run_candidate_urls(source_run: SourceRun) -> tuple[str, ...]:
    return tuple(
        sorted(source_run.candidate_observations.values_list("candidate__canonical_url", flat=True))
    )


def _coverage(candidate_ids: list, source_run_id=None) -> dict:
    artifacts_by_candidate: dict = defaultdict(set)
    observations = ArtifactObservation.objects.filter(candidate_id__in=candidate_ids)
    if source_run_id:
        observations = observations.filter(source_run_id=source_run_id)
    for candidate_id, artifact_id in observations.values_list("candidate_id", "raw_artifact_id"):
        artifacts_by_candidate[candidate_id].add(artifact_id)
    source_artifact_ids = {item for values in artifacts_by_candidate.values() for item in values}
    ocr_derivatives: dict = defaultdict(set)
    for source_id, derived_id in ArtifactDerivative.objects.filter(
        source_artifact_id__in=source_artifact_ids,
        transformation_type=ArtifactDerivative.TransformationType.OCR_SEARCHABLE_PDF,
    ).values_list("source_artifact_id", "derived_artifact_id"):
        ocr_derivatives[source_id].add(derived_id)
    expanded_artifacts_by_candidate = {
        candidate_id: artifacts
        | {
            derived_id
            for artifact_id in artifacts
            for derived_id in ocr_derivatives.get(artifact_id, ())
        }
        for candidate_id, artifacts in artifacts_by_candidate.items()
    }
    artifact_ids = {item for values in expanded_artifacts_by_candidate.values() for item in values}
    extracted_artifacts = set(
        ExtractionRun.objects.filter(
            raw_artifact_id__in=artifact_ids,
            status=ExtractionRun.Status.SUCCEEDED,
        ).values_list("raw_artifact_id", flat=True)
    )
    graph_artifacts = set(
        GraphNode.objects.filter(
            node_type=GraphNode.NodeType.ARTIFACT,
            source_type="raw_artifact",
            source_id__in=artifact_ids,
        ).values_list("source_id", flat=True)
    )
    version_ids = set(
        VersionEvidence.objects.filter(raw_artifact_id__in=artifact_ids).values_list(
            "document_version_id", flat=True
        )
    )
    section_ids = list(
        NormalizedSection.objects.filter(document_version_id__in=version_ids).values_list(
            "id", flat=True
        )
    )
    provider, model, _ = embedding_configuration()
    local_model = embedding_configuration("local_hash")[1]
    local_embedding_count = (
        SectionEmbedding.objects.filter(
            normalized_section_id__in=section_ids,
            provider="local_hash",
            model=local_model,
            source_text_sha256=F("normalized_section__text_sha256"),
        )
        .values("normalized_section_id")
        .distinct()
        .count()
    )
    configured_embedding_count = (
        SectionEmbedding.objects.filter(
            normalized_section_id__in=section_ids,
            provider=provider,
            model=model,
            source_text_sha256=F("normalized_section__text_sha256"),
        )
        .values("normalized_section_id")
        .distinct()
        .count()
    )
    return {
        "artifact_ready_count": len(artifacts_by_candidate),
        "extraction_ready_count": sum(
            bool(artifacts & extracted_artifacts)
            for artifacts in expanded_artifacts_by_candidate.values()
        ),
        "graph_ready_count": sum(
            bool(artifacts & graph_artifacts)
            for artifacts in expanded_artifacts_by_candidate.values()
        ),
        "document_version_count": len(version_ids),
        "section_count": len(section_ids),
        "local_embedding_count": local_embedding_count,
        "configured_embedding_count": configured_embedding_count,
        "configured_embedding_provider": provider,
        "configured_embedding_model": model,
    }


def _finding(code: str, severity: str, detail: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "detail": detail}


def collect_source_reliability(
    endpoint: SourceEndpoint,
    *,
    assessed_at=None,
) -> dict:
    now = assessed_at or timezone.now()
    runs = _latest_endpoint_runs(endpoint)
    latest_run = runs[0] if runs else None
    previous_run = runs[1] if len(runs) > 1 else None
    urls = _run_candidate_urls(latest_run) if latest_run else ()
    previous_urls = _run_candidate_urls(previous_run) if previous_run else ()
    candidate_hash = _hash_values(urls) if urls else ""
    previous_candidate_hash = _hash_values(previous_urls) if previous_urls else ""
    candidate_set_changed = bool(
        candidate_hash and previous_candidate_hash and candidate_hash != previous_candidate_hash
    )
    candidate_ids = (
        list(latest_run.candidate_observations.values_list("candidate_id", flat=True))
        if latest_run
        else []
    )
    coverage = _coverage(candidate_ids, latest_run.id if latest_run else None)
    freshness_deadline = None
    if latest_run:
        expected_next = endpoint.next_poll_at or (
            (latest_run.finished_at or latest_run.created_at)
            + timedelta(minutes=endpoint.polling_interval_minutes)
        )
        freshness_deadline = expected_next + timedelta(
            minutes=settings.SOURCE_FRESHNESS_GRACE_MINUTES
        )

    browser_capture_ready = not endpoint.requires_javascript
    network_policy_ready = not endpoint.requires_javascript
    if endpoint.requires_javascript and latest_run:
        capture = (
            BrowserCapture.objects.filter(
                source_run=latest_run,
                status=BrowserCapture.Status.COMPLETED,
            )
            .prefetch_related("network_exchanges")
            .first()
        )
        browser_capture_ready = capture is not None
        network_policy_ready = bool(capture and capture_has_bounded_network(capture))

    findings = []
    if not endpoint.is_enabled:
        status = SourceReliabilityAssessment.Status.DISABLED
    elif latest_run is None:
        findings.append(
            _finding("no_completed_run", "warning", "No completed endpoint run exists.")
        )
        status = SourceReliabilityAssessment.Status.WARNING
    else:
        if endpoint.health_state == SourceEndpoint.HealthState.UNHEALTHY:
            findings.append(
                _finding(
                    "endpoint_unhealthy",
                    "critical",
                    f"Endpoint has {endpoint.consecutive_failures} consecutive failed runs.",
                )
            )
        elif endpoint.health_state == SourceEndpoint.HealthState.DEGRADED:
            findings.append(
                _finding(
                    "endpoint_degraded",
                    "warning",
                    f"Endpoint has {endpoint.consecutive_failures} consecutive failed runs.",
                )
            )
        run_finished = latest_run.finished_at or latest_run.created_at
        within_pipeline_grace = now <= run_finished + timedelta(
            minutes=settings.SOURCE_PIPELINE_GRACE_MINUTES
        )
        if freshness_deadline and now > freshness_deadline:
            findings.append(
                _finding(
                    "source_stale",
                    "critical",
                    f"Latest successful run exceeded {freshness_deadline.isoformat()}.",
                )
            )
        if not urls:
            findings.append(
                _finding("no_candidates", "critical", "Latest run has no candidate evidence.")
            )
        if endpoint.requires_javascript and not browser_capture_ready:
            findings.append(
                _finding(
                    "browser_capture_missing",
                    "critical",
                    "Completed JavaScript run has no completed browser capture.",
                )
            )
        if endpoint.requires_javascript and not network_policy_ready:
            findings.append(
                _finding(
                    "browser_network_unverified",
                    "critical",
                    "Latest browser capture does not satisfy its bounded network policy.",
                )
            )
        coverage_checks = (
            ("artifact_coverage", coverage["artifact_ready_count"], len(urls)),
            ("extraction_coverage", coverage["extraction_ready_count"], len(urls)),
            ("graph_coverage", coverage["graph_ready_count"], len(urls)),
            (
                "local_embedding_coverage",
                coverage["local_embedding_count"],
                coverage["section_count"],
            ),
            (
                "configured_embedding_coverage",
                coverage["configured_embedding_count"],
                coverage["section_count"],
            ),
        )
        for code, ready, total in coverage_checks:
            if ready == total and (total > 0 or not urls):
                continue
            severity = "info" if within_pipeline_grace else "warning"
            findings.append(
                _finding(
                    code,
                    severity,
                    f"{ready}/{total} latest-source records are ready.",
                )
            )
        if candidate_set_changed:
            findings.append(
                _finding(
                    "candidate_set_changed",
                    "info",
                    "Latest candidate URL set differs from the previous completed run.",
                )
            )
        severities = {finding["severity"] for finding in findings}
        if "critical" in severities:
            status = SourceReliabilityAssessment.Status.CRITICAL
        elif "warning" in severities:
            status = SourceReliabilityAssessment.Status.WARNING
        elif any(
            finding["severity"] == "info" and finding["code"].endswith("_coverage")
            for finding in findings
        ):
            status = SourceReliabilityAssessment.Status.PROCESSING
        else:
            status = SourceReliabilityAssessment.Status.HEALTHY

    return {
        "endpoint": endpoint,
        "source_run": latest_run,
        "status": status,
        "freshness_deadline": freshness_deadline,
        "candidate_set_sha256": candidate_hash,
        "previous_candidate_set_sha256": previous_candidate_hash,
        "candidate_set_changed": candidate_set_changed,
        "candidate_count": len(urls),
        **coverage,
        "browser_capture_ready": browser_capture_ready,
        "network_policy_ready": network_policy_ready,
        "findings": findings,
        "assessed_at": now,
    }


def _assessment_signature(values: dict) -> str:
    signature_values = {
        key: value for key, value in values.items() if key not in {"endpoint", "assessed_at"}
    }
    signature_values["endpoint_id"] = str(values["endpoint"].id)
    signature_values["source_run"] = str(values["source_run"].id) if values["source_run"] else ""
    signature_values["freshness_deadline"] = (
        values["freshness_deadline"].isoformat() if values["freshness_deadline"] else ""
    )
    serialized = json.dumps(signature_values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


@transaction.atomic
def assess_source_reliability(
    endpoint: SourceEndpoint,
    *,
    assessed_at=None,
) -> tuple[SourceReliabilityAssessment, bool]:
    values = collect_source_reliability(endpoint, assessed_at=assessed_at)
    signature = _assessment_signature(values)
    existing = SourceReliabilityAssessment.objects.filter(assessment_signature=signature).first()
    if existing:
        return existing, False
    previous = endpoint.reliability_assessments.order_by("-assessed_at", "-id").first()
    previous_signal = (
        endpoint.reliability_assessments.exclude(
            status=SourceReliabilityAssessment.Status.PROCESSING
        )
        .order_by("-assessed_at", "-id")
        .first()
    )
    assessment = SourceReliabilityAssessment(
        **values,
        previous_assessment=previous,
        assessment_signature=signature,
    )
    assessment.full_clean()
    try:
        assessment.save()
    except IntegrityError:
        return SourceReliabilityAssessment.objects.get(assessment_signature=signature), False

    payload = {
        "assessment_id": str(assessment.id),
        "endpoint_id": str(endpoint.id),
        "source_run_id": str(assessment.source_run_id or ""),
        "status": assessment.status,
        "previous_status": previous.status if previous else "",
        "candidate_count": assessment.candidate_count,
        "artifact_ready_count": assessment.artifact_ready_count,
        "extraction_ready_count": assessment.extraction_ready_count,
        "graph_ready_count": assessment.graph_ready_count,
        "section_count": assessment.section_count,
        "local_embedding_count": assessment.local_embedding_count,
        "configured_embedding_count": assessment.configured_embedding_count,
        "findings": assessment.findings,
    }
    record_pipeline_event(
        event_type="source.reliability.assessed",
        aggregate_type="source_reliability_assessment",
        aggregate_id=assessment.id,
        payload=payload,
    )
    if assessment.status in {
        SourceReliabilityAssessment.Status.WARNING,
        SourceReliabilityAssessment.Status.CRITICAL,
    } and (previous is None or previous.status != assessment.status):
        record_pipeline_event(
            event_type="source.reliability.alert",
            aggregate_type="source_endpoint",
            aggregate_id=endpoint.id,
            payload=payload,
        )
    elif (
        assessment.status == SourceReliabilityAssessment.Status.HEALTHY
        and previous_signal
        and previous_signal.status
        in {
            SourceReliabilityAssessment.Status.WARNING,
            SourceReliabilityAssessment.Status.CRITICAL,
        }
    ):
        record_pipeline_event(
            event_type="source.reliability.recovered",
            aggregate_type="source_endpoint",
            aggregate_id=endpoint.id,
            payload=payload,
        )
    return assessment, True
