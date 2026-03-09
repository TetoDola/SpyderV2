"""Celery tasks for the ingestion pipeline.

Three separate ingestion tasks (voice, document, freeform) converge
into a shared pipeline: extract → resolve → write → summarize.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.network_graph.dsl import DSLContext
from apps.network_graph.models import Ingestion, IngestionStatus, Node

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step 1: Ingestion tasks (one per source type)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def process_voice_note(self: object, ingestion_id: str) -> None:
    """Transcribe a voice note and chain to extraction."""
    ingestion = Ingestion.objects.get(pk=ingestion_id)
    ingestion.status = IngestionStatus.TRANSCRIBING
    ingestion.save(update_fields=["status"])

    try:
        from apps.network_graph.services.ingest_voice import transcribe_audio

        if not ingestion.original_file:
            raise ValueError("No audio file attached to ingestion")

        raw_text = transcribe_audio(ingestion.original_file.path)
        ingestion.raw_text = raw_text
        ingestion.status = IngestionStatus.EXTRACTING
        ingestion.save(update_fields=["raw_text", "status"])

        # Chain to shared extraction
        extract_entities.delay(ingestion_id)

    except Exception as e:
        _mark_failed(ingestion, "TRANSCRIBING", e)
        raise


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=5,
)
def process_document(self: object, ingestion_id: str) -> None:
    """Extract text from a document and chain to extraction."""
    ingestion = Ingestion.objects.get(pk=ingestion_id)
    ingestion.status = IngestionStatus.EXTRACTING
    ingestion.save(update_fields=["status"])

    try:
        from apps.network_graph.services.ingest_document import extract_text

        if not ingestion.original_file:
            raise ValueError("No document file attached to ingestion")

        raw_text = extract_text(ingestion.original_file.path)
        ingestion.raw_text = raw_text
        ingestion.save(update_fields=["raw_text"])

        # Chain to shared extraction
        extract_entities.delay(ingestion_id)

    except Exception as e:
        _mark_failed(ingestion, "EXTRACTING", e)
        raise


@shared_task(bind=True)
def process_freeform_note(self: object, ingestion_id: str) -> None:
    """Process a freeform note and chain to extraction."""
    ingestion = Ingestion.objects.get(pk=ingestion_id)

    try:
        from apps.network_graph.services.ingest_freeform import (
            process_freeform_note as process_text,
        )

        # raw_text was already set from the request body
        ingestion.raw_text = process_text(ingestion.raw_text)
        ingestion.status = IngestionStatus.EXTRACTING
        ingestion.save(update_fields=["raw_text", "status"])

        # Chain to shared extraction
        extract_entities.delay(ingestion_id)

    except Exception as e:
        _mark_failed(ingestion, "EXTRACTING", e)
        raise


# ---------------------------------------------------------------------------
# Step 2: Entity extraction (shared)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def extract_entities(self: object, ingestion_id: str) -> None:
    """Extract entities via LLM and chain to resolution."""
    ingestion = Ingestion.objects.get(pk=ingestion_id)
    ingestion.status = IngestionStatus.EXTRACTING
    ingestion.save(update_fields=["status"])

    try:
        from apps.network_graph.services.extraction import extract_entities as do_extract

        result = do_extract(ingestion.raw_text)
        ingestion.extracted_json = result
        ingestion.status = IngestionStatus.RESOLVING
        ingestion.save(update_fields=["extracted_json", "status"])

        resolve_entities.delay(ingestion_id)

    except Exception as e:
        _mark_failed(ingestion, "EXTRACTING", e)
        raise


# ---------------------------------------------------------------------------
# Step 3: Entity resolution (shared)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=5,
)
def resolve_entities(self: object, ingestion_id: str) -> None:
    """Resolve extracted entities against existing graph nodes."""
    ingestion = Ingestion.objects.get(pk=ingestion_id)
    ingestion.status = IngestionStatus.RESOLVING
    ingestion.save(update_fields=["status"])

    try:
        from apps.network_graph.services.resolution import (
            resolve_companies,
            resolve_people,
        )

        ctx = DSLContext(ingestion_id=ingestion_id)
        extracted = ingestion.extracted_json if isinstance(ingestion.extracted_json, dict) else {}

        people = extracted.get("people", [])
        companies = extracted.get("companies", [])

        people_list = people if isinstance(people, list) else []
        company_list = companies if isinstance(companies, list) else []
        resolved_people = resolve_people(ctx, people_list)
        resolved_companies = resolve_companies(ctx, company_list)

        # Store resolved data for the next step
        ingestion.extracted_json["_resolved_people"] = [
            {
                "name": r.name,
                "node_id": r.node_id,
                "node_type": r.node_type,
                "confidence": r.confidence,
                "auto_linked": r.auto_linked,
                "is_new": r.is_new,
            }
            for r in resolved_people
        ]
        ingestion.extracted_json["_resolved_companies"] = [
            {
                "name": r.name,
                "node_id": r.node_id,
                "node_type": r.node_type,
                "confidence": r.confidence,
                "auto_linked": r.auto_linked,
                "is_new": r.is_new,
            }
            for r in resolved_companies
        ]

        ingestion.status = IngestionStatus.WRITING
        ingestion.save(update_fields=["extracted_json", "status"])
        ctx.flush_to_ingestion()

        write_graph.delay(ingestion_id)

    except Exception as e:
        _mark_failed(ingestion, "RESOLVING", e)
        raise


# ---------------------------------------------------------------------------
# Step 4: Graph writing (shared)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=5,
)
def write_graph(self: object, ingestion_id: str) -> None:
    """Write resolved entities to the graph via DSL."""
    ingestion = Ingestion.objects.get(pk=ingestion_id)
    ingestion.status = IngestionStatus.WRITING
    ingestion.save(update_fields=["status"])

    try:
        from apps.network_graph.services.graph_writer import (
            write_graph as do_write,
        )
        from apps.network_graph.services.resolution import ResolvedEntity

        ctx = DSLContext(ingestion_id=ingestion_id)
        extracted = ingestion.extracted_json if isinstance(ingestion.extracted_json, dict) else {}

        # Reconstruct ResolvedEntity objects from stored data
        resolved_people = [ResolvedEntity(**r) for r in extracted.get("_resolved_people", [])]
        resolved_companies = [
            ResolvedEntity(**r) for r in extracted.get("_resolved_companies", [])
        ]

        meeting_node = do_write(
            ctx,
            ingestion,
            resolved_people,
            resolved_companies,
            extracted,
        )

        # Store meeting node ID for summarization
        if meeting_node:
            ingestion.extracted_json["_meeting_node_id"] = str(meeting_node.pk)

        ingestion.status = IngestionStatus.SUMMARIZING
        ingestion.save(update_fields=["extracted_json", "status"])
        ctx.flush_to_ingestion()

        summarize.delay(ingestion_id)

    except Exception as e:
        _mark_failed(ingestion, "WRITING", e)
        raise


# ---------------------------------------------------------------------------
# Step 5: Summarization (shared)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=10,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def summarize(self: object, ingestion_id: str) -> None:
    """Run per-meeting, per-person, and per-company summaries."""
    ingestion = Ingestion.objects.get(pk=ingestion_id)
    ingestion.status = IngestionStatus.SUMMARIZING
    ingestion.save(update_fields=["status"])

    try:
        from apps.network_graph.services.summarization import (
            summarize_company,
            summarize_meeting,
            summarize_person,
        )

        ctx = DSLContext(ingestion_id=ingestion_id)
        extracted = ingestion.extracted_json if isinstance(ingestion.extracted_json, dict) else {}

        # Get meeting node
        meeting_node_id = extracted.get("_meeting_node_id")
        meeting_node = Node.objects.get(pk=meeting_node_id) if meeting_node_id else None

        # Get resolved people
        resolved_people_data = extracted.get("_resolved_people", [])
        person_nodes = [
            Node.objects.get(pk=r["node_id"])
            for r in resolved_people_data
            if isinstance(r, dict) and r.get("node_id")
        ]

        # 1. Per-meeting summary
        meeting_summary: dict[str, object] = {}
        if meeting_node:
            meeting_summary = summarize_meeting(
                ctx,
                meeting_node,
                ingestion.raw_text,
                person_nodes,
            )

        # 2. Per-person summaries
        for person_node in person_nodes:
            summarize_person(ctx, person_node, meeting_summary, ingestion.raw_text)

        # 3. Per-company summaries (only for companies with linked employees)
        resolved_companies_data = extracted.get("_resolved_companies", [])
        company_ids = {
            r["node_id"]
            for r in resolved_companies_data
            if isinstance(r, dict) and r.get("node_id")
        }
        for company_id in company_ids:
            company_node = Node.objects.filter(pk=company_id).first()
            if company_node:
                summarize_company(ctx, company_node)

        # Mark complete
        ingestion.status = IngestionStatus.COMPLETE
        ingestion.completed_at = timezone.now()
        ingestion.save(update_fields=["status", "completed_at"])
        ctx.flush_to_ingestion()

    except Exception as e:
        _mark_failed(ingestion, "SUMMARIZING", e)
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mark_failed(ingestion: Ingestion, step: str, error: Exception) -> None:
    """Mark an ingestion as failed with error context."""
    ingestion.status = IngestionStatus.FAILED
    ingestion.failed_step = step
    ingestion.error_message = str(error)
    ingestion.save(update_fields=["status", "failed_step", "error_message"])
    logger.error("Ingestion %s failed at %s: %s", ingestion.pk, step, error)
