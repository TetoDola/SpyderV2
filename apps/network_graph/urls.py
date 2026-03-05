from django.urls import path

from apps.network_graph import views

app_name = "network_graph"

urlpatterns = [
    # Page
    path("", views.index, name="index"),
    # API
    path("api/graph/", views.api_graph, name="api-graph"),
    path("api/nodes/", views.api_node_create, name="api-node-create"),
    path("api/nodes/search/", views.api_node_search, name="api-node-search"),
    path("api/nodes/<str:node_id>/", views.api_node_detail, name="api-node-detail"),
    path("api/nodes/<str:node_id>/update/", views.api_node_update, name="api-node-update"),
    path("api/nodes/<str:node_id>/delete/", views.api_node_delete, name="api-node-delete"),
    path("api/nodes/<str:node_id>/image/", views.api_node_image, name="api-node-image"),
    path("api/templates/", views.api_templates, name="api-templates"),
    path("api/import/", views.api_import_nodes, name="api-import"),
]
