import json
import re

import frontmatter
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from apps.network_graph.models import (
    Connection,
    Ingestion,
    IngestionSourceType,
    IngestionStatus,
    Node,
    NodeTemplate,
    ResolutionCandidate,
    ResolutionStatus,
)
from apps.network_graph.parser import process_auto_links

# Regex to translate Obsidian [[Link]] to @[Link] (bracket-delimited)
# Negative lookbehind skips image embeds ![[image.jpg]]
OBSIDIAN_LINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".webm"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


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
    return JsonResponse(
        {
            "id": str(node.id),
            "title": node.title,
            "node_type": node.node_type,
            "properties": node.properties,
            "notes": node.notes,
            "summary": node.summary,
            "is_ghost": node.is_ghost,
            "profile_image": node.profile_image.url if node.profile_image else None,
            "created_at": node.created_at.isoformat(),
        }
    )


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
    if "node_type" in data and data["node_type"] in ("PERSON", "COMPANY", "MEETING"):
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

    return JsonResponse(
        {
            "id": str(node.id),
            "title": node.title,
            "node_type": node.node_type,
            "properties": node.properties,
            "notes": node.notes,
            "summary": node.summary,
            "is_ghost": node.is_ghost,
            "profile_image": node.profile_image.url if node.profile_image else None,
        }
    )


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

    return JsonResponse(
        {
            "profile_image": node.profile_image.url,
        }
    )


@require_GET
def api_node_search(request: HttpRequest) -> JsonResponse:
    """Search nodes by title prefix for @ autocomplete."""
    q = request.GET.get("q", "").strip()

    if q:
        nodes = Node.objects.filter(title__icontains=q)[:10]
    else:
        nodes = Node.objects.all()[:10]
    return JsonResponse(
        {"results": [{"id": str(n.id), "title": n.title, "node_type": n.node_type} for n in nodes]}
    )


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

    return JsonResponse(
        {
            "node_type": template.node_type,
            "default_properties": template.default_properties,
            "default_notes": template.default_notes,
        }
    )


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
        type_aliases: dict[str, str] = {
            "PERSON": "PERSON",
            "PEOPLE": "PERSON",
            "CONTACT": "PERSON",
            "COMPANY": "COMPANY",
            "ORG": "COMPANY",
            "ORGANIZATION": "COMPANY",
            "ORGANISATION": "COMPANY",
            "BUSINESS": "COMPANY",
            "TEAM": "COMPANY",
            "MEETING": "MEETING",
            "EVENT": "MEETING",
            "CALL": "MEETING",
        }
        raw_type = str(metadata.pop("node_type", metadata.pop("type", "PERSON"))).upper()
        node_type = type_aliases.get(raw_type, "PERSON")

        # Remaining frontmatter becomes properties — skip None/empty, flatten lists
        def _coerce(v: object) -> str | None:
            if v is None:
                return None
            if isinstance(v, list):
                joined = ", ".join(str(i) for i in v if i is not None)
                return joined if joined else None
            s = str(v).strip()
            return s if s else None

        properties = {
            k: coerced for k, v in metadata.items() if (coerced := _coerce(v)) is not None
        }

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

    return JsonResponse(
        {
            "created": created_count,
            "updated": updated_count,
            "total": created_count + updated_count,
        }
    )


# ---------------------------------------------------------------------------
# Pipeline Ingestion Endpoints (one per source type)
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def api_ingest_voice(request: HttpRequest) -> JsonResponse:
    """Ingest a voice note. Accepts audio file upload."""
    if "file" not in request.FILES:
        return JsonResponse({"error": "No audio file provided"}, status=400)

    audio_file = request.FILES["file"]
    ext = "." + audio_file.name.rsplit(".", 1)[-1].lower() if "." in audio_file.name else ""
    if ext not in AUDIO_EXTENSIONS:
        return JsonResponse(
            {"error": f"Unsupported audio format: {', '.join(sorted(AUDIO_EXTENSIONS))}"},
            status=400,
        )

    ingestion = Ingestion.objects.create(
        source_type=IngestionSourceType.VOICE_NOTE,
        original_file=audio_file,
        status=IngestionStatus.PENDING,
    )

    from apps.network_graph.tasks import process_voice_note

    process_voice_note.delay(str(ingestion.pk))

    return JsonResponse(
        {"ingestion_id": str(ingestion.pk), "status": ingestion.status},
        status=202,
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_ingest_document(request: HttpRequest) -> JsonResponse:
    """Ingest a document. Accepts PDF, DOCX, TXT, or MD file upload."""
    if "file" not in request.FILES:
        return JsonResponse({"error": "No document file provided"}, status=400)

    doc_file = request.FILES["file"]
    ext = "." + doc_file.name.rsplit(".", 1)[-1].lower() if "." in doc_file.name else ""
    if ext not in DOCUMENT_EXTENSIONS:
        return JsonResponse(
            {"error": f"Unsupported document format: {', '.join(sorted(DOCUMENT_EXTENSIONS))}"},
            status=400,
        )

    ingestion = Ingestion.objects.create(
        source_type=IngestionSourceType.DOCUMENT,
        original_file=doc_file,
        status=IngestionStatus.PENDING,
    )

    from apps.network_graph.tasks import process_document

    process_document.delay(str(ingestion.pk))

    return JsonResponse(
        {"ingestion_id": str(ingestion.pk), "status": ingestion.status},
        status=202,
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_ingest_note(request: HttpRequest) -> JsonResponse:
    """Ingest a freeform note. Accepts JSON body with text field."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    text = data.get("text", "").strip()
    if not text:
        return JsonResponse({"error": "text is required"}, status=400)

    ingestion = Ingestion.objects.create(
        source_type=IngestionSourceType.FREEFORM_NOTE,
        raw_text=text,
        status=IngestionStatus.PENDING,
    )

    from apps.network_graph.tasks import process_freeform_note

    process_freeform_note.delay(str(ingestion.pk))

    return JsonResponse(
        {"ingestion_id": str(ingestion.pk), "status": ingestion.status},
        status=202,
    )


# ---------------------------------------------------------------------------
# Ingestion status & review
# ---------------------------------------------------------------------------


@require_GET
def api_ingestion_status(request: HttpRequest, ingestion_id: str) -> JsonResponse:
    """Return the current status of an ingestion."""
    ingestion = get_object_or_404(Ingestion, pk=ingestion_id)
    return JsonResponse(
        {
            "ingestion_id": str(ingestion.pk),
            "source_type": ingestion.source_type,
            "status": ingestion.status,
            "error_message": ingestion.error_message,
            "failed_step": ingestion.failed_step,
            "created_at": ingestion.created_at.isoformat(),
            "completed_at": ingestion.completed_at.isoformat() if ingestion.completed_at else None,
        }
    )


@require_GET
def api_ingestion_review(request: HttpRequest, ingestion_id: str) -> JsonResponse:
    """Return a review card for a completed ingestion."""
    ingestion = get_object_or_404(Ingestion, pk=ingestion_id)
    extracted = ingestion.extracted_json if isinstance(ingestion.extracted_json, dict) else {}

    pending_candidates = ResolutionCandidate.objects.filter(
        ingestion=ingestion,
        status=ResolutionStatus.PENDING,
    )

    return JsonResponse(
        {
            "ingestion_id": str(ingestion.pk),
            "source_type": ingestion.source_type,
            "status": ingestion.status,
            "extracted": {
                "people_count": len(extracted.get("people", [])),
                "companies_count": len(extracted.get("companies", [])),
                "relationships_count": len(extracted.get("relationships", [])),
            },
            "dsl_commands_count": (
                len(ingestion.dsl_commands) if isinstance(ingestion.dsl_commands, list) else 0
            ),
            "pending_resolutions": [
                {
                    "id": str(c.pk),
                    "name": c.extracted_name,
                    "confidence": c.confidence,
                }
                for c in pending_candidates
            ],
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_ingestion_retry(request: HttpRequest, ingestion_id: str) -> JsonResponse:
    """Retry a failed ingestion from its last failed step."""
    ingestion = get_object_or_404(Ingestion, pk=ingestion_id)

    if ingestion.status != IngestionStatus.FAILED:
        return JsonResponse({"error": "Ingestion is not in FAILED state"}, status=400)

    from apps.network_graph.tasks import (
        extract_entities,
        process_document,
        process_freeform_note,
        process_voice_note,
        resolve_entities,
        summarize,
        write_graph,
    )

    step = ingestion.failed_step
    ingestion.status = IngestionStatus.PENDING
    ingestion.error_message = ""
    ingestion.failed_step = ""
    ingestion.save(update_fields=["status", "error_message", "failed_step"])

    step_task_map: dict[str, object] = {
        "EXTRACTING": extract_entities,
        "RESOLVING": resolve_entities,
        "WRITING": write_graph,
        "SUMMARIZING": summarize,
    }

    task = step_task_map.get(step)
    if task and callable(task):
        task.delay(str(ingestion.pk))
    else:
        # Restart from beginning based on source type
        if ingestion.source_type == IngestionSourceType.VOICE_NOTE:
            process_voice_note.delay(str(ingestion.pk))
        elif ingestion.source_type == IngestionSourceType.DOCUMENT:
            process_document.delay(str(ingestion.pk))
        else:
            process_freeform_note.delay(str(ingestion.pk))

    return JsonResponse(
        {"ingestion_id": str(ingestion.pk), "status": ingestion.status, "retrying_from": step},
        status=202,
    )


# ---------------------------------------------------------------------------
# Resolution Queue
# ---------------------------------------------------------------------------


@require_GET
def api_resolution_queue(request: HttpRequest) -> JsonResponse:
    """List all pending resolution candidates."""
    candidates = ResolutionCandidate.objects.filter(
        status=ResolutionStatus.PENDING,
    ).select_related("candidate_node")

    return JsonResponse(
        {
            "results": [
                {
                    "id": str(c.pk),
                    "extracted_name": c.extracted_name,
                    "extracted_email": c.extracted_email,
                    "extracted_company": c.extracted_company,
                    "extracted_title": c.extracted_title,
                    "candidate_node": {
                        "id": str(c.candidate_node.pk),
                        "title": c.candidate_node.title,
                        "node_type": c.candidate_node.node_type,
                    }
                    if c.candidate_node
                    else None,
                    "confidence": c.confidence,
                }
                for c in candidates
            ]
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_resolution_resolve(request: HttpRequest, candidate_id: str) -> JsonResponse:
    """Resolve a candidate: confirm match, create new, or dismiss."""
    candidate = get_object_or_404(ResolutionCandidate, pk=candidate_id)

    if candidate.status != ResolutionStatus.PENDING:
        return JsonResponse({"error": "Candidate already resolved"}, status=400)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    action = data.get("action", "")

    if action == "confirm":
        from apps.network_graph.dsl import DSLContext, merge_nodes

        target_id = data.get("target_node_id", "")
        if not target_id:
            return JsonResponse({"error": "target_node_id required for confirm"}, status=400)

        ctx = DSLContext(ingestion_id=str(candidate.ingestion_id))

        if candidate.candidate_node and str(candidate.candidate_node.pk) != target_id:
            merge_nodes(ctx, source_id=str(candidate.candidate_node.pk), target_id=target_id)

        candidate.resolved_node_id = target_id
        candidate.status = ResolutionStatus.CONFIRMED
        candidate.save()

    elif action == "create_new":
        if candidate.candidate_node and candidate.candidate_node.is_ghost:
            candidate.candidate_node.is_ghost = False
            candidate.candidate_node.save()
        candidate.resolved_node = candidate.candidate_node
        candidate.status = ResolutionStatus.CONFIRMED
        candidate.save()

    elif action == "reject":
        candidate.status = ResolutionStatus.REJECTED
        candidate.save()

    else:
        return JsonResponse({"error": "action must be confirm, create_new, or reject"}, status=400)

    return JsonResponse(
        {
            "id": str(candidate.pk),
            "status": candidate.status,
        }
    )
