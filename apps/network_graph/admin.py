from django.contrib import admin

from apps.network_graph.models import (
    Connection,
    Ingestion,
    Node,
    NodeTemplate,
    ResolutionCandidate,
)


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("title", "node_type", "is_ghost", "created_at")
    list_filter = ("node_type", "is_ghost")
    search_fields = ("title",)


@admin.register(NodeTemplate)
class NodeTemplateAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("node_type", "updated_at")


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("source", "target", "relationship_label", "created_at")
    list_filter = ("relationship_label",)


@admin.register(Ingestion)
class IngestionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "source_type", "status", "created_at", "completed_at")
    list_filter = ("source_type", "status")
    readonly_fields = ("extracted_json", "dsl_commands")


@admin.register(ResolutionCandidate)
class ResolutionCandidateAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("extracted_name", "confidence", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("extracted_name",)
