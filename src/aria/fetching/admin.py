from django.contrib import admin

from aria.fetching.models import FetchAttempt


@admin.register(FetchAttempt)
class FetchAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "candidate",
        "attempt_number",
        "status",
        "response_status",
        "bytes_received",
        "started_at",
    )
    list_filter = ("status", "response_status", "candidate__endpoint__connector_type")
    search_fields = ("requested_url", "final_url", "candidate__fingerprint", "error_code")
    readonly_fields = (
        "id",
        "candidate",
        "source_run",
        "attempt_number",
        "status",
        "requested_url",
        "final_url",
        "request_headers",
        "response_status",
        "response_headers",
        "redirect_chain",
        "resolved_addresses",
        "bytes_received",
        "started_at",
        "finished_at",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
