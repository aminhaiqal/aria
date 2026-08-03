from django.contrib import admin

from aria.extraction.models import ExtractedBlock, ExtractedDocument, ExtractionRun


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExtractionRun)
class ExtractionRunAdmin(ReadOnlyAdmin):
    list_display = (
        "raw_artifact",
        "extractor_name",
        "extractor_version",
        "status",
        "created_at",
        "finished_at",
    )
    list_filter = ("status", "extractor_name", "extractor_version")
    search_fields = ("raw_artifact__sha256", "error_code", "error_message")


@admin.register(ExtractedDocument)
class ExtractedDocumentAdmin(ReadOnlyAdmin):
    list_display = ("title", "raw_artifact", "page_count", "requires_ocr", "created_at")
    list_filter = ("requires_ocr", "language_hint")
    search_fields = ("title", "plain_text", "raw_artifact__sha256")


@admin.register(ExtractedBlock)
class ExtractedBlockAdmin(ReadOnlyAdmin):
    list_display = ("extracted_document", "ordinal", "block_type", "page_number")
    list_filter = ("block_type",)
    search_fields = ("heading", "text")
