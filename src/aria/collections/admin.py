from django.contrib import admin

from aria.collections.models import PublicationCollection


@admin.register(PublicationCollection)
class PublicationCollectionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "authority",
        "document_family",
        "priority",
        "is_evidence_eligible",
        "is_enabled",
    )
    list_filter = ("document_family", "priority", "is_evidence_eligible", "is_enabled")
    search_fields = ("name", "authority__name")
    autocomplete_fields = ("authority",)
    prepopulated_fields = {"slug": ("name",)}
