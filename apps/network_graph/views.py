import json

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from apps.network_graph.models import Connection, Node, NodeTemplate
from apps.network_graph.parser import process_auto_links


@require_GET
def index(request: HttpRequest) -> HttpResponse:
    """Serve the main graph canvas page."""
    return render(request, "network_graph/index.html")


@require_GET
def api_graph(request: HttpRequest) -> JsonResponse:
    """Return full graph data for vis-network rendering."""
    nodes = Node.objects.all()
    edges = Connection.objects.select_related("source", "target").all()

    node_data = [
        {
            "id": str(n.id),
            "label": n.title,
            "group": n.node_type,
            "is_ghost": n.is_ghost,
            "image": n.profile_image.url if n.profile_image else None,
            "created_at": n.created_at.isoformat(),
        }
        for n in nodes
    ]

    edge_data = [
        {
            "from": str(e.source_id),
            "to": str(e.target_id),
            "label": e.relationship_label,
        }
        for e in edges
    ]

    return JsonResponse({"nodes": node_data, "edges": edge_data})


@require_GET
def api_node_detail(request: HttpRequest, node_id: str) -> JsonResponse:
    """Return full details for a single node."""
    node = get_object_or_404(Node, pk=node_id)
    return JsonResponse({
        "id": str(node.id),
        "title": node.title,
        "node_type": node.node_type,
        "properties": node.properties,
        "notes": node.notes,
        "is_ghost": node.is_ghost,
        "profile_image": node.profile_image.url if node.profile_image else None,
        "created_at": node.created_at.isoformat(),
    })


@csrf_exempt
@require_http_methods(["PUT"])
def api_node_update(request: HttpRequest, node_id: str) -> JsonResponse:
    """Update a node's properties/notes, promote ghosts, and sync connections."""
    node = get_object_or_404(Node, pk=node_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if "title" in data:
        node.title = data["title"]
    if "node_type" in data:
        if data["node_type"] in ("PERSON", "COMPANY", "MEETING"):
            node.node_type = data["node_type"]
    if "properties" in data:
        if not isinstance(data["properties"], dict):
            return JsonResponse({"error": "properties must be an object"}, status=400)
        node.properties = data["properties"]
    if "notes" in data:
        node.notes = data["notes"]

    # Ghost promotion: if user adds real content, promote to full node
    has_content = bool(node.notes.strip()) or bool(node.properties)
    if node.is_ghost and has_content:
        node.is_ghost = False

    node.save()

    # Diff-based auto-linking: creates new edges, deletes stale ones
    process_auto_links(node)

    return JsonResponse({
        "id": str(node.id),
        "title": node.title,
        "node_type": node.node_type,
        "properties": node.properties,
        "notes": node.notes,
        "is_ghost": node.is_ghost,
        "profile_image": node.profile_image.url if node.profile_image else None,
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_node_create(request: HttpRequest) -> JsonResponse:
    """Create a new node from the FAB modal."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    title = data.get("title", "").strip()
    if not title:
        return JsonResponse({"error": "title is required"}, status=400)

    node_type = data.get("node_type", "PERSON")
    if node_type not in ("PERSON", "COMPANY", "MEETING"):
        return JsonResponse({"error": "Invalid node_type"}, status=400)

    node = Node.objects.create(
        title=title,
        node_type=node_type,
        properties=data.get("properties", {}),
        notes=data.get("notes", ""),
        is_ghost=False,
    )

    process_auto_links(node)

    return JsonResponse(
        {
            "id": str(node.id),
            "title": node.title,
            "node_type": node.node_type,
            "is_ghost": node.is_ghost,
        },
        status=201,
    )


@csrf_exempt
@require_http_methods(["DELETE"])
def api_node_delete(request: HttpRequest, node_id: str) -> JsonResponse:
    """Delete a node and all its connections."""
    node = get_object_or_404(Node, pk=node_id)
    node.delete()
    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["POST"])
def api_node_image(request: HttpRequest, node_id: str) -> JsonResponse:
    """Upload a profile image for a node."""
    node = get_object_or_404(Node, pk=node_id)

    if "image" not in request.FILES:
        return JsonResponse({"error": "No image file provided"}, status=400)

    image_file = request.FILES["image"]

    # Delete old image if it exists
    if node.profile_image:
        node.profile_image.delete(save=False)

    node.profile_image = image_file
    node.save(update_fields=["profile_image"])

    return JsonResponse({
        "profile_image": node.profile_image.url,
    })


@require_GET
def api_node_search(request: HttpRequest) -> JsonResponse:
    """Search nodes by title prefix for @ autocomplete."""
    q = request.GET.get("q", "").strip()

    if q:
        nodes = Node.objects.filter(title__icontains=q)[:10]
    else:
        nodes = Node.objects.all()[:10]
    return JsonResponse({
        "results": [
            {"id": str(n.id), "title": n.title, "node_type": n.node_type}
            for n in nodes
        ]
    })


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def api_templates(request: HttpRequest) -> JsonResponse:
    """GET: return all templates. PUT: upsert a template for a node_type."""
    if request.method == "GET":
        templates = NodeTemplate.objects.all()
        result: dict[str, dict[str, object]] = {}
        for t in templates:
            result[t.node_type] = {
                "default_properties": t.default_properties,
                "default_notes": t.default_notes,
            }
        return JsonResponse(result)

    # PUT
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    node_type = data.get("node_type", "")
    if node_type not in ("PERSON", "COMPANY", "MEETING"):
        return JsonResponse({"error": "Invalid node_type"}, status=400)

    default_properties = data.get("default_properties", {})
    if not isinstance(default_properties, dict):
        return JsonResponse({"error": "default_properties must be an object"}, status=400)

    default_notes = data.get("default_notes", "")

    template, _ = NodeTemplate.objects.update_or_create(
        node_type=node_type,
        defaults={
            "default_properties": default_properties,
            "default_notes": default_notes,
        },
    )

    return JsonResponse({
        "node_type": template.node_type,
        "default_properties": template.default_properties,
        "default_notes": template.default_notes,
    })
