from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.common.models import AppendOnlyModel
from aria.discovery.models import SourceRun
from aria.sources.models import SourceEndpoint


class SourceReliabilityAssessment(AppendOnlyModel):
    class Status(models.TextChoices):
        HEALTHY = "healthy", "Healthy"
        PROCESSING = "processing", "Processing"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"
        DISABLED = "disabled", "Disabled"

    endpoint = models.ForeignKey(
        SourceEndpoint,
        on_delete=models.PROTECT,
        related_name="reliability_assessments",
    )
    source_run = models.ForeignKey(
        SourceRun,
        on_delete=models.PROTECT,
        related_name="reliability_assessments",
        null=True,
        blank=True,
    )
    previous_assessment = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="next_assessments",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    assessment_signature = models.CharField(
        max_length=64,
        unique=True,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    freshness_deadline = models.DateTimeField(null=True, blank=True, db_index=True)
    candidate_set_sha256 = models.CharField(
        max_length=64,
        blank=True,
        validators=[RegexValidator(r"^$|^[0-9a-f]{64}$")],
    )
    previous_candidate_set_sha256 = models.CharField(
        max_length=64,
        blank=True,
        validators=[RegexValidator(r"^$|^[0-9a-f]{64}$")],
    )
    candidate_set_changed = models.BooleanField(default=False)
    candidate_count = models.PositiveIntegerField(default=0)
    artifact_ready_count = models.PositiveIntegerField(default=0)
    extraction_ready_count = models.PositiveIntegerField(default=0)
    graph_ready_count = models.PositiveIntegerField(default=0)
    document_version_count = models.PositiveIntegerField(default=0)
    section_count = models.PositiveIntegerField(default=0)
    local_embedding_count = models.PositiveIntegerField(default=0)
    configured_embedding_count = models.PositiveIntegerField(default=0)
    configured_embedding_provider = models.CharField(max_length=64, blank=True)
    configured_embedding_model = models.CharField(max_length=128, blank=True)
    browser_capture_ready = models.BooleanField(default=False)
    network_policy_ready = models.BooleanField(default=False)
    findings = models.JSONField(default=list, blank=True)
    assessed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-assessed_at", "-id")
        indexes = [
            models.Index(fields=("endpoint", "status", "assessed_at")),
            models.Index(fields=("status", "freshness_deadline")),
        ]

    def clean(self) -> None:
        if self.source_run_id and self.source_run.endpoint_id != self.endpoint_id:
            raise ValidationError("Reliability assessment run must match its endpoint.")
        if self.previous_assessment_id and self.previous_assessment.endpoint_id != self.endpoint_id:
            raise ValidationError("Previous reliability assessment must match its endpoint.")
        for field_name in (
            "artifact_ready_count",
            "extraction_ready_count",
            "graph_ready_count",
        ):
            if getattr(self, field_name) > self.candidate_count:
                raise ValidationError(f"{field_name} cannot exceed candidate_count.")
        if self.local_embedding_count > self.section_count:
            raise ValidationError("Local embedding count cannot exceed section count.")
        if self.configured_embedding_count > self.section_count:
            raise ValidationError("Configured embedding count cannot exceed section count.")
        if not isinstance(self.findings, list) or any(
            not isinstance(finding, dict) or not {"code", "severity", "detail"}.issubset(finding)
            for finding in self.findings
        ):
            raise ValidationError("Reliability findings require code, severity, and detail.")

    def __str__(self) -> str:
        return f"{self.endpoint_id} [{self.status}] {self.assessed_at:%Y-%m-%d %H:%M:%S}"
