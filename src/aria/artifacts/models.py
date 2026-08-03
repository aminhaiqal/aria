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
    retrieved_at = models.DateTimeField(default=timezone.now, db_index=True)
    connector_configuration_version = models.PositiveIntegerField()
    etag = models.CharField(max_length=512, blank=True)
    last_modified = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("-retrieved_at",)
        indexes = [models.Index(fields=("candidate", "retrieved_at"))]

    def __str__(self) -> str:
        return f"{self.candidate_id} observed {self.raw_artifact.sha256[:12]}…"
