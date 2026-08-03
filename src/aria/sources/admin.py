from django.contrib import admin, messages

from aria.sources.models import ConnectorConfiguration, SourceEndpoint


class ConnectorConfigurationInline(admin.TabularInline):
    model = ConnectorConfiguration
    extra = 0
    fields = ("version", "configuration", "notes", "is_active", "created_at")
    readonly_fields = ("created_at",)


@admin.register(SourceEndpoint)
class SourceEndpointAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "collection",
        "connector_type",
        "health_state",
        "next_poll_at",
        "is_enabled",
    )
    list_filter = ("connector_type", "health_state", "requires_javascript", "is_enabled")
    search_fields = ("name", "discovery_url", "collection__name", "collection__authority__name")
    autocomplete_fields = ("collection",)
    inlines = (ConnectorConfigurationInline,)
    actions = ("queue_manual_run",)

    @admin.action(description="Queue a manual source run")
    def queue_manual_run(self, request, queryset) -> None:
        from aria.discovery.services import audit_manual_run, create_source_run
        from aria.discovery.tasks import execute_source_run

        count = 0
        for endpoint in queryset.filter(is_enabled=True):
            source_run, created = create_source_run(endpoint, trigger="manual")
            if created:
                audit_manual_run(source_run, request.user.get_username())
                execute_source_run.delay(str(source_run.id))
                count += 1
        self.message_user(request, f"Queued {count} source run(s).", messages.SUCCESS)


@admin.register(ConnectorConfiguration)
class ConnectorConfigurationAdmin(admin.ModelAdmin):
    list_display = ("endpoint", "version", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("endpoint__name",)
    autocomplete_fields = ("endpoint",)
