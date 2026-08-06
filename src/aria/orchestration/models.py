from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation, RawArtifact
from aria.common.models import AppendOnlyModel, TimeStampedModel
from aria.comparisons.models import (
    ComparisonSummary,
    DocumentComparison,
    VersionLineageAssessment,
)
from aria.documents.models import DocumentVersion
from aria.extraction.models import ExtractionRun
from aria.quality.models import QualityAssessmentRun


class ChangeOrchestration(TimeStampedModel):
    class Stage(models.TextChoices):
        ARTIFACT = "artifact", "Artifact verification"
        EXTRACTION = "extraction", "Extraction"
        VERSION = "version", "Version resolution"
        QUALITY = "quality", "Quality assessment"
        KNOWLEDGE = "knowledge", "Knowledge projection"
        LINEAGE = "lineage", "Lineage and anchors"
        COMPARISON = "comparison", "Deterministic comparison"
        REVIEW = "review", "Human review"
        SUMMARY = "summary", "GPT summary"
        COMPLETED = "completed", "Completed"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        WAITING_OCR = "waiting_ocr", "Waiting for OCR"
        NO_CONTENT_CHANGE = "no_content_change", "No normalized content change"
        QUALITY_REVIEW_REQUIRED = "quality_review_required", "Quality review required"
        LINEAGE_REJECTED = "lineage_rejected", "Lineage rejected"
        REVIEW_REQUIRED = "review_required", "Comparison review required"
        SUMMARY_PENDING = "summary_pending", "GPT summary pending"
        SUMMARY_FAILED = "summary_failed", "GPT summary failed"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    artifact_observation = models.OneToOneField(
        ArtifactObservation,
        on_delete=models.PROTECT,
        related_name="change_orchestration",
    )
    source_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="change_orchestrations",
    )
    idempotency_key = models.CharField(
        max_length=64,
        unique=True,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    current_stage = models.CharField(
        max_length=16,
        choices=Stage.choices,
        default=Stage.ARTIFACT,
        db_index=True,
    )
    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.PROTECT,
        related_name="change_orchestrations",
        null=True,
        blank=True,
    )
    document_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="change_orchestrations",
        null=True,
        blank=True,
    )
    quality_run = models.ForeignKey(
        QualityAssessmentRun,
        on_delete=models.PROTECT,
        related_name="change_orchestrations",
        null=True,
        blank=True,
    )
    lineage_assessment = models.ForeignKey(
        VersionLineageAssessment,
        on_delete=models.PROTECT,
        related_name="change_orchestrations",
        null=True,
        blank=True,
    )
    comparison = models.ForeignKey(
        DocumentComparison,
        on_delete=models.PROTECT,
        related_name="change_orchestrations",
        null=True,
        blank=True,
    )
    summary = models.ForeignKey(
        ComparisonSummary,
        on_delete=models.PROTECT,
        related_name="change_orchestrations",
        null=True,
        blank=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status", "heartbeat_at")),
            models.Index(fields=("source_artifact", "status")),
        ]

    def clean(self) -> None:
        if self.artifact_observation_id:
            if self.artifact_observation.raw_artifact_id != self.source_artifact_id:
                raise ValidationError("Orchestration artifact must match its observation.")
            if not self.artifact_observation.content_changed:
                raise ValidationError("Only changed artifact observations can be orchestrated.")

    def __str__(self) -> str:
        return f"{self.artifact_observation_id} [{self.status}:{self.current_stage}]"


class OrchestrationStepAttempt(AppendOnlyModel):
    class Outcome(models.TextChoices):
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped"
        WAITING = "waiting", "Waiting"
        FAILED = "failed", "Failed"

    orchestration = models.ForeignKey(
        ChangeOrchestration,
        on_delete=models.PROTECT,
        related_name="step_attempts",
    )
    stage = models.CharField(
        max_length=16,
        choices=ChangeOrchestration.Stage.choices,
        db_index=True,
    )
    attempt_number = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=16, choices=Outcome.choices, db_index=True)
    input_hash = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    output = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("orchestration", "started_at", "attempt_number")
        constraints = [
            models.UniqueConstraint(
                fields=("orchestration", "stage", "attempt_number"),
                name="unique_orchestration_stage_attempt",
            )
        ]
        indexes = [models.Index(fields=("orchestration", "stage", "outcome"))]

    def __str__(self) -> str:
        return f"{self.orchestration_id}:{self.stage}:{self.attempt_number} [{self.outcome}]"
