from django.contrib import admin

from aria.browser.models import (
    BrowserCapture,
    BrowserNetworkExchange,
    SourceAdmissionAssessment,
    SourceAdmissionPromotion,
)


class ReadOnlyBrowserAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(BrowserCapture)
class BrowserCaptureAdmin(ReadOnlyBrowserAdmin):
    list_display = (
        "source_run",
        "endpoint",
        "status",
        "attempt_count",
        "request_count",
        "response_bytes",
        "created_at",
    )
    list_filter = ("status", "profile", "endpoint__connector_type")
    search_fields = (
        "requested_url",
        "final_url",
        "source_run__id",
        "original_artifact__sha256",
        "rendered_artifact__sha256",
        "error_code",
    )
    date_hierarchy = "created_at"


@admin.register(BrowserNetworkExchange)
class BrowserNetworkExchangeAdmin(ReadOnlyBrowserAdmin):
    list_display = (
        "capture",
        "attempt_number",
        "sequence",
        "method",
        "resource_type",
        "disposition",
        "response_status",
        "request_body_bytes",
        "byte_size",
    )
    list_filter = ("disposition", "resource_type", "method", "response_status")
    search_fields = ("requested_url", "body_sha256", "block_reason")
    date_hierarchy = "occurred_at"


@admin.register(SourceAdmissionAssessment)
class SourceAdmissionAssessmentAdmin(ReadOnlyBrowserAdmin):
    list_display = (
        "endpoint",
        "status",
        "candidate_count",
        "required_captures",
        "assessed_at",
    )
    list_filter = ("status", "required_captures")
    search_fields = ("endpoint__name", "report_signature", "candidate_set_sha256")
    date_hierarchy = "assessed_at"


@admin.register(SourceAdmissionPromotion)
class SourceAdmissionPromotionAdmin(ReadOnlyBrowserAdmin):
    list_display = ("endpoint", "assessment", "next_poll_at", "promoted_at")
    search_fields = ("endpoint__name", "assessment__report_signature")
    date_hierarchy = "promoted_at"
