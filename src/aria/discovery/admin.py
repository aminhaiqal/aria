from django.contrib import admin

from aria.discovery.models import (
    CandidateObservation,
    DiscoveredCandidate,
    EndpointObservation,
    SourceRun,
)


@admin.register(SourceRun)
class SourceRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "endpoint",
        "trigger",
        "status",
        "discovered_candidate_count",
        "created_at",
    )
    list_filter = ("trigger", "status", "endpoint__connector_type")
    search_fields = ("id", "endpoint__name", "idempotency_key", "error_code")
    autocomplete_fields = ("endpoint",)
    readonly_fields = (
        "id",
        "idempotency_key",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "discovered_candidate_count",
        "error_code",
        "error_message",
    )


@admin.register(DiscoveredCandidate)
class DiscoveredCandidateAdmin(admin.ModelAdmin):
    list_display = (
        "discovered_url",
        "endpoint",
        "pipeline_state",
        "first_discovered_at",
        "last_discovered_at",
    )
    list_filter = ("pipeline_state", "endpoint__connector_type")
    search_fields = ("discovered_url", "canonical_url", "external_identifier", "fingerprint")
    autocomplete_fields = ("endpoint", "latest_source_run")
    readonly_fields = ("id", "created_at", "updated_at", "first_discovered_at")


@admin.register(CandidateObservation)
class CandidateObservationAdmin(admin.ModelAdmin):
    list_display = ("candidate", "source_run", "created_at")
    search_fields = ("candidate__discovered_url", "source_run__id")
    autocomplete_fields = ("candidate", "source_run")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(EndpointObservation)
class EndpointObservationAdmin(admin.ModelAdmin):
    list_display = (
        "endpoint",
        "outcome",
        "response_status",
        "byte_size",
        "checked_at",
    )
    list_filter = ("outcome", "response_status", "endpoint__connector_type")
    search_fields = ("endpoint__name", "requested_url", "final_url", "content_sha256")
    readonly_fields = tuple(field.name for field in EndpointObservation._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
