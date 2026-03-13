"""Graph schema contract for PERSON, COMPANY, and MEETING nodes.

Defines the expected property keys and edge types for each node type.
Used by DSL create_node() for validation and by the extraction prompt
to ensure field names match exactly.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NodeSchema:
    """Schema for a single node type."""

    system_locked: list[str] = field(default_factory=list)
    pipeline_populated: list[str] = field(default_factory=list)


PERSON_SCHEMA = NodeSchema(
    system_locked=["First Name", "Last Name", "Email", "Phone Number"],
    pipeline_populated=[
        "Title",  # job title, extracted from context
        "Company",  # company name as string (also linked via WORKS_AT edge)
        "First Met",  # date of earliest ingestion mentioning them
        "Last Interaction",  # date of most recent ingestion
        "Interaction Count",  # total ingestions mentioning them
    ],
)

COMPANY_SCHEMA = NodeSchema(
    system_locked=["Company Name", "Website", "Phone Number"],
    pipeline_populated=[
        "Industry",  # extracted from context if mentioned
        "Employee Count",  # count of PERSON nodes linked via WORKS_AT
    ],
)

MEETING_SCHEMA = NodeSchema(
    system_locked=["Date", "Attendees"],
    pipeline_populated=[
        "Source Type",  # voice_note / document / freeform_note
        "Participants",  # list of PERSON node IDs (also captured as edges)
        "Key Points",  # from meeting summary
        "Decisions",  # from meeting summary
        "Follow Ups",  # from meeting summary
    ],
)

NODE_SCHEMAS: dict[str, NodeSchema] = {
    "PERSON": PERSON_SCHEMA,
    "COMPANY": COMPANY_SCHEMA,
    "MEETING": MEETING_SCHEMA,
}


# ---------------------------------------------------------------------------
# Edge type constants
# ---------------------------------------------------------------------------

EDGE_ATTENDED = "ATTENDED"  # person → meeting
EDGE_KNOWS = "KNOWS"  # person ↔ person (with context label)
EDGE_WORKS_AT = "WORKS_AT"  # person → company
EDGE_DISCUSSED = "DISCUSSED"  # meeting → company

VALID_EDGE_TYPES = {
    # System-generated edges (not produced by extraction prompt)
    EDGE_ATTENDED,
    EDGE_DISCUSSED,
    # Full extraction vocabulary
    EDGE_KNOWS,
    EDGE_WORKS_AT,
    "WORKED_AT",
    "FOUNDED",
    "INVESTED_IN",
    "REPORTS_TO",
    "RELATED_TO",
    "PARTNERED_WITH",
    "ACQUIRED",
    "ASSOCIATED_WITH",
}

# ---------------------------------------------------------------------------
# Full relationship type vocabulary
# ---------------------------------------------------------------------------

EDGE_TYPES: dict[str, dict[str, object]] = {
    # Person → Company
    "WORKS_AT": {
        "from_type": "PERSON",
        "to_type": "COMPANY",
        "description": "Currently employed at",
    },
    "WORKED_AT": {
        "from_type": "PERSON",
        "to_type": "COMPANY",
        "description": "Previously employed at",
    },
    "FOUNDED": {
        "from_type": "PERSON",
        "to_type": "COMPANY",
        "description": "Created or co-founded",
    },
    "INVESTED_IN": {
        "from_type": None,  # Can be PERSON or COMPANY
        "to_type": "COMPANY",
        "description": "Invested in (angel or fund)",
    },
    # Person → Person
    "KNOWS": {
        "from_type": "PERSON",
        "to_type": "PERSON",
        "description": "Professional acquaintance",
    },
    "REPORTS_TO": {
        "from_type": "PERSON",
        "to_type": "PERSON",
        "description": "Reports to (direct management)",
    },
    "RELATED_TO": {
        "from_type": "PERSON",
        "to_type": "PERSON",
        "description": "Family or personal relationship",
    },
    # Company → Company
    "PARTNERED_WITH": {
        "from_type": "COMPANY",
        "to_type": "COMPANY",
        "description": "Business partnership",
    },
    "ACQUIRED": {
        "from_type": "COMPANY",
        "to_type": "COMPANY",
        "description": "Acquired or merged with",
    },
    # Generic fallback
    "ASSOCIATED_WITH": {
        "from_type": None,  # Any node type
        "to_type": None,    # Any node type
        "description": "Generic relationship that doesn't fit other types",
    },
}


# ---------------------------------------------------------------------------
# Extraction JSON schema (for LLM structured output)
# ---------------------------------------------------------------------------

EXTRACTION_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": ["string", "null"]},
                    "company": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                },
                "required": ["name"],
            },
        },
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "website": {"type": ["string", "null"]},
                    "industry": {"type": ["string", "null"]},
                },
                "required": ["name"],
            },
        },
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["name"],
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_name": {"type": "string"},
                    "to_name": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["from_name", "to_name", "label"],
            },
        },
        "meeting_context": {
            "type": "object",
            "properties": {
                "date": {"type": ["string", "null"]},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "decisions": {"type": "array", "items": {"type": "string"}},
                "follow_ups": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["people", "companies", "relationships", "meeting_context"],
}


# ---------------------------------------------------------------------------
# Summary schemas (for LLM structured output)
# ---------------------------------------------------------------------------

MEETING_SUMMARY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "one_liner": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "follow_ups": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["one_liner", "key_points", "decisions", "follow_ups"],
}

PERSON_PROFILE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "role": {"type": "string"},
        "how_we_know_each_other": {"type": "string"},
        "last_interaction": {"type": ["string", "null"]},
        "key_context": {"type": "array", "items": {"type": "string"}},
        "follow_ups_involving_them": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["role", "how_we_know_each_other", "key_context"],
}

COMPANY_SUMMARY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string"},
        "relationship_health": {"type": "string"},
        "total_contacts": {"type": "integer"},
        "last_interaction": {"type": ["string", "null"]},
        "key_context": {"type": "array", "items": {"type": "string"}},
        "open_follow_ups": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["company_name", "relationship_health", "total_contacts", "key_context"],
}

# Max word counts for summary compression
PERSON_SUMMARY_MAX_WORDS = 500
COMPANY_SUMMARY_MAX_WORDS = 300
