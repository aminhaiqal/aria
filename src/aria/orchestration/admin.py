from django.contrib import admin

from aria.orchestration.models import ChangeOrchestration, OrchestrationStepAttempt


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ChangeOrchestration)
class ChangeOrchestrationAdmin(ReadOnlyAdmin):
    list_display = (
        "artifact_observation",
        "status",
        "current_stage",
        "document_version",
        "comparison",
        "retry_count",
        "updated_at",
    )
    list_filter = ("status", "current_stage", "source_artifact__storage_backend")
    search_fields = (
        "idempotency_key",
        "source_artifact__sha256",
        "artifact_observation__final_url",
        "error_code",
    )
    raw_id_fields = (
        "artifact_observation",
        "source_artifact",
        "extraction_run",
        "document_version",
        "quality_run",
        "lineage_assessment",
        "comparison",
        "summary",
    )


@admin.register(OrchestrationStepAttempt)
class OrchestrationStepAttemptAdmin(ReadOnlyAdmin):
    list_display = (
        "orchestration",
        "stage",
        "attempt_number",
        "outcome",
        "started_at",
        "finished_at",
    )
    list_filter = ("stage", "outcome")
    search_fields = ("orchestration__idempotency_key", "input_hash", "error_code")
    raw_id_fields = ("orchestration",)
