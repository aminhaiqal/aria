from django.contrib import admin

from aria.impacts.models import ImpactEvidence, RegulatoryImpact


class ReadOnlyImpactAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RegulatoryImpact)
class RegulatoryImpactAdmin(ReadOnlyImpactAdmin):
    list_display = (
        "title",
        "impact_type",
        "origin",
        "comparison_item",
        "confirmation_review",
        "created_at",
    )
    list_filter = ("impact_type", "origin", "comparison_item__comparison__identity__collection")
    search_fields = (
        "title",
        "statement",
        "input_fingerprint",
        "comparison_item__comparison__identity__canonical_title",
    )


@admin.register(ImpactEvidence)
class ImpactEvidenceAdmin(ReadOnlyImpactAdmin):
    list_display = ("impact", "side", "structural_anchor", "source_artifact", "created_at")
    list_filter = ("side", "impact__impact_type")
    search_fields = ("impact__title", "artifact_sha256", "anchor_text_sha256")
