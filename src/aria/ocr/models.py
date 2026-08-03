from django.core.validators import RegexValidator
from django.db import models

from aria.artifacts.models import ArtifactDerivative, RawArtifact
from aria.common.models import TimeStampedModel


class OCRRun(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        RETRYABLE_FAILURE = "retryable_failure", "Retryable failure"
        PERMANENT_FAILURE = "permanent_failure", "Permanent failure"

    source_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="ocr_runs",
    )
    profile_name = models.CharField(max_length=128)
    profile_version = models.CharField(max_length=64)
    configuration = models.JSONField(default=dict)
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
    searchable_pdf_derivative = models.ForeignKey(
        ArtifactDerivative,
        on_delete=models.PROTECT,
        related_name="searchable_pdf_ocr_runs",
        null=True,
        blank=True,
    )
    text_sidecar_derivative = models.ForeignKey(
        ArtifactDerivative,
        on_delete=models.PROTECT,
        related_name="text_sidecar_ocr_runs",
        null=True,
        blank=True,
    )
    toolchain = models.JSONField(default=dict, blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    non_whitespace_characters = models.PositiveBigIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "source_artifact",
                    "profile_name",
                    "profile_version",
                    "configuration_hash",
                ),
                name="unique_ocr_configuration_per_artifact",
            )
        ]
        indexes = [models.Index(fields=("source_artifact", "status", "created_at"))]

    def __str__(self) -> str:
        return (
            f"{self.source_artifact.sha256[:12]}… "
            f"{self.profile_name}:{self.profile_version} [{self.status}]"
        )
