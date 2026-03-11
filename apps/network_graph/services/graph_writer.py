"""Graph writer service.

Takes resolved entities + extracted relationships and translates them
into DSL commands. All mutations go through the DSL layer.
"""

from __future__ import annotations

import logging
from datetime import datetime

from django.db import transaction

from apps.network_graph.dsl import DSLContext, connect, create_node, update_profile
from apps.network_graph.models import Ingestion, Node
from apps.network_graph.schema import (
    EDGE_ATTENDED,
    EDGE_DISCUSSED,
    EDGE_KNOWS,
    EDGE_WORKS_AT,
)
from apps.network_graph.services.resolution import ResolvedEntity

logger = logging.getLogger(__name__)


@transaction.atomic
def write_graph(
    ctx: DSLContext,
    ingestion: Ingestion,
    resolved_people: list[ResolvedEntity],
    resolved_companies: list[ResolvedEntity],
    extracted_json: dict[str, object],
) -> Node | None:
    """Write resolved entities and relationships to the graph.

    All node/edge mutations are wrapped in a single transaction.
    If any step fails, everything is rolled back.

    Returns the MEETING node created for this ingestion (if applicable).
    """
    today = datetime.now().date().isoformat()

    # Build lookup: entity name → node_id
    entity_map: dict[str, str] = {}
    for entity in resolved_people + resolved_companies:
        entity_map[entity.name.lower()] = entity.node_id

    # Update pipeline-populated properties on PERSON nodes
    for person in resolved_people:
        _update_person_properties(ctx, person, today)

    # Create MEETING node
    meeting_context = extracted_json.get("meeting_context", {})
    meeting_node = _create_meeting_node(ctx, ingestion, meeting_context)

    # Create edges
    _create_person_meeting_edges(ctx, resolved_people, meeting_node)
    _create_person_company_edges(ctx, resolved_people, resolved_companies, entity_map)
    _create_relationship_edges(ctx, extracted_json, entity_map)
    _create_company_meeting_edges(ctx, resolved_companies, meeting_node)

    return meeting_node


def _update_person_properties(
    ctx: DSLContext,
    person: ResolvedEntity,
    today: str,
) -> None:
    """Update pipeline-populated properties on a person node."""
    node = Node.objects.get(pk=person.node_id)
    props = node.properties if isinstance(node.properties, dict) else {}

    # Update Last Interaction
    update_data: dict[str, object] = {
        "properties": {
            "Last Interaction": today,
            "Interaction Count": str(int(props.get("Interaction Count", "0") or "0") + 1),
        }
    }

    # Set First Met if not already set
    if not props.get("First Met"):
        first_met = update_data["properties"]
        if isinstance(first_met, dict):
            first_met["First Met"] = today

    update_profile(ctx, node_id=person.node_id, new_data=update_data)


def _create_meeting_node(
    ctx: DSLContext,
    ingestion: Ingestion,
    meeting_context: object,
) -> Node:
    """Create a MEETING node for this ingestion."""
    mc = meeting_context if isinstance(meeting_context, dict) else {}

    date = mc.get("date", "") or ""
    key_points = mc.get("key_points", []) or []
    decisions = mc.get("decisions", []) or []

    properties: dict[str, object] = {
        "Date": str(date) if date else datetime.now().date().isoformat(),
        "Source Type": ingestion.source_type,
        "Key Points": key_points if isinstance(key_points, list) else [],
        "Decisions": decisions if isinstance(decisions, list) else [],
    }

    title = f"Ingestion {ingestion.created_at.strftime('%Y-%m-%d %H:%M')}"
    return create_node(ctx, node_type="MEETING", title=title, properties=properties)


def _create_person_meeting_edges(
    ctx: DSLContext,
    resolved_people: list[ResolvedEntity],
    meeting_node: Node,
) -> None:
    """Create ATTENDED edges: person → meeting."""
    for person in resolved_people:
        connect(
            ctx,
            source_id=person.node_id,
            target_id=str(meeting_node.pk),
            relationship_label=EDGE_ATTENDED,
        )


def _create_person_company_edges(
    ctx: DSLContext,
    resolved_people: list[ResolvedEntity],
    resolved_companies: list[ResolvedEntity],
    entity_map: dict[str, str],
) -> None:
    """Create WORKS_AT edges: person → company (from extraction data)."""
    # Build company name → node_id lookup
    company_map: dict[str, str] = {c.name.lower(): c.node_id for c in resolved_companies}

    for person in resolved_people:
        # Check if person has a company property that maps to a resolved company
        node = Node.objects.get(pk=person.node_id)
        if isinstance(node.properties, dict):
            person_company = str(node.properties.get("Company", "")).strip().lower()
            if person_company and person_company in company_map:
                connect(
                    ctx,
                    source_id=person.node_id,
                    target_id=company_map[person_company],
                    relationship_label=EDGE_WORKS_AT,
                )


def _create_relationship_edges(
    ctx: DSLContext,
    extracted_json: dict[str, object],
    entity_map: dict[str, str],
) -> None:
    """Create edges from the extracted relationships array."""
    relationships = extracted_json.get("relationships", [])
    if not isinstance(relationships, list):
        return

    for rel in relationships:
        if not isinstance(rel, dict):
            continue

        from_name = str(rel.get("from_name", "")).strip().lower()
        to_name = str(rel.get("to_name", "")).strip().lower()
        label = str(rel.get("label", EDGE_KNOWS)).strip()

        from_id = entity_map.get(from_name)
        to_id = entity_map.get(to_name)

        # Log unresolvable names instead of silently skipping. This happens
        # when extraction produces a relationship target that wasn't extracted
        # as a person or company (e.g. "Swiss Engineering Leaders" is an event,
        # not a resolved entity).
        if not from_id:
            logger.warning("Relationship skipped: from_name '%s' not found in resolved entities", from_name)
            continue
        if not to_id:
            logger.warning("Relationship skipped: to_name '%s' not found in resolved entities", to_name)
            continue
        if from_id == to_id:
            continue

        connect(ctx, source_id=from_id, target_id=to_id, relationship_label=label)


def _create_company_meeting_edges(
    ctx: DSLContext,
    resolved_companies: list[ResolvedEntity],
    meeting_node: Node,
) -> None:
    """Create DISCUSSED edges: meeting → company."""
    for company in resolved_companies:
        connect(
            ctx,
            source_id=str(meeting_node.pk),
            target_id=company.node_id,
            relationship_label=EDGE_DISCUSSED,
        )
