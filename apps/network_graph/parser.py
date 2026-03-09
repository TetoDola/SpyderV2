"""@mention parser with connection diffing and ghost node support.

All graph mutations route through the DSL layer.
"""

import re

from apps.network_graph.dsl import DSLContext, connect, create_node
from apps.network_graph.models import Connection, Node

# Two patterns:
# 1. @[Complex Name (with parens, dots, etc.)] — bracket-delimited (from Obsidian import)
# 2. @Simple Name — word chars and spaces until a delimiter (from UI @mentions)
MENTION_RE = re.compile(r"@\[([^\]]+)\]|@([\w][\w ]*?)(?=[,.\n@]|$)", re.UNICODE)


def extract_mentions(text: str) -> list[str]:
    """Return all unique mentioned titles from @ syntax."""
    raw = MENTION_RE.findall(text)  # list of (bracket_match, simple_match)
    return list(
        dict.fromkeys(
            (bracket or simple).strip() for bracket, simple in raw if (bracket or simple).strip()
        )
    )


def _collect_desired_connections(
    node: Node,
) -> set[tuple[str, str]]:
    """Build the set of (target_title, relationship_label) that SHOULD exist.

    Scans both notes (label="") and each property value (label=key).
    """
    desired: set[tuple[str, str]] = set()

    # From notes — blank label
    for title in extract_mentions(node.notes):
        if title != node.title:
            desired.add((title, ""))

    # From properties — key becomes the label
    if isinstance(node.properties, dict):
        for key, value in node.properties.items():
            if not isinstance(value, str):
                continue
            for title in extract_mentions(value):
                if title != node.title:
                    desired.add((title, key))

    return desired


def sync_connections(node: Node, ctx: DSLContext | None = None) -> None:
    """Diff current connections against mentions and create/delete as needed.

    All graph mutations go through the DSL layer for audit logging.
    """
    if ctx is None:
        ctx = DSLContext()

    desired = _collect_desired_connections(node)

    # Resolve or create target nodes — collect mapping title -> Node
    target_map: dict[str, Node] = {}
    for title, _label in desired:
        if title not in target_map:
            # Case-insensitive lookup to avoid duplicate ghosts
            target_node = Node.objects.filter(title__iexact=title).first()
            if not target_node:
                target_node = create_node(
                    ctx,
                    node_type="PERSON",
                    title=title,
                    is_ghost=True,
                )
            target_map[title] = target_node

    # Build desired set as (target_id, label)
    desired_edges: set[tuple[str, str]] = set()
    for title, label in desired:
        target = target_map[title]
        desired_edges.add((str(target.pk), label))

    # Get existing outgoing connections for this node
    existing = Connection.objects.filter(source=node).select_related("target")
    existing_edges: set[tuple[str, str]] = set()
    existing_map: dict[tuple[str, str], Connection] = {}

    for conn in existing:
        key = (str(conn.target_id), conn.relationship_label)
        existing_edges.add(key)
        existing_map[key] = conn

    # DELETE stale edges
    to_delete = existing_edges - desired_edges
    for edge_key in to_delete:
        existing_map[edge_key].delete()

    # CREATE new edges via DSL
    to_create = desired_edges - existing_edges
    for target_id, label in to_create:
        connect(ctx, source_id=str(node.pk), target_id=target_id, relationship_label=label)


def process_auto_links(node: Node, ctx: DSLContext | None = None) -> None:
    """Run the full mention parse + connection diff/sync."""
    sync_connections(node, ctx)
