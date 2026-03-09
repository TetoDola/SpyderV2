from django.urls import path

from apps.network_graph import views

app_name = "network_graph"

urlpatterns = [
    # Page
    path("", views.index, name="index"),
    # Graph API
    path("api/graph/", views.api_graph, name="api-graph"),
    # Node CRUD
    path("api/nodes/", views.api_node_create, name="api-node-create"),
    path("api/nodes/search/", views.api_node_search, name="api-node-search"),
    path("api/nodes/<str:node_id>/", views.api_node_detail, name="api-node-detail"),
    path("api/nodes/<str:node_id>/update/", views.api_node_update, name="api-node-update"),
    path("api/nodes/<str:node_id>/delete/", views.api_node_delete, name="api-node-delete"),
    path("api/nodes/<str:node_id>/image/", views.api_node_image, name="api-node-image"),
    # Templates
    path("api/templates/", views.api_templates, name="api-templates"),
    # Import (legacy markdown batch)
    path("api/import/", views.api_import_nodes, name="api-import"),
    # Pipeline ingestion (one per source type)
    path("api/ingest/voice/", views.api_ingest_voice, name="api-ingest-voice"),
    path("api/ingest/document/", views.api_ingest_document, name="api-ingest-document"),
    path("api/ingest/note/", views.api_ingest_note, name="api-ingest-note"),
    path("api/ingest/meeting/", views.api_ingest_meeting, name="api-ingest-meeting"),
    # Ingestion list & status
    path("api/ingestions/", views.api_ingestions_list, name="api-ingestions-list"),
    path(
        "api/ingestions/<str:ingestion_id>/status/",
        views.api_ingestion_status,
        name="api-ingestion-status",
    ),
    path(
        "api/ingestions/<str:ingestion_id>/review/",
        views.api_ingestion_review,
        name="api-ingestion-review",
    ),
    path(
        "api/ingestions/<str:ingestion_id>/retry/",
        views.api_ingestion_retry,
        name="api-ingestion-retry",
    ),
    path(
        "api/ingestions/<str:ingestion_id>/dismiss/",
        views.api_ingestion_dismiss,
        name="api-ingestion-dismiss",
    ),
    path(
        "api/ingestions/<str:ingestion_id>/delete/",
        views.api_ingestion_delete,
        name="api-ingestion-delete",
    ),
    # Resolution queue
    path(
        "api/resolution-queue/",
        views.api_resolution_queue,
        name="api-resolution-queue",
    ),
    path(
        "api/resolution-queue/<str:candidate_id>/resolve/",
        views.api_resolution_resolve,
        name="api-resolution-resolve",
    ),
]
