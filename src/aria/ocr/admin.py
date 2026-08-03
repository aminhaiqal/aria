from django.contrib import admin

from aria.ocr.models import OCRRun


@admin.register(OCRRun)
class OCRRunAdmin(admin.ModelAdmin):
    list_display = (
        "source_artifact",
        "profile_name",
        "profile_version",
        "status",
        "page_count",
        "non_whitespace_characters",
        "created_at",
    )
    list_filter = ("status", "profile_name", "profile_version")
    search_fields = (
        "source_artifact__sha256",
        "configuration_hash",
        "error_code",
        "error_message",
    )
    readonly_fields = (
        "source_artifact",
        "profile_name",
        "profile_version",
        "configuration",
        "configuration_hash",
        "status",
        "searchable_pdf_derivative",
        "text_sidecar_derivative",
        "toolchain",
        "page_count",
        "non_whitespace_characters",
        "started_at",
        "finished_at",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
