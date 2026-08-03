from django.db import models
from django.utils import timezone

from aria.common.models import AppendOnlyModel, UUIDModel


class PipelineEvent(AppendOnlyModel):
    event_type = models.CharField(max_length=128, db_index=True)
    aggregate_type = models.CharField(max_length=64, db_index=True)
    aggregate_id = models.UUIDField(db_index=True)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-occurred_at",)
        indexes = [models.Index(fields=("aggregate_type", "aggregate_id", "occurred_at"))]

    def __str__(self) -> str:
        return f"{self.event_type} ({self.aggregate_type}:{self.aggregate_id})"


class OutboxEvent(UUIDModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PUBLISHING = "publishing", "Publishing"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    pipeline_event = models.OneToOneField(
        PipelineEvent,
        on_delete=models.PROTECT,
        related_name="outbox_event",
    )
    topic = models.CharField(max_length=128)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ("available_at",)
        indexes = [models.Index(fields=("status", "available_at"))]

    def __str__(self) -> str:
        return f"{self.topic} [{self.status}]"


class AuditEvent(AppendOnlyModel):
    action = models.CharField(max_length=128, db_index=True)
    actor_type = models.CharField(max_length=32, default="system")
    actor_identifier = models.CharField(max_length=255, blank=True)
    target_type = models.CharField(max_length=64, db_index=True)
    target_id = models.UUIDField(db_index=True)
    details = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-occurred_at",)
        indexes = [models.Index(fields=("target_type", "target_id", "occurred_at"))]

    def __str__(self) -> str:
        return f"{self.action} ({self.target_type}:{self.target_id})"
