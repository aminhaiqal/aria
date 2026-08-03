from django.contrib import admin

from aria.artifacts.models import ArtifactDerivative, ArtifactObservation, RawArtifact


class ReadOnlyArtifactAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(RawArtifact)
class RawArtifactAdmin(ReadOnlyArtifactAdmin):
    list_display = (
        "sha256",
        "byte_size",
        "detected_content_type",
        "storage_backend",
        "created_at",
    )
    list_filter = ("storage_backend", "detected_content_type")
    search_fields = ("sha256", "storage_key")
    date_hierarchy = "created_at"


@admin.register(ArtifactObservation)
class ArtifactObservationAdmin(ReadOnlyArtifactAdmin):
    list_display = (
        "raw_artifact",
        "candidate",
        "response_status",
        "final_url",
        "retrieved_at",
    )
    list_filter = ("response_status", "raw_artifact__detected_content_type")
    search_fields = ("requested_url", "final_url", "raw_artifact__sha256")
    date_hierarchy = "retrieved_at"


@admin.register(ArtifactDerivative)
class ArtifactDerivativeAdmin(ReadOnlyArtifactAdmin):
    list_display = (
        "source_artifact",
        "derived_artifact",
        "transformation_type",
        "profile",
        "created_at",
    )
    list_filter = ("transformation_type", "profile")
    search_fields = (
        "source_artifact__sha256",
        "derived_artifact__sha256",
        "configuration_hash",
    )
    date_hierarchy = "created_at"
