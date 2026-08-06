from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.artifacts.models import ArtifactDerivative, RawArtifact
from aria.common.models import AppendOnlyModel, TimeStampedModel
from aria.discovery.models import SourceRun
from aria.sources.models import SourceEndpoint


class BrowserCapture(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        QUARANTINED = "quarantined", "Quarantined"

    source_run = models.OneToOneField(
        SourceRun,
        on_delete=models.PROTECT,
        related_name="browser_capture",
    )
    endpoint = models.ForeignKey(
        SourceEndpoint,
        on_delete=models.PROTECT,
        related_name="browser_captures",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    profile = models.CharField(max_length=128)
    configuration = models.JSONField(default=dict)
    configuration_hash = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    toolchain = models.JSONField(default=dict, blank=True)
    requested_url = models.URLField(max_length=2048)
    final_url = models.URLField(max_length=2048, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_headers = models.JSONField(default=dict, blank=True)
    redirect_chain = models.JSONField(default=list, blank=True)
    resolved_addresses = models.JSONField(default=list, blank=True)
    original_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="browser_captures_as_original",
        null=True,
        blank=True,
    )
    rendered_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="browser_captures_as_rendered",
        null=True,
        blank=True,
    )
    rendered_derivative = models.ForeignKey(
        ArtifactDerivative,
        on_delete=models.PROTECT,
        related_name="browser_captures",
        null=True,
        blank=True,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    request_count = models.PositiveIntegerField(default=0)
    blocked_request_count = models.PositiveIntegerField(default=0)
    response_bytes = models.PositiveBigIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("endpoint", "status", "created_at")),
            models.Index(fields=("status", "updated_at")),
        ]

    def clean(self) -> None:
        if self.source_run_id and self.endpoint_id != self.source_run.endpoint_id:
            raise ValidationError("Browser capture must match its source run endpoint.")
        if self.endpoint_id and (
            self.endpoint.connector_type != SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING
            or not self.endpoint.requires_javascript
        ):
            raise ValidationError(
                "Browser capture requires an explicitly JavaScript-enabled endpoint."
            )
        if self.rendered_derivative_id:
            derivative = self.rendered_derivative
            if (
                derivative.transformation_type
                != ArtifactDerivative.TransformationType.BROWSER_RENDERED_DOM
            ):
                raise ValidationError("Browser capture derivative must be a rendered DOM.")
            if self.original_artifact_id != derivative.source_artifact_id:
                raise ValidationError(
                    "Rendered DOM derivative must start from the original response."
                )
            if self.rendered_artifact_id != derivative.derived_artifact_id:
                raise ValidationError("Rendered DOM artifact must match its derivative record.")
        if self.status == self.Status.COMPLETED:
            if not self.original_artifact_id or not self.rendered_artifact_id:
                raise ValidationError(
                    "Completed browser captures require both immutable artifacts."
                )
            if self.response_status is None or self.finished_at is None:
                raise ValidationError("Completed browser captures require response evidence.")
            if (
                self.original_artifact_id != self.rendered_artifact_id
                and not self.rendered_derivative_id
            ):
                raise ValidationError("A changed rendered DOM requires derivative lineage.")
        if self.status in {self.Status.FAILED, self.Status.QUARANTINED} and not self.error_code:
            raise ValidationError("Failed browser captures require an error code.")

    def __str__(self) -> str:
        return f"{self.source_run_id} [{self.status}]"


class BrowserNetworkExchange(AppendOnlyModel):
    class Disposition(models.TextChoices):
        ALLOWED = "allowed", "Allowed"
        BLOCKED = "blocked", "Blocked"

    capture = models.ForeignKey(
        BrowserCapture,
        on_delete=models.PROTECT,
        related_name="network_exchanges",
    )
    attempt_number = models.PositiveSmallIntegerField()
    sequence = models.PositiveIntegerField()
    requested_url = models.URLField(max_length=2048)
    method = models.CharField(max_length=16)
    resource_type = models.CharField(max_length=32)
    disposition = models.CharField(max_length=16, choices=Disposition.choices, db_index=True)
    block_reason = models.CharField(max_length=255, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    content_type = models.CharField(max_length=255, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    body_sha256 = models.CharField(
        max_length=64,
        blank=True,
        validators=[RegexValidator(r"^$|^[0-9a-f]{64}$")],
    )
    body_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="browser_network_exchanges",
        null=True,
        blank=True,
    )
    resolved_addresses = models.JSONField(default=list, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("capture", "attempt_number", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("capture", "attempt_number", "sequence"),
                name="unique_browser_exchange_sequence",
            )
        ]
        indexes = [
            models.Index(fields=("capture", "disposition", "sequence")),
        ]

    def clean(self) -> None:
        if self.body_artifact_id:
            if self.body_sha256 != self.body_artifact.sha256:
                raise ValidationError("Network exchange body hash must match its artifact.")
            if self.byte_size != self.body_artifact.byte_size:
                raise ValidationError("Network exchange byte size must match its body artifact.")
        elif self.body_sha256 or self.byte_size:
            raise ValidationError("Network exchange body evidence requires an artifact.")
        if self.disposition == self.Disposition.BLOCKED:
            if not self.block_reason:
                raise ValidationError("Blocked browser requests require a reason.")
            if self.response_status is not None or self.body_artifact_id:
                raise ValidationError("Blocked browser requests cannot contain response evidence.")
        elif self.block_reason:
            raise ValidationError("Allowed browser requests cannot contain a block reason.")

    def __str__(self) -> str:
        return f"{self.capture_id}:{self.attempt_number}:{self.sequence}"
