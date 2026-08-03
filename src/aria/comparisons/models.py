from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.artifacts.models import RawArtifact
from aria.common.models import AppendOnlyModel
from aria.documents.models import DocumentVersion
from aria.extraction.models import ExtractionRun


class VersionLineageAssessment(AppendOnlyModel):
    class RepresentationKind(models.TextChoices):
        LANDING_HTML = "landing_html", "Landing HTML"
        OFFICIAL_PDF = "official_pdf", "Official PDF"
        OCR_DERIVED = "ocr_derived", "OCR-derived official file"
        UNKNOWN = "unknown", "Unknown"

    class ProvenanceStatus(models.TextChoices):
        VERIFIED = "verified", "Verified"
        RECONSTRUCTABLE = "reconstructable", "Reconstructable"
        QUARANTINED = "quarantined", "Quarantined"

    document_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="lineage_assessments",
    )
    representation_kind = models.CharField(
        max_length=32,
        choices=RepresentationKind.choices,
        db_index=True,
    )
    comparison_track_key = models.CharField(max_length=160, db_index=True)
    provenance_status = models.CharField(
        max_length=32,
        choices=ProvenanceStatus.choices,
        db_index=True,
    )
    source_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="version_lineage_assessments",
        null=True,
        blank=True,
    )
    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.PROTECT,
        related_name="version_lineage_assessments",
        null=True,
        blank=True,
    )
    ruleset = models.CharField(max_length=128)
    configuration = models.JSONField(default=dict)
    configuration_hash = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    basis = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("document_version", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("document_version", "configuration_hash"),
                name="unique_version_lineage_assessment_config",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(provenance_status="quarantined")
                    | (
                        models.Q(source_artifact__isnull=False)
                        & models.Q(extraction_run__isnull=False)
                    )
                ),
                name="lineage_evidence_required_unless_quarantined",
            ),
        ]
        indexes = [
            models.Index(fields=("comparison_track_key", "provenance_status")),
            models.Index(fields=("document_version", "ruleset")),
        ]

    def __str__(self) -> str:
        return f"{self.document_version_id} {self.representation_kind} [{self.provenance_status}]"
