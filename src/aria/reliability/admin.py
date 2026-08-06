from django.contrib import admin

from aria.reliability.models import SourceReliabilityAssessment


@admin.register(SourceReliabilityAssessment)
class SourceReliabilityAssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "endpoint",
        "status",
        "candidate_count",
        "artifact_ready_count",
        "graph_ready_count",
        "configured_embedding_count",
        "assessed_at",
    )
    list_filter = ("status", "configured_embedding_provider", "browser_capture_ready")
    search_fields = ("endpoint__name", "candidate_set_sha256", "assessment_signature")
    date_hierarchy = "assessed_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
