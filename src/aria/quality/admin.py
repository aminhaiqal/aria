from django.contrib import admin

from aria.quality.models import (
    DocumentQualityAssessment,
    QualityAssessmentRun,
    QualityFinding,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QualityAssessmentRun)
class QualityAssessmentRunAdmin(ReadOnlyAdmin):
    list_display = (
        "collection",
        "ruleset",
        "status",
        "document_count",
        "passed_count",
        "warning_count",
        "review_required_count",
        "created_at",
    )
    list_filter = ("status", "ruleset", "collection__authority")
    search_fields = ("collection__name", "configuration_hash", "corpus_fingerprint")


@admin.register(DocumentQualityAssessment)
class DocumentQualityAssessmentAdmin(ReadOnlyAdmin):
    list_display = (
        "document_version",
        "outcome",
        "score",
        "source_artifact",
        "created_at",
    )
    list_filter = ("outcome", "quality_run__collection")
    search_fields = ("document_version__title", "source_artifact__sha256")


@admin.register(QualityFinding)
class QualityFindingAdmin(ReadOnlyAdmin):
    list_display = ("code", "severity", "document_version", "source_artifact", "created_at")
    list_filter = ("severity", "code", "assessment__quality_run__collection")
    search_fields = ("message", "document_version__title", "source_artifact__sha256")
