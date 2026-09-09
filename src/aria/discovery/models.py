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


class MonitoredResource(TimeStampedModel):
    class ResourceType(models.TextChoices):
        DETAIL_PAGE = "detail_page", "Detail page"
        RSS = "rss", "RSS feed"
        ATOM = "atom", "Atom feed"

    class HealthState(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        HEALTHY = "healthy", "Healthy"
        DEGRADED = "degraded", "Degraded"
        UNHEALTHY = "unhealthy", "Unhealthy"
        DISABLED = "disabled", "Disabled"

    endpoint = models.ForeignKey(
        SourceEndpoint,
        on_delete=models.PROTECT,
        related_name="monitored_resources",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    resource_type = models.CharField(max_length=16, choices=ResourceType.choices, db_index=True)
    url = models.URLField(max_length=2048)
    fingerprint = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    title = models.CharField(max_length=512, blank=True)
    is_approved = models.BooleanField(default=False, db_index=True)
    approval_basis = models.CharField(max_length=255, blank=True)
    is_enabled = models.BooleanField(default=True, db_index=True)
    retired_at = models.DateTimeField(null=True, blank=True, db_index=True)
    retirement_reason = models.TextField(blank=True)
    retirement_actor_identifier = models.CharField(max_length=255, blank=True)
    polling_interval_minutes = models.PositiveIntegerField(default=360)
    next_poll_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_changed_at = models.DateTimeField(null=True, blank=True)
    last_successful_run_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    health_state = models.CharField(
        max_length=16,
        choices=HealthState.choices,
        default=HealthState.UNKNOWN,
        db_index=True,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("endpoint", "resource_type", "url")
        constraints = [
            models.UniqueConstraint(
                fields=("endpoint", "fingerprint"),
                name="unique_resource_fingerprint_per_endpoint",
            ),
            models.CheckConstraint(
                condition=models.Q(polling_interval_minutes__gte=1),
                name="resource_poll_interval_at_least_one_minute",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        retired_at__isnull=True,
                        retirement_reason="",
                        retirement_actor_identifier="",
                    )
                    | (
                        models.Q(
                            is_enabled=False,
                            health_state="disabled",
                            next_poll_at__isnull=True,
                        )
                        & ~models.Q(retirement_reason="")
                        & ~models.Q(retirement_actor_identifier="")
                    )
                ),
                name="monitored_resource_retirement_contract",
            ),
        ]
        indexes = [
            models.Index(fields=("is_enabled", "is_approved", "next_poll_at")),
            models.Index(fields=("endpoint", "resource_type", "health_state")),
        ]

    def clean(self) -> None:
        if self.parent_id and self.parent.endpoint_id != self.endpoint_id:
            raise ValidationError("A monitored resource parent must belong to the same endpoint.")
        if self.parent_id and self.parent_id == self.id:
            raise ValidationError("A monitored resource cannot be its own parent.")
        if self.retired_at:
            errors = {}
            if self.is_enabled:
                errors["is_enabled"] = "A retired resource cannot remain enabled."
            if self.health_state != self.HealthState.DISABLED:
                errors["health_state"] = "A retired resource must have disabled health."
            if self.next_poll_at is not None:
                errors["next_poll_at"] = "A retired resource cannot have a next poll."
            if not self.retirement_reason.strip():
                errors["retirement_reason"] = "A retirement reason is required."
            if not self.retirement_actor_identifier.strip():
                errors["retirement_actor_identifier"] = "A retirement actor is required."
            if errors:
                raise ValidationError(errors)
        elif self.retirement_reason or self.retirement_actor_identifier:
            raise ValidationError(
                "Retirement details cannot be recorded without a retirement timestamp."
            )

    @property
    def is_retired(self) -> bool:
        return self.retired_at is not None

    def __str__(self) -> str:
        return f"{self.get_resource_type_display()}: {self.url}"


class ResourceRun(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    resource = models.ForeignKey(
        MonitoredResource,
        on_delete=models.PROTECT,
        related_name="runs",
    )
    source_run = models.OneToOneField(
        SourceRun,
        on_delete=models.PROTECT,
        related_name="resource_run",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    idempotency_key = models.CharField(max_length=255, unique=True)
    cursor_before = models.JSONField(null=True, blank=True)
    cursor_after = models.JSONField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    observed_link_count = models.PositiveIntegerField(default=0)
    accepted_candidate_count = models.PositiveIntegerField(default=0)
    quarantined_link_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("resource", "status", "created_at"))]

    def clean(self) -> None:
        if self.source_run_id and self.resource_id:
            if self.source_run.endpoint_id != self.resource.endpoint_id:
                raise ValidationError("Resource run and source run must use the same endpoint.")

    def __str__(self) -> str:
        return f"{self.resource} — {self.created_at:%Y-%m-%d %H:%M:%S}"


class ResourceStructureIncident(AppendOnlyModel):
    resource = models.ForeignKey(
        MonitoredResource,
        on_delete=models.PROTECT,
        related_name="structure_incidents",
    )
    resource_run = models.OneToOneField(
        ResourceRun,
        on_delete=models.PROTECT,
        related_name="structure_incident",
    )
    error_code = models.CharField(max_length=128)
    error_message = models.TextField()
    expected_selectors = models.JSONField(default=list)
    requested_url = models.URLField(max_length=2048)
    final_url = models.URLField(max_length=2048)
    response_status = models.PositiveSmallIntegerField()
    content_type = models.CharField(max_length=255, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    content_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    structure_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    structure_sample = models.JSONField(default=list)
    redirect_chain = models.JSONField(default=list, blank=True)
    pause_until = models.DateTimeField(db_index=True)
    detected_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-detected_at", "-id")
        indexes = [
            models.Index(fields=("resource", "detected_at")),
            models.Index(fields=("structure_sha256", "detected_at")),
        ]

    def clean(self) -> None:
        errors = {}
        if self.resource_run_id and self.resource_id:
            if self.resource_run.resource_id != self.resource_id:
                errors["resource_run"] = "The incident run must belong to the resource."
        if not isinstance(self.expected_selectors, list) or any(
            not isinstance(value, str) or not value.strip()
            for value in self.expected_selectors
        ):
            errors["expected_selectors"] = "Expected selectors must be non-empty strings."
        if not isinstance(self.structure_sample, list) or any(
            not isinstance(value, str) for value in self.structure_sample
        ):
            errors["structure_sample"] = "The structure sample must contain strings only."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.resource_id} [{self.error_code}] {self.detected_at:%Y-%m-%d %H:%M:%S}"


class ResourceObservation(AppendOnlyModel):
    class Outcome(models.TextChoices):
        CHANGED = "changed", "Changed"
        UNCHANGED = "unchanged", "Unchanged"
        NOT_MODIFIED = "not_modified", "HTTP not modified"

    resource_run = models.OneToOneField(
        ResourceRun,
        on_delete=models.PROTECT,
        related_name="observation",
    )
    resource = models.ForeignKey(
        MonitoredResource,
        on_delete=models.PROTECT,
        related_name="observations",
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
    link_set_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    link_count = models.PositiveIntegerField(default=0)
    checked_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-checked_at", "-id")
        indexes = [models.Index(fields=("resource", "outcome", "checked_at"))]
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
                name="resource_observation_outcome_matches_status",
            )
        ]

    def clean(self) -> None:
        if self.resource_run_id and self.resource_id:
            if self.resource_run.resource_id != self.resource_id:
                raise ValidationError("Resource observation must match its resource run.")
        if self.previous_observation_id:
            if self.previous_observation.resource_id != self.resource_id:
                raise ValidationError("Previous observation must belong to the same resource.")

    def __str__(self) -> str:
        return f"{self.resource_id} {self.checked_at:%Y-%m-%d %H:%M:%S} [{self.outcome}]"


class ResourceLinkObservation(AppendOnlyModel):
    class State(models.TextChoices):
        ADDED = "added", "Added"
        RETAINED = "retained", "Retained"
        REMOVED = "removed", "Removed"

    class Disposition(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        QUARANTINED = "quarantined", "Quarantined"

    resource_observation = models.ForeignKey(
        ResourceObservation,
        on_delete=models.PROTECT,
        related_name="link_observations",
    )
    target_url = models.URLField(max_length=2048)
    fingerprint = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    title = models.CharField(max_length=1024, blank=True)
    external_identifier = models.CharField(max_length=512, blank=True)
    relation = models.CharField(max_length=32, default="document")
    state = models.CharField(max_length=16, choices=State.choices, db_index=True)
    disposition = models.CharField(
        max_length=16,
        choices=Disposition.choices,
        db_index=True,
    )
    quarantine_reason = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("resource_observation", "target_url")
        constraints = [
            models.UniqueConstraint(
                fields=("resource_observation", "fingerprint"),
                name="unique_link_per_resource_observation",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(disposition="accepted", quarantine_reason="")
                    | models.Q(disposition="quarantined")
                ),
                name="resource_link_quarantine_reason_contract",
            ),
        ]
        indexes = [
            models.Index(fields=("resource_observation", "state", "disposition")),
        ]

    def __str__(self) -> str:
        return f"{self.state}: {self.target_url}"
