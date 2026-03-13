import json
import logging
import re

import frontmatter
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.dateparse import parse_datetime
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

logger = logging.getLogger(__name__)


# Active statuses for notification badge count
ACTIVE_STATUSES = [
    IngestionStatus.PENDING,
    IngestionStatus.TRANSCRIBING,
    IngestionStatus.EXTRACTING,
    IngestionStatus.RESOLVING,
    IngestionStatus.WRITING,
    IngestionStatus.SUMMARIZING,
    IngestionStatus.FAILED,
]

# Regex to translate Obsidian [[Link]] to @[Link] (bracket-delimited)
# Negative lookbehind skips image embeds ![[image.jpg]]
OBSIDIAN_LINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".webm"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def _pick_ingestion_task(ingestion: Ingestion) -> object:
    """Choose the right Celery task based on whether a file is attached."""
    from apps.network_graph.tasks import (
        process_document,
        process_freeform_note,
        process_voice_note,
    )

    if ingestion.original_file:
        name = ingestion.original_file.name or ""
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if ext in AUDIO_EXTENSIONS:
            return process_voice_note
        if ext in DOCUMENT_EXTENSIONS:
            return process_document
    return process_freeform_note


@require_GET
def index(request: HttpRequest) -> HttpResponse:
    """Serve the main graph canvas page."""
    return render(request, "network_graph/index.html")


@require_GET
def api_graph(request: HttpRequest) -> JsonResponse:
    """Return graph data for force-graph rendering.

    MEETING nodes are excluded — meetings surface as metadata on KNOWS
    edges and in the person summary tab instead of as standalone nodes.
    """
    nodes = Node.objects.exclude(node_type="MEETING")
    node_ids = nodes.values_list("pk", flat=True)
    edges = Connection.objects.select_related("source", "target").filter(
        source_id__in=node_ids,
        target_id__in=node_ids,
    )

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
            "metadata": e.metadata if isinstance(e.metadata, dict) else {},
        }
        for e in edges
    ]

    return JsonResponse({"nodes": node_data, "edges": edge_data})


@require_GET
def api_node_detail(request: HttpRequest, node_id: str) -> JsonResponse:
    """Return full details for a single node.

    For PERSON nodes, includes aggregated interaction history from
    all KNOWS edges (meeting metadata).
    """
    node = get_object_or_404(Node, pk=node_id)

    data: dict[str, object] = {
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

    # Aggregate interaction history from KNOWS edges for PERSON nodes
    if node.node_type == "PERSON":
        from django.db.models import Q

        knows_edges = Connection.objects.filter(
            Q(source_id=node_id) | Q(target_id=node_id),
            relationship_label="KNOWS",
        ).select_related("source", "target")

        all_meetings: list[dict[str, str]] = []
        for edge in knows_edges:
            meta = edge.metadata if isinstance(edge.metadata, dict) else {}
            meetings = meta.get("meetings", [])
            if isinstance(meetings, list):
                # Tag each meeting with who the other person is
                other = edge.target if str(edge.source_id) == node_id else edge.source
                for m in meetings:
                    if isinstance(m, dict):
                        all_meetings.append({
                            "meeting_node_id": m.get("meeting_node_id", ""),
                            "title": m.get("title", ""),
                            "date": m.get("date", ""),
                            "context": m.get("context", ""),
                            "with_person": other.title,
                            "with_person_id": str(other.pk),
                        })

        # Deduplicate by meeting_node_id and sort by date descending
        seen_ids: set[str] = set()
        unique_meetings: list[dict[str, str]] = []
        for m in all_meetings:
            mid = m.get("meeting_node_id", "")
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                unique_meetings.append(m)
        unique_meetings.sort(key=lambda m: m.get("date", ""), reverse=True)

        data["interactions"] = unique_meetings

    return JsonResponse(data)


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
    """Search nodes by title prefix for @ autocomplete.

    Query params:
        q: search string (title icontains)
        node_type: filter by node type (e.g. PERSON)
    """
    q = request.GET.get("q", "").strip()
    node_type = request.GET.get("node_type", "").strip()

    qs = Node.objects.all()
    if q:
        qs = qs.filter(title__icontains=q)
    if node_type and node_type in ("PERSON", "COMPANY", "MEETING"):
        qs = qs.filter(node_type=node_type)
    nodes = qs[:10]
    return JsonResponse(
        {
            "results": [
                {
                    "id": str(n.id),
                    "title": n.title,
                    "node_type": n.node_type,
                    "properties": n.properties,
                    "is_ghost": n.is_ghost,
                }
                for n in nodes
            ]
        }
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
    """Ingest notes about a specific person.

    Accepts multipart form with:
        about_node_id: UUID of existing person (mutually exclusive with about_name)
        about_name: name string if creating new person
        about_create_new: bool, true if about_name should create a new node
        notes: text content (required if no file)
        auto_create: bool, auto-create extracted entities
        file: optional attachment
    """
    # Parse form data (multipart or JSON)
    if request.content_type and "multipart" in request.content_type:
        about_node_id = request.POST.get("about_node_id", "").strip()
        about_name = request.POST.get("about_name", "").strip()
        notes = request.POST.get("notes", "").strip()
        auto_create = request.POST.get("auto_create", "true").lower() == "true"
        file = request.FILES.get("file")
    else:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        about_node_id = str(data.get("about_node_id", "")).strip()
        about_name = str(data.get("about_name", "")).strip()
        notes = str(data.get("notes", "")).strip()
        auto_create = bool(data.get("auto_create", True))
        file = None

    # Validation
    if not about_node_id and not about_name:
        return JsonResponse({"error": "about_node_id or about_name is required"}, status=400)
    if not notes and not file:
        return JsonResponse({"error": "Notes or a file attachment is required"}, status=400)

    # Resolve the about person
    if about_node_id:
        about_node = Node.objects.filter(pk=about_node_id).first()
        if not about_node:
            return JsonResponse({"error": "Person not found"}, status=404)
        title = f"Notes about {about_node.title}"
    else:
        title = f"Notes about {about_name}"

    ingestion = Ingestion.objects.create(
        source_type=IngestionSourceType.FREEFORM_NOTE,
        title=title,
        raw_text=notes,
        auto_create=auto_create,
        original_file=file,
        status=IngestionStatus.PENDING,
    )

    _pick_ingestion_task(ingestion).delay(str(ingestion.pk))

    return JsonResponse(
        {
            "ingestion_id": str(ingestion.pk),
            "status": ingestion.status,
            "message": "Notes queued for processing.",
        },
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_ingest_meeting(request: HttpRequest) -> JsonResponse:
    """Log a meeting with linked people, notes, and optional file attachment.

    Accepts multipart form with:
        title: optional meeting title
        date: date string (defaults to today)
        linked_people: JSON array of {node_id} or {name, create_new: true}
        notes: text content
        auto_create: bool, auto-create extracted entities
        file: optional audio/document attachment
    """
    title = request.POST.get("title", "").strip()
    date = request.POST.get("date", "").strip()
    linked_people_raw = request.POST.get("linked_people", "[]")
    notes = request.POST.get("notes", "").strip()
    auto_create = request.POST.get("auto_create", "true").lower() == "true"
    file = request.FILES.get("file")

    # Parse linked_people JSON
    try:
        linked_people = json.loads(linked_people_raw)
        if not isinstance(linked_people, list):
            return JsonResponse({"error": "linked_people must be an array"}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid linked_people JSON"}, status=400)

    # Validation
    if not linked_people:
        return JsonResponse({"error": "At least one person must be linked"}, status=400)

    # Validate each linked person entry
    for i, person in enumerate(linked_people):
        if not isinstance(person, dict):
            return JsonResponse(
                {"error": f"linked_people[{i}] must be an object"},
                status=400,
            )
        node_id = person.get("node_id")
        name = person.get("name", "")
        create_new = person.get("create_new", False)
        if node_id:
            if not Node.objects.filter(pk=node_id).exists():
                return JsonResponse(
                    {"error": f"Person with id {node_id} no longer exists. Please re-select."},
                    status=400,
                )
        elif create_new and name:
            continue  # Valid: creating a new person
        else:
            return JsonResponse(
                {"error": f"linked_people[{i}] must have node_id or name with create_new"},
                status=400,
            )

    if not notes and not file:
        return JsonResponse({"error": "Notes or a file attachment is required"}, status=400)

    # Validate file extension if provided
    if file:
        ext = "." + file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
        allowed = AUDIO_EXTENSIONS | DOCUMENT_EXTENSIONS
        if ext not in allowed:
            return JsonResponse(
                {"error": f"Unsupported file format. Allowed: {', '.join(sorted(allowed))}"},
                status=400,
            )

    # Build title
    if not title:
        names = []
        for p in linked_people[:3]:
            if p.get("node_id"):
                node = Node.objects.filter(pk=p["node_id"]).first()
                names.append(node.title if node else "Unknown")
            elif p.get("name"):
                names.append(p["name"])
        title = f"Meeting with {', '.join(names)}"
        if len(linked_people) > 3:
            title += f" +{len(linked_people) - 3}"

    ingestion = Ingestion.objects.create(
        source_type=IngestionSourceType.MEETING,
        title=title,
        raw_text=notes,
        auto_create=auto_create,
        original_file=file,
        extracted_json={
            "linked_people": linked_people,
            "meeting_date": date,
        },
        status=IngestionStatus.PENDING,
    )

    _pick_ingestion_task(ingestion).delay(str(ingestion.pk))

    return JsonResponse(
        {
            "ingestion_id": str(ingestion.pk),
            "status": ingestion.status,
            "message": "Meeting logged. Processing in background.",
        },
        status=201,
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
    """Return detailed review data for a completed ingestion.

    Parses dsl_commands to extract nodes_created, nodes_updated,
    and connections_created for the review card expansion.
    """
    ingestion = get_object_or_404(Ingestion, pk=ingestion_id)
    dsl_cmds = ingestion.dsl_commands if isinstance(ingestion.dsl_commands, list) else []

    nodes_created: list[dict[str, object]] = []
    nodes_updated: list[dict[str, object]] = []
    connections_created: list[dict[str, str]] = []

    for cmd in dsl_cmds:
        if not isinstance(cmd, dict):
            continue
        action = cmd.get("action", "")
        if action == "create_node":
            node_id = cmd.get("node_id", "")
            node = Node.objects.filter(pk=node_id).first() if node_id else None
            nodes_created.append(
                {
                    "id": node_id,
                    "name": node.title if node else cmd.get("title", "Unknown"),
                    "type": cmd.get("node_type", "PERSON"),
                    "is_ghost": node.is_ghost if node else False,
                }
            )
        elif action == "update_profile":
            node_id = cmd.get("node_id", "")
            node = Node.objects.filter(pk=node_id).first() if node_id else None
            updates = cmd.get("updates")
            changes = list(updates.keys()) if isinstance(updates, dict) else []
            nodes_updated.append(
                {
                    "id": node_id,
                    "name": node.title if node else "Unknown",
                    "type": node.node_type if node else "PERSON",
                    "changes": changes,
                }
            )
        elif action == "connect":
            source_id = cmd.get("source_id", "")
            target_id = cmd.get("target_id", "")
            src = Node.objects.filter(pk=source_id).first() if source_id else None
            tgt = Node.objects.filter(pk=target_id).first() if target_id else None
            connections_created.append(
                {
                    "from": src.title if src else "Unknown",
                    "to": tgt.title if tgt else "Unknown",
                    "label": cmd.get("label", ""),
                }
            )

    pending_candidates = ResolutionCandidate.objects.filter(
        ingestion=ingestion,
        status=ResolutionStatus.PENDING,
    ).select_related("candidate_node")

    return JsonResponse(
        {
            "ingestion_id": str(ingestion.pk),
            "source_type": ingestion.source_type,
            "status": ingestion.status,
            "title": ingestion.title,
            "created_at": ingestion.created_at.isoformat(),
            "completed_at": ingestion.completed_at.isoformat() if ingestion.completed_at else None,
            "results": {
                "nodes_created": nodes_created,
                "nodes_updated": nodes_updated,
                "connections_created": connections_created,
            },
            "pending_resolutions": len(list(pending_candidates)),
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


# ---------------------------------------------------------------------------
# Ingestion List & Dismiss
# ---------------------------------------------------------------------------


@require_GET
def api_ingestions_list(request: HttpRequest) -> JsonResponse:
    """List ingestions for the notification panel.

    Query params:
        status: 'active' | 'complete' | omit for all
        updated_since: ISO datetime — only return items changed since
        page: page number (default 1)
        page_size: items per page (default 20)
    """
    status_filter = request.GET.get("status", "").strip()
    updated_since = request.GET.get("updated_since", "").strip()
    page = max(1, int(request.GET.get("page", "1")))
    page_size = min(50, max(1, int(request.GET.get("page_size", "20"))))

    qs = Ingestion.objects.all().order_by("-created_at")

    if status_filter == "active":
        qs = qs.filter(status__in=ACTIVE_STATUSES)
    elif status_filter == "complete":
        qs = qs.filter(status__in=[IngestionStatus.COMPLETE, IngestionStatus.DISMISSED])

    if updated_since:
        dt = parse_datetime(updated_since)
        if dt:
            from django.db.models import Q

            qs = qs.filter(Q(created_at__gte=dt) | Q(completed_at__gte=dt))

    total = qs.count()
    offset = (page - 1) * page_size
    ingestions = qs[offset : offset + page_size]

    # Prefetch pending resolution candidates
    items = []
    for ing in ingestions:
        pending = ResolutionCandidate.objects.filter(
            ingestion=ing,
            status=ResolutionStatus.PENDING,
        ).select_related("candidate_node")

        items.append(
            {
                "id": str(ing.pk),
                "source_type": ing.source_type,
                "status": ing.status,
                "title": ing.title
                or (f"{ing.get_source_type_display()} — {ing.created_at.strftime('%Y-%m-%d')}"),
                "created_at": ing.created_at.isoformat(),
                "completed_at": ing.completed_at.isoformat() if ing.completed_at else None,
                "failed_step": ing.failed_step,
                "error_message": ing.error_message,
                "pending_resolutions": [
                    {
                        "id": str(c.pk),
                        "extracted_name": c.extracted_name,
                        "extracted_company": c.extracted_company,
                        "confidence": c.confidence,
                        "candidate_node": {
                            "id": str(c.candidate_node.pk),
                            "name": c.candidate_node.title,
                            "properties": c.candidate_node.properties,
                        }
                        if c.candidate_node
                        else None,
                    }
                    for c in pending
                ],
            }
        )

    # Also compute badge count
    badge_count = Ingestion.objects.filter(status__in=ACTIVE_STATUSES).count()
    badge_count += ResolutionCandidate.objects.filter(status=ResolutionStatus.PENDING).count()

    return JsonResponse(
        {
            "results": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "badge_count": badge_count,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_ingestion_dismiss(request: HttpRequest, ingestion_id: str) -> JsonResponse:
    """Dismiss a failed ingestion."""
    ingestion = get_object_or_404(Ingestion, pk=ingestion_id)

    if ingestion.status not in (IngestionStatus.FAILED, IngestionStatus.COMPLETE):
        return JsonResponse(
            {"error": "Can only dismiss failed or complete ingestions"},
            status=400,
        )

    ingestion.status = IngestionStatus.DISMISSED
    ingestion.save(update_fields=["status"])

    return JsonResponse({"success": True})


@csrf_exempt
@require_http_methods(["DELETE"])
def api_ingestion_delete(request: HttpRequest, ingestion_id: str) -> JsonResponse:
    """Delete an ingestion and all nodes/connections it created.

    Parses ``dsl_commands`` to find every CREATE_NODE and CONNECT entry,
    then deletes those objects before deleting the Ingestion itself.
    ResolutionCandidates cascade-delete automatically via FK.
    """
    ingestion = get_object_or_404(Ingestion, pk=ingestion_id)

    commands = ingestion.dsl_commands if isinstance(ingestion.dsl_commands, list) else []
    extracted = ingestion.extracted_json if isinstance(ingestion.extracted_json, dict) else {}

    # Collect IDs of nodes this ingestion created
    created_node_ids: set[str] = set()
    for cmd in commands:
        if not isinstance(cmd, dict):
            continue
        if cmd.get("command") == "CREATE_NODE":
            nid = cmd.get("node_id")
            if nid:
                created_node_ids.add(str(nid))

    # Also include the meeting node (may not appear in dsl_commands if created
    # by the graph writer before the DSL context was set up).
    meeting_node_id = extracted.get("_meeting_node_id")
    if meeting_node_id:
        created_node_ids.add(str(meeting_node_id))

    # Collect connection IDs that were created (not pre-existing)
    created_conn_pairs: list[tuple[str, str, str]] = []
    for cmd in commands:
        if not isinstance(cmd, dict):
            continue
        if cmd.get("command") == "CONNECT" and cmd.get("created"):
            src = cmd.get("source_id", "")
            tgt = cmd.get("target_id", "")
            lbl = cmd.get("label", "")
            if src and tgt:
                created_conn_pairs.append((str(src), str(tgt), str(lbl)))

    deleted_nodes = 0
    deleted_connections = 0

    # Delete connections first (to avoid FK issues)
    for src, tgt, lbl in created_conn_pairs:
        count, _ = Connection.objects.filter(
            source_id=src, target_id=tgt, relationship_label=lbl,
        ).delete()
        deleted_connections += count

    # Also delete any connections TO or FROM created nodes (even if not
    # logged as "created" — e.g. connections added by resolution later).
    if created_node_ids:
        count, _ = Connection.objects.filter(source_id__in=created_node_ids).delete()
        deleted_connections += count
        count, _ = Connection.objects.filter(target_id__in=created_node_ids).delete()
        deleted_connections += count

    # Delete the created nodes
    if created_node_ids:
        deleted_nodes, _ = Node.objects.filter(pk__in=created_node_ids).delete()

    # Delete the ingestion (cascades ResolutionCandidates)
    ingestion.delete()

    logger.info(
        "Deleted ingestion %s: %d nodes, %d connections removed",
        ingestion_id, deleted_nodes, deleted_connections,
    )

    return JsonResponse({
        "success": True,
        "deleted_nodes": deleted_nodes,
        "deleted_connections": deleted_connections,
    })
