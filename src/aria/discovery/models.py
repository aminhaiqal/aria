from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.common.models import AppendOnlyModel, TimeStampedModel
from aria.sources.models import SourceEndpoint


class SourceRun(TimeStampedModel):
    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        MANUAL = "manual", "Manual"
        REPLAY = "replay", "Replay"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    endpoint = models.ForeignKey(
        SourceEndpoint,
        on_delete=models.PROTECT,
        related_name="source_runs",
    )
    trigger = models.CharField(max_length=16, choices=Trigger.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    idempotency_key = models.CharField(max_length=255, unique=True)
    connector_configuration_version = models.PositiveIntegerField()
    cursor_before = models.JSONField(null=True, blank=True)
    cursor_after = models.JSONField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    discovered_candidate_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("endpoint", "status", "created_at"))]

    def __str__(self) -> str:
        return f"{self.endpoint} — {self.created_at:%Y-%m-%d %H:%M:%S}"


class DiscoveredCandidate(TimeStampedModel):
    class PipelineState(models.TextChoices):
        DISCOVERED = "discovered", "Discovered"
        FETCH_PENDING = "fetch_pending", "Fetch pending"
        FETCHED = "fetched", "Fetched"
        RAW_STORED = "raw_stored", "Raw stored"
        EXTRACTED = "extracted", "Extracted"
        NORMALIZED = "normalized", "Normalized"
        IDENTITY_RESOLVED = "identity_resolved", "Identity resolved"
        VERSIONED = "versioned", "Versioned"
        DIFFED = "diffed", "Diffed"
        PUBLISHED = "published", "Published"
        RETRYABLE_FAILURE = "retryable_failure", "Retryable failure"
        PERMANENT_FAILURE = "permanent_failure", "Permanent failure"
        QUARANTINED = "quarantined", "Quarantined"
        MANUAL_REVIEW = "manual_review", "Manual review"

    endpoint = models.ForeignKey(
        SourceEndpoint,
        on_delete=models.PROTECT,
        related_name="discovered_candidates",
    )
    latest_source_run = models.ForeignKey(
        SourceRun,
        on_delete=models.PROTECT,
        related_name="latest_candidates",
    )
    discovered_url = models.URLField(max_length=2048)
    canonical_url = models.URLField(max_length=2048, blank=True)
    external_identifier = models.CharField(max_length=512, blank=True)
    fingerprint = models.CharField(max_length=64)
    metadata_hints = models.JSONField(default=dict, blank=True)
    pipeline_state = models.CharField(
        max_length=32,
        choices=PipelineState.choices,
        default=PipelineState.DISCOVERED,
        db_index=True,
    )
    first_discovered_at = models.DateTimeField()
    last_discovered_at = models.DateTimeField()

    class Meta:
        ordering = ("-last_discovered_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("endpoint", "fingerprint"),
                name="unique_candidate_fingerprint_per_endpoint",
            )
        ]
        indexes = [models.Index(fields=("endpoint", "pipeline_state"))]

    def __str__(self) -> str:
        return self.canonical_url or self.discovered_url


class CandidateObservation(TimeStampedModel):
    source_run = models.ForeignKey(
        SourceRun,
        on_delete=models.PROTECT,
        related_name="candidate_observations",
    )
    candidate = models.ForeignKey(
        DiscoveredCandidate,
        on_delete=models.PROTECT,
        related_name="observations",
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("source_run", "candidate"),
                name="unique_candidate_observation_per_run",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source_run_id}: {self.candidate_id}"


class EndpointObservation(AppendOnlyModel):
    class Outcome(models.TextChoices):
        CHANGED = "changed", "Changed"
        UNCHANGED = "unchanged", "Unchanged"
        NOT_MODIFIED = "not_modified", "HTTP not modified"

    source_run = models.OneToOneField(
        SourceRun,
        on_delete=models.PROTECT,
        related_name="endpoint_observation",
    )
    endpoint = models.ForeignKey(
        SourceEndpoint,
        on_delete=models.PROTECT,
        related_name="endpoint_observations",
    )
    previous_observation = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="next_observations",
        null=True,
        blank=True,
    )
    outcome = models.CharField(max_length=16, choices=Outcome.choices, db_index=True)
    requested_url = models.URLField(max_length=2048)
    final_url = models.URLField(max_length=2048)
    response_status = models.PositiveSmallIntegerField()
    request_headers = models.JSONField(default=dict, blank=True)
    response_headers = models.JSONField(default=dict, blank=True)
    redirect_chain = models.JSONField(default=list, blank=True)
    resolved_addresses = models.JSONField(default=list, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    content_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    etag = models.CharField(max_length=512, blank=True)
    last_modified = models.CharField(max_length=255, blank=True)
    connector_configuration_version = models.PositiveIntegerField()
    checked_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-checked_at", "-id")
        indexes = [models.Index(fields=("endpoint", "outcome", "checked_at"))]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(outcome="not_modified", response_status=304)
                    | models.Q(
                        outcome__in=("changed", "unchanged"),
                        response_status__gte=200,
                        response_status__lt=300,
                    )
                ),
                name="endpoint_observation_outcome_matches_status",
            )
        ]

    def clean(self) -> None:
        if self.source_run_id and self.endpoint_id != self.source_run.endpoint_id:
            raise ValidationError("Endpoint observation must match its source run endpoint.")
        if (
            self.previous_observation_id
            and self.previous_observation.endpoint_id != self.endpoint_id
        ):
            raise ValidationError("Previous observation must belong to the same endpoint.")

    def __str__(self) -> str:
        return f"{self.endpoint_id} {self.checked_at:%Y-%m-%d %H:%M:%S} [{self.outcome}]"
