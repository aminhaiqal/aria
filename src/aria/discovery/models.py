from django.db import models

from aria.common.models import TimeStampedModel
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
