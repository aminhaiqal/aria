from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.artifacts.models import ArtifactObservation, RawArtifact
from aria.collections.models import PublicationCollection
from aria.common.models import AppendOnlyModel, TimeStampedModel
from aria.extraction.models import ExtractedBlock, ExtractionRun


class DocumentIdentity(TimeStampedModel):
    collection = models.ForeignKey(
        PublicationCollection,
        on_delete=models.PROTECT,
        related_name="document_identities",
    )
    stable_key = models.CharField(
        max_length=64,
        unique=True,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    canonical_title = models.TextField(blank=True)
    canonical_url = models.URLField(max_length=2048)
    identity_basis = models.JSONField(default=dict)
    is_manual_override = models.BooleanField(default=False)

    class Meta:
        ordering = ("collection__authority__name", "canonical_title", "canonical_url")
        indexes = [models.Index(fields=("collection", "canonical_url"))]

    def __str__(self) -> str:
        return self.canonical_title or self.canonical_url


class DocumentVersion(AppendOnlyModel):
    identity = models.ForeignKey(
        DocumentIdentity,
        on_delete=models.PROTECT,
        related_name="versions",
    )
    normalized_content_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
        db_index=True,
    )
    title = models.TextField(blank=True)
    canonical_url = models.URLField(max_length=2048)
    language_hint = models.CharField(max_length=32, blank=True)
    plain_content = models.TextField()
    normalized_metadata = models.JSONField(default=dict)
    extractor_name = models.CharField(max_length=128)
    extractor_version = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("identity", "normalized_content_sha256"),
                name="unique_normalized_version_per_identity",
            )
        ]
        indexes = [models.Index(fields=("identity", "created_at"))]

    def __str__(self) -> str:
        return f"{self.identity} @ {self.normalized_content_sha256[:12]}…"


class VersionEvidence(AppendOnlyModel):
    document_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="evidence_records",
    )
    raw_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="document_version_evidence",
    )
    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.PROTECT,
        related_name="version_evidence_records",
    )
    artifact_observation = models.OneToOneField(
        ArtifactObservation,
        on_delete=models.PROTECT,
        related_name="document_version_evidence",
        null=True,
        blank=True,
    )
    observed_url = models.URLField(max_length=2048, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.document_version_id} from {self.raw_artifact.sha256[:12]}…"


class NormalizedSection(AppendOnlyModel):
    document_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="sections",
    )
    source_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="normalized_sections",
    )
    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.PROTECT,
        related_name="normalized_sections",
    )
    source_block = models.ForeignKey(
        ExtractedBlock,
        on_delete=models.PROTECT,
        related_name="normalized_sections",
    )
    ordinal = models.PositiveIntegerField()
    section_type = models.CharField(max_length=32)
    heading = models.TextField(blank=True)
    text = models.TextField()
    text_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
        db_index=True,
    )
    page_number = models.PositiveIntegerField(null=True, blank=True)
    char_start = models.PositiveBigIntegerField()
    char_end = models.PositiveBigIntegerField()
    source_locator = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("document_version", "ordinal")
        constraints = [
            models.UniqueConstraint(
                fields=("document_version", "ordinal"),
                name="unique_normalized_section_ordinal",
            ),
            models.CheckConstraint(
                condition=models.Q(char_end__gte=models.F("char_start")),
                name="normalized_section_char_range_valid",
            ),
        ]
        indexes = [
            models.Index(fields=("document_version", "page_number", "ordinal")),
            GinIndex(
                SearchVector("heading", "text", config="simple"),
                name="normalized_section_fts",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document_version_id}:{self.ordinal}"
