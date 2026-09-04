from django.contrib import admin

from aria.impacts.models import (
    ApplicabilityTaxonomy,
    ApplicabilityTerm,
    ImpactEvidence,
    ImpactTarget,
    RegulatoryImpact,
)


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


@admin.register(ApplicabilityTaxonomy)
class ApplicabilityTaxonomyAdmin(ReadOnlyImpactAdmin):
    list_display = ("name", "version", "jurisdiction", "checksum", "applied_at")
    search_fields = ("name", "slug", "checksum")


@admin.register(ApplicabilityTerm)
class ApplicabilityTermAdmin(ReadOnlyImpactAdmin):
    list_display = ("label", "dimension", "code", "taxonomy", "parent")
    list_filter = ("dimension", "taxonomy")
    search_fields = ("label", "code", "description")


@admin.register(ImpactTarget)
class ImpactTargetAdmin(ReadOnlyImpactAdmin):
    list_display = ("impact", "term", "disposition", "origin", "created_at")
    list_filter = ("disposition", "origin", "term__dimension")
    search_fields = ("impact__title", "term__label", "rationale")
