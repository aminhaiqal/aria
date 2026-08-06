from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.common.models import AppendOnlyModel
from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.fetching.models import FetchAttempt


class RawArtifact(AppendOnlyModel):
    class StorageBackend(models.TextChoices):
        FILESYSTEM = "filesystem", "Filesystem"
        S3 = "s3", "S3 compatible"

    sha256 = models.CharField(
        max_length=64,
        unique=True,
        validators=[RegexValidator(r"^[0-9a-f]{64}$", "Provide a lowercase SHA-256 digest.")],
    )
    byte_size = models.PositiveBigIntegerField()
    detected_content_type = models.CharField(max_length=255)
    storage_backend = models.CharField(max_length=16, choices=StorageBackend.choices)
    storage_key = models.CharField(max_length=1024, unique=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.sha256[:12]}… ({self.byte_size} bytes)"


class ArtifactObservation(AppendOnlyModel):
    raw_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="observations",
    )
    fetch_attempt = models.OneToOneField(
        FetchAttempt,
        on_delete=models.PROTECT,
        related_name="artifact_observation",
    )
    candidate = models.ForeignKey(
        DiscoveredCandidate,
        on_delete=models.PROTECT,
        related_name="artifact_observations",
    )
    source_run = models.ForeignKey(
        SourceRun,
        on_delete=models.PROTECT,
        related_name="artifact_observations",
    )
    requested_url = models.URLField(max_length=2048)
    final_url = models.URLField(max_length=2048)
    response_status = models.PositiveSmallIntegerField()
    response_headers = models.JSONField(default=dict)
    redirect_chain = models.JSONField(default=list)
    content_changed = models.BooleanField(default=True, db_index=True)
    retrieved_at = models.DateTimeField(default=timezone.now, db_index=True)
    connector_configuration_version = models.PositiveIntegerField()
    etag = models.CharField(max_length=512, blank=True)
    last_modified = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("-retrieved_at",)
        indexes = [models.Index(fields=("candidate", "retrieved_at"))]

    def __str__(self) -> str:
        return f"{self.candidate_id} observed {self.raw_artifact.sha256[:12]}…"


class ArtifactDerivative(AppendOnlyModel):
    class TransformationType(models.TextChoices):
        OCR_SEARCHABLE_PDF = "ocr_searchable_pdf", "OCR searchable PDF"
        OCR_TEXT_SIDECAR = "ocr_text_sidecar", "OCR text sidecar"
        BROWSER_RENDERED_DOM = "browser_rendered_dom", "Browser-rendered DOM"

    source_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="derived_outputs",
    )
    derived_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="derivations_as_output",
    )
    transformation_type = models.CharField(
        max_length=32,
        choices=TransformationType.choices,
        db_index=True,
    )
    profile = models.CharField(max_length=128)
    configuration_hash = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "source_artifact",
                    "derived_artifact",
                    "transformation_type",
                    "configuration_hash",
                ),
                name="unique_artifact_derivative_lineage",
            ),
            models.CheckConstraint(
                condition=~models.Q(source_artifact=models.F("derived_artifact")),
                name="artifact_derivative_differs_from_source",
            ),
        ]
        indexes = [models.Index(fields=("source_artifact", "transformation_type", "created_at"))]

    def __str__(self) -> str:
        return (
            f"{self.source_artifact.sha256[:12]}… -> "
            f"{self.derived_artifact.sha256[:12]}… ({self.transformation_type})"
        )
