from django.contrib import admin

from aria.comparisons.models import (
    ComparisonItem,
    ComparisonReview,
    ComparisonSummary,
    DocumentComparison,
    StructuralAnchor,
    VersionLineageAssessment,
)
from aria.events.services import record_audit_event


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VersionLineageAssessment)
class VersionLineageAssessmentAdmin(ReadOnlyAdmin):
    list_display = (
        "document_version",
        "representation_kind",
        "provenance_status",
        "source_artifact",
        "created_at",
    )
    list_filter = ("representation_kind", "provenance_status", "ruleset")
    search_fields = ("document_version__title", "comparison_track_key", "source_artifact__sha256")


@admin.register(StructuralAnchor)
class StructuralAnchorAdmin(ReadOnlyAdmin):
    list_display = ("canonical_key", "anchor_type", "document_version", "ordinal", "created_at")
    list_filter = ("anchor_type", "ruleset")
    search_fields = ("canonical_key", "label", "text", "source_artifact__sha256")


@admin.register(DocumentComparison)
class DocumentComparisonAdmin(ReadOnlyAdmin):
    list_display = (
        "identity",
        "status",
        "before_version",
        "after_version",
        "modified_count",
        "added_count",
        "removed_count",
        "created_at",
    )
    list_filter = ("status", "ruleset", "identity__collection")
    search_fields = ("identity__canonical_title", "comparison_track_key", "input_fingerprint")


@admin.register(ComparisonItem)
class ComparisonItemAdmin(ReadOnlyAdmin):
    list_display = (
        "comparison",
        "change_type",
        "match_strategy",
        "similarity_score",
        "created_at",
    )
    list_filter = ("change_type", "match_strategy", "comparison__identity__collection")
    search_fields = ("fingerprint", "before_anchor__label", "after_anchor__label")


@admin.register(ComparisonReview)
class ComparisonReviewAdmin(admin.ModelAdmin):
    list_display = ("comparison_item", "decision", "reviewer", "previous_review", "created_at")
    list_filter = ("decision", "comparison_item__comparison__identity__collection")
    search_fields = ("rationale", "comparison_item__fingerprint", "reviewer__username")
    exclude = ("reviewer", "previous_review")
    readonly_fields = ("created_at",)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.reviewer = request.user
        obj.previous_review = obj.comparison_item.reviews.order_by("-created_at", "-id").first()
        super().save_model(request, obj, form, change)
        record_audit_event(
            action="comparison.review_recorded",
            target_type="comparison_item",
            target_id=obj.comparison_item_id,
            actor_type="user",
            actor_identifier=str(request.user.pk),
            details={
                "review_id": str(obj.id),
                "decision": obj.decision,
                "previous_review_id": str(obj.previous_review_id)
                if obj.previous_review_id
                else None,
            },
        )


@admin.register(ComparisonSummary)
class ComparisonSummaryAdmin(ReadOnlyAdmin):
    list_display = (
        "comparison",
        "provider",
        "model",
        "status",
        "input_tokens",
        "output_tokens",
        "created_at",
    )
    list_filter = ("status", "provider", "model", "prompt_version")
    search_fields = ("comparison__identity__canonical_title", "input_hash", "response_id")
