from aria.browser.models import SourceAdmissionAssessment
from aria.discovery.models import SourceRun
from aria.reliability.models import SourceReliabilityAssessment
from aria.reliability.services import collect_source_reliability
from aria.sources.models import SourceEndpoint


def _coverage_ratio(ready: int, total: int) -> float:
    return ready / total if total else 0.0


def collect_source_confidence(endpoint: SourceEndpoint) -> dict:
    reliability = collect_source_reliability(endpoint)
    assessment = endpoint.admission_assessments.select_related("source_pack_snapshot").first()
    promotion = endpoint.admission_promotions.select_related("assessment").first()
    snapshot = endpoint.source_pack_snapshots.first()
    candidate_count = reliability["candidate_count"]
    section_count = reliability["section_count"]
    coverage_ratios = (
        _coverage_ratio(reliability["artifact_ready_count"], candidate_count),
        _coverage_ratio(reliability["extraction_ready_count"], candidate_count),
        _coverage_ratio(reliability["graph_ready_count"], candidate_count),
        _coverage_ratio(reliability["local_embedding_count"], section_count),
        _coverage_ratio(reliability["configured_embedding_count"], section_count),
    )
    evidence_coverage_percent = round(sum(coverage_ratios) / len(coverage_ratios) * 100)
    completed_run_count = endpoint.source_runs.filter(
        status=SourceRun.Status.COMPLETED,
        resource_run__isnull=True,
    ).count()
    if endpoint.is_enabled:
        if reliability["status"] == SourceReliabilityAssessment.Status.HEALTHY:
            state = "operational"
        elif reliability["status"] == SourceReliabilityAssessment.Status.PROCESSING:
            state = "processing"
        else:
            state = "attention"
    elif assessment and assessment.status == SourceAdmissionAssessment.Status.READY:
        state = "admission_ready"
    elif completed_run_count:
        state = "pilot_evidence"
    else:
        state = "registered"
    return {
        "endpoint": endpoint,
        "state": state,
        "is_enabled": endpoint.is_enabled,
        "next_poll_at": endpoint.next_poll_at,
        "completed_run_count": completed_run_count,
        "evidence_coverage_percent": evidence_coverage_percent,
        "reliability": reliability,
        "source_pack_snapshot": snapshot,
        "admission_assessment": assessment,
        "admission_promotion": promotion,
    }


def collect_source_confidence_report() -> list[dict]:
    endpoints = SourceEndpoint.objects.select_related(
        "collection",
        "collection__authority",
    ).all()
    return [collect_source_confidence(endpoint) for endpoint in endpoints]


def serialize_source_confidence(row: dict) -> dict:
    endpoint = row["endpoint"]
    reliability = row["reliability"]
    snapshot = row["source_pack_snapshot"]
    assessment = row["admission_assessment"]
    promotion = row["admission_promotion"]
    return {
        "endpoint_id": str(endpoint.id),
        "endpoint_name": endpoint.name,
        "authority": endpoint.collection.authority.name,
        "state": row["state"],
        "is_enabled": row["is_enabled"],
        "next_poll_at": row["next_poll_at"].isoformat() if row["next_poll_at"] else "",
        "completed_run_count": row["completed_run_count"],
        "evidence_coverage_percent": row["evidence_coverage_percent"],
        "reliability_status": reliability["status"],
        "candidate_count": reliability["candidate_count"],
        "artifact_ready_count": reliability["artifact_ready_count"],
        "extraction_ready_count": reliability["extraction_ready_count"],
        "graph_ready_count": reliability["graph_ready_count"],
        "section_count": reliability["section_count"],
        "local_embedding_count": reliability["local_embedding_count"],
        "configured_embedding_count": reliability["configured_embedding_count"],
        "configured_embedding_provider": reliability["configured_embedding_provider"],
        "source_pack": (
            {
                "slug": snapshot.pack_slug,
                "version": snapshot.pack_version,
                "checksum": snapshot.checksum,
            }
            if snapshot
            else None
        ),
        "admission": (
            {
                "assessment_id": str(assessment.id),
                "profile": assessment.admission_profile,
                "status": assessment.status,
                "report_signature": assessment.report_signature,
            }
            if assessment
            else None
        ),
        "promotion": (
            {
                "promotion_id": str(promotion.id),
                "assessment_id": str(promotion.assessment_id),
                "next_poll_at": promotion.next_poll_at.isoformat(),
            }
            if promotion
            else None
        ),
        "findings": reliability["findings"],
    }
