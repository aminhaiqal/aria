from django.contrib import admin

from aria.discovery.models import CandidateObservation, DiscoveredCandidate, SourceRun


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
