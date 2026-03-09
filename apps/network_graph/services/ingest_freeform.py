"""Freeform note ingestion service: user text → clean text with @mentions."""

from __future__ import annotations

import re

# Translate [[Link]] syntax to @[Link] for consistency
OBSIDIAN_LINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")


def process_freeform_note(text: str) -> str:
    """Process a freeform note for pipeline ingestion.

    - Translates [[Link]] to @[Link] syntax
    - Strips leading/trailing whitespace
    - Passes through as-is otherwise (no extraction needed)

    Args:
        text: Raw freeform note text from the user.

    Returns:
        Cleaned text ready for entity extraction.
    """
    # Translate [[Link]] → @[Link]
    text = OBSIDIAN_LINK_RE.sub(r"@[\1]", text)
    return text.strip()
