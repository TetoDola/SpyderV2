"""Graph mutation DSL layer.

All database writes for the pipeline go through these five commands.
Each command logs itself to an optional command log (stored on Ingestion.dsl_commands).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction

from apps.network_graph.models import (
    Connection,
    Ingestion,
    Node,
    ResolutionCandidate,
    ResolutionStatus,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class DSLContext:
    """Execution context that tracks all commands issued during a pipeline run."""

    ingestion_id: str | None = None
    commands: list[dict[str, object]] = field(default_factory=list)

    def _log(self, command: str, **kwargs: object) -> None:
        entry = {"command": command, **kwargs}
        self.commands.append(entry)
        logger.info("DSL %s: %s", command, entry)

    def flush_to_ingestion(self) -> None:
        """Persist the command log to the Ingestion record (appends to existing commands).

        BUG FIX (2026-03-11): dsl_commands audit log showed only UPDATE_PROFILE

        What happened: After a meeting ingestion completed, inspecting
        ingestion.dsl_commands showed only UPDATE_PROFILE commands from the
        summarize step. All CREATE_NODE and CONNECT commands from the resolve
        and write steps were missing, making it look like zero edges were created.

        Root cause: The pipeline runs as 3 separate Celery tasks (resolve →
        write → summarize), each creating its own DSLContext. This method
        originally did:
            Ingestion.objects.filter(pk=...).update(dsl_commands=self.commands)
        This is a full replace — so each step's flush overwrote the previous
        step's commands. The summarize step ran last, so only its UPDATE_PROFILE
        commands survived.

        Fix: Read existing commands first, then append this step's commands.
        """
        if self.ingestion_id:
            ingestion = Ingestion.objects.get(pk=self.ingestion_id)
            existing = ingestion.dsl_commands if isinstance(ingestion.dsl_commands, list) else []
            ingestion.dsl_commands = existing + self.commands
            ingestion.save(update_fields=["dsl_commands"])


def create_node(
    ctx: DSLContext,
    node_type: str,
    title: str,
    properties: dict[str, object] | None = None,
    is_ghost: bool = False,
) -> Node:
    """Create a new node and log the command.

    If a PERSON node with the same email already exists, returns the
    existing node instead of creating a duplicate.
    """
    props = properties or {}
    email = str(props.get("Email", "") or "").strip() if isinstance(props, dict) else ""

    try:
        node = Node.objects.create(
            title=title,
            node_type=node_type,
            properties=props,
            is_ghost=is_ghost,
        )
    except IntegrityError:
        if node_type == "PERSON" and email:
            existing = Node.objects.filter(email=email).first()
            if existing:
                logger.info("Duplicate email %s — returning existing node %s", email, existing.pk)
                return existing
        raise

    ctx._log(
        "CREATE_NODE",
        node_id=str(node.pk),
        node_type=node_type,
        title=title,
        is_ghost=is_ghost,
    )
    return node


def connect(
    ctx: DSLContext,
    source_id: str,
    target_id: str,
    relationship_label: str = "",
) -> Connection:
    """Create an edge between two nodes. Returns existing if duplicate."""
    conn, created = Connection.objects.get_or_create(
        source_id=source_id,
        target_id=target_id,
        relationship_label=relationship_label,
    )
    ctx._log(
        "CONNECT",
        source_id=source_id,
        target_id=target_id,
        label=relationship_label,
        created=created,
    )
    return conn


def update_profile(
    ctx: DSLContext,
    node_id: str,
    new_data: dict[str, object],
) -> Node:
    """Update a node's properties and/or summary with new data."""
    node = Node.objects.get(pk=node_id)

    props = new_data.get("properties")
    if isinstance(props, dict) and isinstance(node.properties, dict):
        node.properties.update(props)

    summary = new_data.get("summary")
    if isinstance(summary, dict):
        node.summary = summary

    notes = new_data.get("notes")
    if isinstance(notes, str):
        node.notes = notes

    # Promote ghost if real data is being written
    if node.is_ghost:
        node.is_ghost = False

    node.save()  # email synced automatically via Node.save()

    ctx._log(
        "UPDATE_PROFILE",
        node_id=node_id,
        updated_keys=list(new_data.keys()),
    )
    return node


def flag_for_review(
    ctx: DSLContext,
    node_id: str,
    reason: str,
    ingestion_id: str | None = None,
    extracted_name: str = "",
    extracted_email: str = "",
    extracted_company: str = "",
    extracted_title: str = "",
    confidence: float = 0.0,
) -> ResolutionCandidate:
    """Flag a node for user review in the resolution queue."""
    ing_id = ingestion_id or ctx.ingestion_id
    if not ing_id:
        raise ValueError("Cannot flag for review without an ingestion_id")

    candidate = ResolutionCandidate.objects.create(
        ingestion_id=ing_id,
        extracted_name=extracted_name,
        extracted_email=extracted_email,
        extracted_company=extracted_company,
        extracted_title=extracted_title,
        candidate_node_id=node_id,
        confidence=confidence,
        status=ResolutionStatus.PENDING,
    )
    ctx._log(
        "FLAG_FOR_REVIEW",
        node_id=node_id,
        reason=reason,
        candidate_id=str(candidate.pk),
        confidence=confidence,
    )
    return candidate


@transaction.atomic
def merge_nodes(
    ctx: DSLContext,
    source_id: str,
    target_id: str,
) -> Node:
    """Merge source node into target node.

    - Transfers all connections from source to target
    - Merges properties (target wins on conflicts)
    - Appends source summary to target summary
    - Deletes source node
    """
    source = Node.objects.get(pk=source_id)
    target = Node.objects.get(pk=target_id)

    # Merge properties: source provides defaults, target wins on conflict
    if isinstance(source.properties, dict) and isinstance(target.properties, dict):
        merged = {**source.properties, **target.properties}
        # Remove empty strings from source that target already has values for
        for key, val in target.properties.items():
            if val:
                merged[key] = val
            elif source.properties.get(key):
                merged[key] = source.properties[key]
        target.properties = merged

    # Merge summaries
    if isinstance(source.summary, dict) and source.summary:
        if isinstance(target.summary, dict) and target.summary:
            # Append source key_context to target key_context
            src_ctx = source.summary.get("key_context", [])
            tgt_ctx = target.summary.get("key_context", [])
            if isinstance(src_ctx, list) and isinstance(tgt_ctx, list):
                target.summary["key_context"] = tgt_ctx + src_ctx
        else:
            target.summary = source.summary

    # Transfer outgoing connections
    for conn in Connection.objects.filter(source=source):
        if conn.target_id == target.pk:
            conn.delete()
            continue
        Connection.objects.get_or_create(
            source=target,
            target=conn.target,
            relationship_label=conn.relationship_label,
        )
        conn.delete()

    # Transfer incoming connections
    for conn in Connection.objects.filter(target=source):
        if conn.source_id == target.pk:
            conn.delete()
            continue
        Connection.objects.get_or_create(
            source=conn.source,
            target=target,
            relationship_label=conn.relationship_label,
        )
        conn.delete()

    # Append notes
    if source.notes.strip():
        separator = "\n\n---\n\n" if target.notes.strip() else ""
        target.notes = target.notes + separator + source.notes

    target.save()
    source.delete()

    ctx._log(
        "MERGE_NODES",
        source_id=source_id,
        target_id=target_id,
    )
    return target
