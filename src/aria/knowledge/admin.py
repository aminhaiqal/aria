from django.contrib import admin

from aria.knowledge.models import GraphEdge, GraphNode, SectionEmbedding


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GraphNode)
class GraphNodeAdmin(ReadOnlyAdmin):
    list_display = ("node_type", "label", "source_type", "source_id")
    list_filter = ("node_type", "source_type")
    search_fields = ("label", "canonical_key")


@admin.register(GraphEdge)
class GraphEdgeAdmin(ReadOnlyAdmin):
    list_display = ("subject", "predicate", "object", "source_type", "created_at")
    list_filter = ("predicate", "source_type")
    search_fields = ("subject__label", "object__label", "fingerprint")


@admin.register(SectionEmbedding)
class SectionEmbeddingAdmin(ReadOnlyAdmin):
    list_display = ("normalized_section", "provider", "model", "dimensions", "created_at")
    list_filter = ("provider", "model", "dimensions")
