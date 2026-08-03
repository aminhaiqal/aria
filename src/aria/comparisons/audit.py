from dataclasses import asdict, dataclass

from aria.comparisons.contracts import (
    LINEAGE_RULESET,
    ProvenanceStatus,
    RepresentationKind,
    representation_track,
)
from aria.documents.models import DocumentVersion


@dataclass(frozen=True)
class VersionLineageAudit:
    version_id: str
    identity_id: str
    representation_kind: str
    comparison_track_key: str
    provenance_status: str
    evidence_count: int
    section_count: int
    source_artifact_ids: tuple[str, ...]
    extraction_run_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    ruleset: str = LINEAGE_RULESET

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["source_artifact_ids"] = list(self.source_artifact_ids)
        payload["extraction_run_ids"] = list(self.extraction_run_ids)
        payload["reasons"] = list(self.reasons)
        return payload


def classify_representation(version: DocumentVersion) -> RepresentationKind:
    lineage = version.normalized_metadata.get("artifact_lineage", {})
    if lineage.get("transformation_type") == "ocr_searchable_pdf":
        return RepresentationKind.OCR_DERIVED
    if version.extractor_name.startswith("html"):
        return RepresentationKind.LANDING_HTML
    if version.extractor_name.startswith("pdf"):
        return RepresentationKind.OFFICIAL_PDF
    return RepresentationKind.UNKNOWN


def audit_document_version(version: DocumentVersion) -> VersionLineageAudit:
    evidence_count = version.evidence_records.count()
    section_sources = list(
        version.sections.order_by()
        .values_list("source_artifact_id", "extraction_run_id")
        .distinct()
    )
    artifact_ids = tuple(sorted({str(artifact_id) for artifact_id, _ in section_sources}))
    extraction_run_ids = tuple(sorted({str(run_id) for _, run_id in section_sources}))
    section_count = version.sections.count()
    reasons: list[str] = []

    if not section_count:
        status = ProvenanceStatus.QUARANTINED
        reasons.append("version_has_no_normalized_sections")
    elif evidence_count:
        status = ProvenanceStatus.VERIFIED
        reasons.append("direct_version_evidence_present")
    elif len(artifact_ids) == 1 and len(extraction_run_ids) == 1:
        status = ProvenanceStatus.RECONSTRUCTABLE
        reasons.append("single_section_artifact_and_extraction_lineage")
    else:
        status = ProvenanceStatus.QUARANTINED
        reasons.append("missing_unambiguous_version_evidence")

    representation = classify_representation(version)
    if representation == RepresentationKind.UNKNOWN:
        reasons.append("unknown_representation_kind")
        status = ProvenanceStatus.QUARANTINED
    track = representation_track(representation)
    return VersionLineageAudit(
        version_id=str(version.id),
        identity_id=str(version.identity_id),
        representation_kind=representation.value,
        comparison_track_key=f"{version.identity.stable_key}:{track}",
        provenance_status=status.value,
        evidence_count=evidence_count,
        section_count=section_count,
        source_artifact_ids=artifact_ids,
        extraction_run_ids=extraction_run_ids,
        reasons=tuple(reasons),
    )


def audit_active_versions() -> dict:
    versions = (
        DocumentVersion.objects.filter(identity__superseded_by__isnull=True)
        .select_related("identity")
        .order_by("identity_id", "created_at", "id")
    )
    items = [audit_document_version(version).as_dict() for version in versions]
    return {
        "ruleset": LINEAGE_RULESET,
        "version_count": len(items),
        "verified_count": sum(
            item["provenance_status"] == ProvenanceStatus.VERIFIED for item in items
        ),
        "reconstructable_count": sum(
            item["provenance_status"] == ProvenanceStatus.RECONSTRUCTABLE for item in items
        ),
        "quarantined_count": sum(
            item["provenance_status"] == ProvenanceStatus.QUARANTINED for item in items
        ),
        "items": items,
    }
