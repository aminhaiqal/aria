from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from aria.artifacts.models import RawArtifact
from aria.common.models import AppendOnlyModel, TimeStampedModel
from aria.documents.models import DocumentIdentity, DocumentVersion, NormalizedSection
from aria.events.models import PipelineEvent
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


class StructuralAnchor(AppendOnlyModel):
    document_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="structural_anchors",
    )
    normalized_section = models.ForeignKey(
        NormalizedSection,
        on_delete=models.PROTECT,
        related_name="structural_anchors",
    )
    source_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="structural_anchors",
    )
    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.PROTECT,
        related_name="structural_anchors",
    )
    ruleset = models.CharField(max_length=128)
    configuration_hash = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    anchor_type = models.CharField(max_length=32, db_index=True)
    canonical_key = models.CharField(max_length=255)
    ordinal = models.PositiveIntegerField()
    label = models.TextField(blank=True)
    text = models.TextField()
    text_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    char_start = models.PositiveBigIntegerField()
    char_end = models.PositiveBigIntegerField()
    source_locator = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("document_version", "ordinal")
        constraints = [
            models.UniqueConstraint(
                fields=("document_version", "configuration_hash", "ordinal"),
                name="unique_structural_anchor_ordinal_config",
            ),
            models.CheckConstraint(
                condition=models.Q(char_end__gte=models.F("char_start")),
                name="structural_anchor_char_range_valid",
            ),
        ]
        indexes = [
            models.Index(fields=("document_version", "canonical_key")),
            models.Index(fields=("anchor_type", "canonical_key")),
        ]

    def __str__(self) -> str:
        return f"{self.document_version_id}:{self.canonical_key}"


class DocumentComparison(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    identity = models.ForeignKey(
        DocumentIdentity,
        on_delete=models.PROTECT,
        related_name="comparisons",
    )
    before_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="comparisons_as_before",
    )
    after_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="comparisons_as_after",
    )
    comparison_track_key = models.CharField(max_length=160, db_index=True)
    ruleset = models.CharField(max_length=128)
    configuration = models.JSONField(default=dict)
    configuration_hash = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    input_fingerprint = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
        unique=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    unchanged_count = models.PositiveIntegerField(default=0)
    added_count = models.PositiveIntegerField(default=0)
    removed_count = models.PositiveIntegerField(default=0)
    modified_count = models.PositiveIntegerField(default=0)
    moved_count = models.PositiveIntegerField(default=0)
    format_only_count = models.PositiveIntegerField(default=0)
    ambiguous_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("before_version", "after_version", "configuration_hash"),
                name="unique_document_comparison_pair_config",
            ),
            models.CheckConstraint(
                condition=~models.Q(before_version=models.F("after_version")),
                name="comparison_versions_differ",
            ),
        ]
        indexes = [
            models.Index(fields=("identity", "status", "created_at")),
            models.Index(fields=("comparison_track_key", "created_at")),
        ]

    def __str__(self) -> str:
        return f"{self.before_version_id} -> {self.after_version_id} [{self.status}]"


class ComparisonItem(AppendOnlyModel):
    class ChangeType(models.TextChoices):
        UNCHANGED = "unchanged", "Unchanged"
        ADDED = "added", "Added"
        REMOVED = "removed", "Removed"
        MODIFIED = "modified", "Modified"
        MOVED = "moved", "Moved"
        FORMAT_ONLY = "format_only", "Formatting only"
        AMBIGUOUS = "ambiguous", "Ambiguous"

    comparison = models.ForeignKey(
        DocumentComparison,
        on_delete=models.PROTECT,
        related_name="items",
    )
    before_anchor = models.ForeignKey(
        StructuralAnchor,
        on_delete=models.PROTECT,
        related_name="comparison_items_as_before",
        null=True,
        blank=True,
    )
    after_anchor = models.ForeignKey(
        StructuralAnchor,
        on_delete=models.PROTECT,
        related_name="comparison_items_as_after",
        null=True,
        blank=True,
    )
    change_type = models.CharField(max_length=16, choices=ChangeType.choices, db_index=True)
    match_strategy = models.CharField(max_length=64)
    similarity_score = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    text_delta = models.JSONField(default=dict)
    evidence = models.JSONField(default=dict)
    fingerprint = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("comparison", "created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("comparison", "fingerprint"),
                name="unique_comparison_item_fingerprint",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(before_anchor__isnull=False) | models.Q(after_anchor__isnull=False)
                ),
                name="comparison_item_has_anchor",
            ),
        ]
        indexes = [
            models.Index(fields=("comparison", "change_type")),
            models.Index(fields=("before_anchor", "after_anchor")),
        ]

    def __str__(self) -> str:
        return f"{self.comparison_id} {self.change_type}"


class ComparisonReview(AppendOnlyModel):
    class Decision(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        REJECTED = "rejected", "Rejected"
        NEEDS_CONTEXT = "needs_context", "Needs context"

    comparison_item = models.ForeignKey(
        ComparisonItem,
        on_delete=models.PROTECT,
        related_name="reviews",
    )
    decision = models.CharField(max_length=16, choices=Decision.choices, db_index=True)
    rationale = models.TextField(blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="comparison_reviews",
    )
    previous_review = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="superseding_reviews",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=("comparison_item", "created_at")),
            models.Index(fields=("decision", "created_at")),
        ]

    def __str__(self) -> str:
        return f"{self.comparison_item_id} [{self.decision}]"

    def clean(self) -> None:
        if (
            self.comparison_item_id
            and self.comparison_item.change_type == ComparisonItem.ChangeType.UNCHANGED
        ):
            raise ValidationError("Unchanged alignments do not require review.")
        if (
            self.previous_review_id
            and self.previous_review.comparison_item_id != self.comparison_item_id
        ):
            raise ValidationError("Previous review must belong to the same comparison item.")


class ComparisonSummary(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    comparison = models.ForeignKey(
        DocumentComparison,
        on_delete=models.PROTECT,
        related_name="summaries",
    )
    provider = models.CharField(max_length=64, default="openrouter")
    model = models.CharField(max_length=128)
    prompt_version = models.CharField(max_length=128)
    input_hash = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    input_snapshot = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    output = models.JSONField(default=dict)
    response_id = models.CharField(max_length=255, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("comparison", "provider", "model", "prompt_version", "input_hash"),
                name="unique_comparison_summary_input",
            )
        ]
        indexes = [
            models.Index(fields=("comparison", "status", "created_at")),
            models.Index(fields=("provider", "model", "created_at")),
        ]

    def __str__(self) -> str:
        return f"{self.comparison_id} {self.model} [{self.status}]"


class ReviewedChangePublication(AppendOnlyModel):
    comparison_item = models.ForeignKey(
        ComparisonItem,
        on_delete=models.PROTECT,
        related_name="reviewed_publications",
    )
    confirmation_review = models.OneToOneField(
        ComparisonReview,
        on_delete=models.PROTECT,
        related_name="change_publication",
    )
    pipeline_event = models.OneToOneField(
        PipelineEvent,
        on_delete=models.PROTECT,
        related_name="reviewed_change_publication",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_change_publications",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("comparison_item", "created_at"))]

    def __str__(self) -> str:
        return f"{self.comparison_item_id} -> {self.pipeline_event_id}"
