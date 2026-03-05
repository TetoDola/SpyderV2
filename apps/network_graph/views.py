import json
import re

import frontmatter
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from apps.network_graph.models import Connection, Node, NodeTemplate
from apps.network_graph.parser import process_auto_links

# Regex to translate Obsidian [[Link]] to @[Link] (bracket-delimited)
# Negative lookbehind skips image embeds ![[image.jpg]]
OBSIDIAN_LINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")


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


@csrf_exempt
@require_http_methods(["POST"])
def api_import_nodes(request: HttpRequest) -> JsonResponse:
    """Import a batch of Markdown files as nodes.

    Translates Obsidian [[Link]] syntax to @Link for auto-linking.
    Extracts YAML frontmatter into properties. Deduplicates by title.
    """
    files = request.FILES.getlist("files")
    if not files:
        return JsonResponse({"error": "No files provided"}, status=400)

    created_count = 0
    updated_count = 0
    processed_nodes: list[Node] = []

    for f in files:
        raw_content = f.read().decode("utf-8", errors="replace")

        # Parse frontmatter
        post = frontmatter.loads(raw_content)
        metadata: dict[str, object] = dict(post.metadata) if post.metadata else {}
        body: str = post.content

        # Translate [[Link]] -> @[Link]  (bracket-delimited for special chars)
        body = OBSIDIAN_LINK_RE.sub(r"@[\1]", body)

        # Remove image embeds  ![[file.jpg]] -> empty
        body = re.sub(r"!\[\[([^\]]+)\]\]", "", body)

        # Title: from frontmatter 'title' key, or filename without .md
        title = str(metadata.pop("title", "")).strip()
        if not title:
            filename = f.name or ""
            # Strip path components (browser may send full relative path)
            filename = filename.replace("\\", "/").split("/")[-1]
            title = filename.removesuffix(".md").strip()
        if not title:
            continue

        # Node type from frontmatter — support common aliases
        _TYPE_ALIASES: dict[str, str] = {
            "PERSON": "PERSON", "PEOPLE": "PERSON", "CONTACT": "PERSON",
            "COMPANY": "COMPANY", "ORG": "COMPANY", "ORGANIZATION": "COMPANY",
            "ORGANISATION": "COMPANY", "BUSINESS": "COMPANY", "TEAM": "COMPANY",
            "MEETING": "MEETING", "EVENT": "MEETING", "CALL": "MEETING",
        }
        raw_type = str(metadata.pop("node_type", metadata.pop("type", "PERSON"))).upper()
        node_type = _TYPE_ALIASES.get(raw_type, "PERSON")

        # Remaining frontmatter becomes properties — skip None/empty, flatten lists
        def _coerce(v: object) -> str | None:
            if v is None:
                return None
            if isinstance(v, list):
                joined = ", ".join(str(i) for i in v if i is not None)
                return joined if joined else None
            s = str(v).strip()
            return s if s else None

        properties = {k: coerced for k, v in metadata.items() if (coerced := _coerce(v)) is not None}

        # Deduplication: match by title only (catches ghosts regardless of type)
        existing = Node.objects.filter(title__iexact=title).first()

        if existing:
            # Promote ghost → real node with correct type and content
            if existing.is_ghost:
                existing.is_ghost = False
                existing.node_type = node_type
                existing.notes = body
                existing.properties = properties
            else:
                # Append imported notes to existing real node
                separator = "\n\n---\n\n" if existing.notes.strip() else ""
                existing.notes = existing.notes + separator + body
                # Merge properties (don't overwrite existing keys)
                for key, val in properties.items():
                    if key not in existing.properties:
                        existing.properties[key] = val
            existing.save()
            processed_nodes.append(existing)
            updated_count += 1
        else:
            node = Node.objects.create(
                title=title,
                node_type=node_type,
                properties=properties,
                notes=body,
                is_ghost=False,
            )
            processed_nodes.append(node)
            created_count += 1

    # Run auto-linking for all processed nodes
    for node in processed_nodes:
        process_auto_links(node)

    return JsonResponse({
        "created": created_count,
        "updated": updated_count,
        "total": created_count + updated_count,
    })
