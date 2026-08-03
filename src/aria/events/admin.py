from django.contrib import admin

from aria.events.models import AuditEvent, OutboxEvent, PipelineEvent


class ReadOnlyEventAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(PipelineEvent)
class PipelineEventAdmin(ReadOnlyEventAdmin):
    list_display = ("event_type", "aggregate_type", "aggregate_id", "occurred_at")
    list_filter = ("event_type", "aggregate_type")
    search_fields = ("aggregate_id",)
    date_hierarchy = "occurred_at"


@admin.register(OutboxEvent)
class OutboxEventAdmin(ReadOnlyEventAdmin):
    list_display = ("topic", "status", "attempts", "available_at", "published_at")
    list_filter = ("status", "topic")
    search_fields = ("pipeline_event__aggregate_id",)


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyEventAdmin):
    list_display = ("action", "actor_type", "target_type", "target_id", "occurred_at")
    list_filter = ("action", "actor_type", "target_type")
    search_fields = ("target_id", "actor_identifier")
    date_hierarchy = "occurred_at"
