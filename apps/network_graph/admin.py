from django.contrib import admin

from apps.network_graph.models import Connection, Node, NodeTemplate


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("title", "node_type", "created_at")
    list_filter = ("node_type",)
    search_fields = ("title",)


@admin.register(NodeTemplate)
class NodeTemplateAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("node_type", "updated_at")


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("source", "target", "relationship_label", "created_at")
    list_filter = ("relationship_label",)
