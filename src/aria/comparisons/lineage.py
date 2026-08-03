import hashlib
import json
from dataclasses import dataclass

from django.db import transaction

from aria.artifacts.models import RawArtifact
from aria.comparisons.audit import audit_document_version
from aria.comparisons.contracts import LINEAGE_RULESET
from aria.comparisons.models import VersionLineageAssessment
from aria.documents.models import DocumentVersion
from aria.extraction.models import ExtractionRun

LINEAGE_CONFIGURATION = {
    "ruleset": LINEAGE_RULESET,
    "representation_strategy": "extractor_and_artifact_lineage_v1",
    "provenance_strategy": "direct_or_unique_section_lineage_v1",
}


@dataclass(frozen=True)
class LineageProjectionSummary:
    candidate_count: int
    created_count: int
    skipped_count: int
    verified_count: int
    reconstructable_count: int
    quarantined_count: int
    configuration_hash: str


def lineage_configuration_hash() -> str:
    serialized = json.dumps(LINEAGE_CONFIGURATION, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


@transaction.atomic
def project_version_lineage(version: DocumentVersion) -> tuple[VersionLineageAssessment, bool]:
    audit = audit_document_version(version)
    configuration_hash = lineage_configuration_hash()
    existing = VersionLineageAssessment.objects.filter(
        document_version=version,
        configuration_hash=configuration_hash,
    ).first()
    if existing is not None:
        return existing, False

    source_artifact = None
    extraction_run = None
    if audit.provenance_status != VersionLineageAssessment.ProvenanceStatus.QUARANTINED:
        source_artifact = RawArtifact.objects.get(pk=audit.source_artifact_ids[0])
        extraction_run = ExtractionRun.objects.get(pk=audit.extraction_run_ids[0])

    assessment = VersionLineageAssessment.objects.create(
        document_version=version,
        representation_kind=audit.representation_kind,
        comparison_track_key=audit.comparison_track_key,
        provenance_status=audit.provenance_status,
        source_artifact=source_artifact,
        extraction_run=extraction_run,
        ruleset=LINEAGE_RULESET,
        configuration=LINEAGE_CONFIGURATION,
        configuration_hash=configuration_hash,
        basis=audit.as_dict(),
    )
    return assessment, True


def project_active_version_lineage() -> LineageProjectionSummary:
    versions = list(
        DocumentVersion.objects.filter(identity__superseded_by__isnull=True)
        .select_related("identity")
        .order_by("identity_id", "created_at", "id")
    )
    assessments = []
    created_count = 0
    for version in versions:
        assessment, created = project_version_lineage(version)
        assessments.append(assessment)
        created_count += created
    return LineageProjectionSummary(
        candidate_count=len(versions),
        created_count=created_count,
        skipped_count=len(versions) - created_count,
        verified_count=sum(
            item.provenance_status == VersionLineageAssessment.ProvenanceStatus.VERIFIED
            for item in assessments
        ),
        reconstructable_count=sum(
            item.provenance_status == VersionLineageAssessment.ProvenanceStatus.RECONSTRUCTABLE
            for item in assessments
        ),
        quarantined_count=sum(
            item.provenance_status == VersionLineageAssessment.ProvenanceStatus.QUARANTINED
            for item in assessments
        ),
        configuration_hash=lineage_configuration_hash(),
    )
