from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from aria.collections.models import PublicationCollection
from aria.common.models import AppendOnlyModel, TimeStampedModel


class SourceEndpoint(TimeStampedModel):
    class ConnectorType(models.TextChoices):
        RSS = "rss", "RSS or Atom"
        HTML_LISTING = "html_listing", "Static HTML listing"
        DIRECT_DOCUMENT = "direct_document", "Direct document"
        SITEMAP = "sitemap", "Sitemap"
        REST_API = "rest_api", "REST API"
        JAVASCRIPT_LISTING = "javascript_listing", "JavaScript listing"
        EMAIL = "email", "Email"
        MANUAL_UPLOAD = "manual_upload", "Manual upload"

    class PaginationStrategy(models.TextChoices):
        NONE = "none", "None"
        PAGE = "page", "Page number"
        CURSOR = "cursor", "Cursor"
        NEXT_LINK = "next_link", "Next link"
        LOAD_MORE = "load_more", "Load more"

    class HealthState(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        HEALTHY = "healthy", "Healthy"
        DEGRADED = "degraded", "Degraded"
        UNHEALTHY = "unhealthy", "Unhealthy"
        DISABLED = "disabled", "Disabled"

    collection = models.ForeignKey(
        PublicationCollection,
        on_delete=models.PROTECT,
        related_name="source_endpoints",
    )
    name = models.CharField(max_length=255)
    discovery_url = models.URLField(max_length=2048)
    connector_type = models.CharField(max_length=32, choices=ConnectorType.choices)
    allowed_domains = models.JSONField(default=list)
    polling_interval_minutes = models.PositiveIntegerField(default=60)
    next_poll_at = models.DateTimeField(null=True, blank=True, db_index=True)
    pagination_strategy = models.CharField(
        max_length=32,
        choices=PaginationStrategy.choices,
        default=PaginationStrategy.NONE,
    )
    expected_content_types = models.JSONField(default=list, blank=True)
    requires_javascript = models.BooleanField(default=False)
    connector_configuration_version = models.PositiveIntegerField(default=1)
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
    is_enabled = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("collection__authority__name", "collection__name", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("collection", "name"),
                name="unique_endpoint_name_per_collection",
            ),
            models.CheckConstraint(
                condition=models.Q(polling_interval_minutes__gte=1),
                name="source_poll_interval_at_least_one_minute",
            ),
        ]
        indexes = [models.Index(fields=("is_enabled", "next_poll_at"))]

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.allowed_domains, list) or not self.allowed_domains:
            raise ValidationError({"allowed_domains": "Provide at least one allowed domain."})
        if self.connector_type == self.ConnectorType.JAVASCRIPT_LISTING:
            self.requires_javascript = True

    def __str__(self) -> str:
        return f"{self.collection}: {self.name}"


class ConnectorConfiguration(TimeStampedModel):
    endpoint = models.ForeignKey(
        SourceEndpoint,
        on_delete=models.CASCADE,
        related_name="connector_configurations",
    )
    version = models.PositiveIntegerField()
    configuration = models.JSONField(default=dict)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("endpoint", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("endpoint", "version"),
                name="unique_connector_configuration_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.endpoint} v{self.version}"


class SourcePackSnapshot(AppendOnlyModel):
    endpoint = models.ForeignKey(
        SourceEndpoint,
        on_delete=models.PROTECT,
        related_name="source_pack_snapshots",
    )
    pack_slug = models.SlugField(max_length=128)
    schema_version = models.PositiveIntegerField()
    pack_version = models.PositiveIntegerField()
    checksum = models.CharField(
        max_length=64,
        unique=True,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    definition = models.JSONField()
    applied_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-applied_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("pack_slug", "pack_version"),
                name="unique_source_pack_version",
            )
        ]
        indexes = [models.Index(fields=("endpoint", "applied_at"))]

    def clean(self) -> None:
        if not isinstance(self.definition, dict):
            raise ValidationError("Source pack snapshots require an object definition.")
        if self.definition.get("slug") != self.pack_slug:
            raise ValidationError("Source pack snapshot slug must match its definition.")
        if self.definition.get("version") != self.pack_version:
            raise ValidationError("Source pack snapshot version must match its definition.")
        if self.definition.get("schema_version") != self.schema_version:
            raise ValidationError("Source pack schema version must match its definition.")

    def __str__(self) -> str:
        return f"{self.pack_slug} v{self.pack_version} ({self.checksum[:12]}…)"
