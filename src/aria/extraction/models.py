from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.artifacts.models import RawArtifact
from aria.common.models import AppendOnlyModel, TimeStampedModel


class ExtractionRun(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        OCR_REQUIRED = "ocr_required", "OCR required"
        RETRYABLE_FAILURE = "retryable_failure", "Retryable failure"
        PERMANENT_FAILURE = "permanent_failure", "Permanent failure"

    raw_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="extraction_runs",
    )
    extractor_name = models.CharField(max_length=128)
    extractor_version = models.CharField(max_length=64)
    configuration_hash = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "raw_artifact",
                    "extractor_name",
                    "extractor_version",
                    "configuration_hash",
                ),
                name="unique_extraction_configuration_per_artifact",
            )
        ]
        indexes = [models.Index(fields=("raw_artifact", "status"))]

    def __str__(self) -> str:
        return f"{self.raw_artifact.sha256[:12]}… {self.extractor_name} [{self.status}]"


class ExtractedDocument(AppendOnlyModel):
    extraction_run = models.OneToOneField(
        ExtractionRun,
        on_delete=models.PROTECT,
        related_name="extracted_document",
    )
    raw_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="extracted_documents",
    )
    title = models.TextField(blank=True)
    language_hint = models.CharField(max_length=32, blank=True)
    plain_text = models.TextField()
    plain_text_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
        db_index=True,
    )
    metadata = models.JSONField(default=dict)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    requires_ocr = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.title or self.plain_text_sha256[:12]


class ExtractedBlock(AppendOnlyModel):
    class BlockType(models.TextChoices):
        HEADING = "heading", "Heading"
        PARAGRAPH = "paragraph", "Paragraph"
        LIST_ITEM = "list_item", "List item"
        TABLE = "table", "Table"
        QUOTE = "quote", "Quote"
        CODE = "code", "Code"
        PAGE = "page", "PDF page"

    extracted_document = models.ForeignKey(
        ExtractedDocument,
        on_delete=models.PROTECT,
        related_name="blocks",
    )
    ordinal = models.PositiveIntegerField()
    block_type = models.CharField(max_length=32, choices=BlockType.choices)
    heading_level = models.PositiveSmallIntegerField(null=True, blank=True)
    heading = models.TextField(blank=True)
    text = models.TextField()
    text_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    page_number = models.PositiveIntegerField(null=True, blank=True)
    char_start = models.PositiveBigIntegerField()
    char_end = models.PositiveBigIntegerField()
    source_locator = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("ordinal",)
        constraints = [
            models.UniqueConstraint(
                fields=("extracted_document", "ordinal"),
                name="unique_extracted_block_ordinal",
            ),
            models.CheckConstraint(
                condition=models.Q(char_end__gte=models.F("char_start")),
                name="extracted_block_char_range_valid",
            ),
        ]
        indexes = [models.Index(fields=("extracted_document", "page_number", "ordinal"))]

    def __str__(self) -> str:
        return f"{self.extracted_document_id}:{self.ordinal} {self.block_type}"
