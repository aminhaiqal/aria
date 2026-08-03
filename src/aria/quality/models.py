from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from aria.artifacts.models import RawArtifact
from aria.collections.models import PublicationCollection
from aria.common.models import AppendOnlyModel, TimeStampedModel
from aria.documents.models import DocumentVersion


class QualityAssessmentRun(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    collection = models.ForeignKey(
        PublicationCollection,
        on_delete=models.PROTECT,
        related_name="quality_assessment_runs",
    )
    ruleset = models.CharField(max_length=128)
    configuration = models.JSONField(default=dict)
    configuration_hash = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    corpus_fingerprint = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    document_count = models.PositiveIntegerField(default=0)
    passed_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)
    review_required_count = models.PositiveIntegerField(default=0)
    finding_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("collection", "configuration_hash", "corpus_fingerprint"),
                name="unique_quality_run_for_collection_corpus_config",
            )
        ]
        indexes = [models.Index(fields=("collection", "status", "created_at"))]

    def __str__(self) -> str:
        return f"{self.collection} {self.ruleset} [{self.status}]"


class DocumentQualityAssessment(AppendOnlyModel):
    class Outcome(models.TextChoices):
        PASSED = "passed", "Passed"
        WARNING = "warning", "Warning"
        REVIEW_REQUIRED = "review_required", "Review required"

    quality_run = models.ForeignKey(
        QualityAssessmentRun,
        on_delete=models.PROTECT,
        related_name="document_assessments",
    )
    document_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="quality_assessments",
    )
    source_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="document_quality_assessments",
    )
    outcome = models.CharField(max_length=32, choices=Outcome.choices, db_index=True)
    score = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    metrics = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("quality_run", "outcome", "document_version")
        constraints = [
            models.UniqueConstraint(
                fields=("quality_run", "document_version"),
                name="unique_document_assessment_per_quality_run",
            )
        ]
        indexes = [models.Index(fields=("quality_run", "outcome"))]

    def __str__(self) -> str:
        return f"{self.document_version_id} [{self.outcome}]"


class QualityFinding(AppendOnlyModel):
    class Severity(models.TextChoices):
        WARNING = "warning", "Warning"
        REVIEW_REQUIRED = "review_required", "Review required"

    assessment = models.ForeignKey(
        DocumentQualityAssessment,
        on_delete=models.PROTECT,
        related_name="findings",
    )
    document_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="quality_findings",
    )
    source_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="quality_findings",
    )
    code = models.CharField(max_length=128, db_index=True)
    severity = models.CharField(max_length=32, choices=Severity.choices, db_index=True)
    message = models.TextField()
    evidence = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("assessment", "severity", "code")
        constraints = [
            models.UniqueConstraint(
                fields=("assessment", "code"),
                name="unique_quality_finding_code_per_assessment",
            )
        ]
        indexes = [
            models.Index(fields=("assessment", "severity")),
            models.Index(fields=("document_version", "code")),
        ]

    def __str__(self) -> str:
        return f"{self.code} [{self.severity}]"
