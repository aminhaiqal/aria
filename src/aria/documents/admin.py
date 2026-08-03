from django.contrib import admin

from aria.documents.models import (
    DocumentIdentity,
    DocumentVersion,
    NormalizedSection,
    VersionEvidence,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentIdentity)
class DocumentIdentityAdmin(ReadOnlyAdmin):
    list_display = ("canonical_title", "collection", "canonical_url", "created_at")
    list_filter = ("collection__authority", "collection", "is_manual_override")
    search_fields = ("canonical_title", "canonical_url", "stable_key")


@admin.register(DocumentVersion)
class DocumentVersionAdmin(ReadOnlyAdmin):
    list_display = ("identity", "normalized_content_sha256", "created_at")
    list_filter = ("extractor_name", "extractor_version", "language_hint")
    search_fields = ("title", "plain_content", "normalized_content_sha256")


@admin.register(VersionEvidence)
class VersionEvidenceAdmin(ReadOnlyAdmin):
    list_display = ("document_version", "raw_artifact", "observed_url", "created_at")
    search_fields = ("raw_artifact__sha256", "observed_url")


@admin.register(NormalizedSection)
class NormalizedSectionAdmin(ReadOnlyAdmin):
    list_display = ("document_version", "ordinal", "section_type", "page_number")
    list_filter = ("section_type",)
    search_fields = ("heading", "text", "text_sha256")
