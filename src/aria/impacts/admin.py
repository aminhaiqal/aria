from django.contrib import admin

from aria.impacts.models import (
    ApplicabilityTaxonomy,
    ApplicabilityTerm,
    BusinessProfile,
    BusinessProfileTerm,
    ImpactEvidence,
    ImpactGeneration,
    ImpactReview,
    ImpactReviewTarget,
    ImpactTarget,
    ProfileImpactMatch,
    RegulatoryImpact,
    ReviewedImpactPublication,
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


@admin.register(ImpactGeneration)
class ImpactGenerationAdmin(ReadOnlyImpactAdmin):
    list_display = (
        "comparison_item",
        "provider",
        "model",
        "status",
        "taxonomy",
        "created_at",
    )
    list_filter = ("provider", "status", "taxonomy")
    search_fields = ("comparison_item__fingerprint", "input_hash", "response_id")


@admin.register(ImpactReview)
class ImpactReviewAdmin(ReadOnlyImpactAdmin):
    list_display = ("impact", "decision", "reviewer", "previous_review", "created_at")
    list_filter = ("decision", "impact__impact_type")
    search_fields = ("impact__title", "reviewed_statement", "rationale", "reviewer__username")


@admin.register(ImpactReviewTarget)
class ImpactReviewTargetAdmin(ReadOnlyImpactAdmin):
    list_display = ("impact_review", "term", "disposition", "source_target", "created_at")
    list_filter = ("disposition", "term__dimension")
    search_fields = ("impact_review__impact__title", "term__label", "rationale")


@admin.register(BusinessProfile)
class BusinessProfileAdmin(ReadOnlyImpactAdmin):
    list_display = ("name", "owner", "taxonomy", "is_active", "updated_at")
    list_filter = ("is_active", "taxonomy")
    search_fields = ("name", "owner__username", "notes")


@admin.register(BusinessProfileTerm)
class BusinessProfileTermAdmin(ReadOnlyImpactAdmin):
    list_display = ("profile", "term", "created_at")
    list_filter = ("term__dimension", "profile__taxonomy")
    search_fields = ("profile__name", "term__label")


@admin.register(ProfileImpactMatch)
class ProfileImpactMatchAdmin(ReadOnlyImpactAdmin):
    list_display = ("profile", "impact_review", "outcome", "ruleset", "created_at")
    list_filter = ("outcome", "ruleset", "profile__taxonomy")
    search_fields = (
        "profile__name",
        "impact_review__impact__title",
        "input_fingerprint",
        "explanation",
    )


@admin.register(ReviewedImpactPublication)
class ReviewedImpactPublicationAdmin(ReadOnlyImpactAdmin):
    list_display = ("impact_review", "pipeline_event", "published_by", "created_at")
    search_fields = (
        "impact_review__impact__title",
        "pipeline_event__event_type",
        "published_by__username",
    )
