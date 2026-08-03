from django.db import models

from aria.common.models import TimeStampedModel
from aria.discovery.models import DiscoveredCandidate, SourceRun


class FetchAttempt(TimeStampedModel):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        NOT_MODIFIED = "not_modified", "Not modified"
        RETRYABLE_FAILURE = "retryable_failure", "Retryable failure"
        PERMANENT_FAILURE = "permanent_failure", "Permanent failure"
        QUARANTINED = "quarantined", "Quarantined"

    candidate = models.ForeignKey(
        DiscoveredCandidate,
        on_delete=models.PROTECT,
        related_name="fetch_attempts",
    )
    source_run = models.ForeignKey(
        SourceRun,
        on_delete=models.PROTECT,
        related_name="fetch_attempts",
    )
    attempt_number = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.RUNNING,
        db_index=True,
    )
    requested_url = models.URLField(max_length=2048)
    final_url = models.URLField(max_length=2048, blank=True)
    request_headers = models.JSONField(default=dict)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_headers = models.JSONField(default=dict)
    redirect_chain = models.JSONField(default=list)
    resolved_addresses = models.JSONField(default=list)
    bytes_received = models.PositiveBigIntegerField(default=0)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("source_run", "candidate", "attempt_number"),
                name="unique_fetch_attempt_number_per_observation",
            )
        ]
        indexes = [models.Index(fields=("candidate", "status", "created_at"))]

    def __str__(self) -> str:
        return f"{self.candidate_id} attempt {self.attempt_number} [{self.status}]"
