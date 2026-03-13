"""LLM entity extraction service.

Single LLM call with strict JSON schema output.
Extracts: people, companies, relationships, meeting context.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings

from apps.network_graph.schema import EXTRACTION_JSON_SCHEMA

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when entity extraction fails."""


def validate_extraction_output(data: object) -> dict[str, object]:
    """Validate that LLM extraction output matches the expected structure.

    Raises ExtractionError with details if invalid. Returns normalized data.
    """
    if not isinstance(data, dict):
        raise ExtractionError(f"Extraction output must be a dict, got {type(data).__name__}")

    errors: list[str] = []

    # people: list of dicts with at least "name"
    people = data.get("people", [])
    if not isinstance(people, list):
        errors.append(f"'people' must be a list, got {type(people).__name__}")
    else:
        for i, person in enumerate(people):
            if not isinstance(person, dict):
                errors.append(f"people[{i}] must be a dict")
            elif not person.get("name"):
                errors.append(f"people[{i}] missing required 'name' field")

    # companies: list of dicts with at least "name"
    companies = data.get("companies", [])
    if not isinstance(companies, list):
        errors.append(f"'companies' must be a list, got {type(companies).__name__}")
    else:
        for i, company in enumerate(companies):
            if not isinstance(company, dict):
                errors.append(f"companies[{i}] must be a dict")
            elif not company.get("name"):
                errors.append(f"companies[{i}] missing required 'name' field")

    # relationships: list of dicts with from_name, to_name, label
    relationships = data.get("relationships", [])
    if not isinstance(relationships, list):
        errors.append(f"'relationships' must be a list, got {type(relationships).__name__}")
    else:
        for i, rel in enumerate(relationships):
            if not isinstance(rel, dict):
                errors.append(f"relationships[{i}] must be a dict")
            else:
                for key in ("from_name", "to_name", "label"):
                    if not rel.get(key):
                        errors.append(f"relationships[{i}] missing required '{key}'")

    # meeting_context: dict or null
    meeting_context = data.get("meeting_context")
    if meeting_context is not None and not isinstance(meeting_context, dict):
        errors.append(
            f"'meeting_context' must be a dict or null, "
            f"got {type(meeting_context).__name__}"
        )

    if errors:
        raise ExtractionError(
            "Extraction validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    # products: optional list of dicts (informational, not graph nodes)
    products = data.get("products", [])
    if not isinstance(products, list):
        # Non-fatal: coerce to empty list
        data["products"] = []

    # Normalize: ensure optional keys exist with defaults
    data.setdefault("people", [])
    data.setdefault("companies", [])
    data.setdefault("products", [])
    data.setdefault("relationships", [])
    data.setdefault("meeting_context", None)

    return data


# Default extraction result when no entities are found
EMPTY_EXTRACTION: dict[str, object] = {
    "people": [],
    "companies": [],
    "products": [],
    "relationships": [],
    "meeting_context": {
        "date": None,
        "key_points": [],
        "decisions": [],
        "follow_ups": [],
    },
}

EXTRACTION_SYSTEM_PROMPT = """\
You are an entity extraction engine for a personal CRM that maps professional relationships.

Your job: Given notes from a meeting or interaction, extract the PEOPLE, COMPANIES, and RELATIONSHIPS that are relevant to the user's professional network.

CRITICAL DISTINCTION — Companies vs Products/Tools:
- COMPANY = An organization where someone WORKS, INVESTS, FOUNDED, or has a direct professional relationship with. These become nodes in the relationship graph.
- PRODUCT/TOOL = Software, platforms, services, or technologies being discussed, evaluated, or used. These do NOT become company nodes.

Examples:
- "Sarah works at Stripe" → Stripe is a COMPANY (someone works there)
- "We're evaluating Auth0 vs Clerk for authentication" → Auth0 and Clerk are PRODUCTS, not companies
- "André runs CarbonPath" → CarbonPath is a COMPANY (someone founded/runs it)
- "They're migrating to Snowflake" → Snowflake is a PRODUCT (tool being adopted)
- "Nadia's fund Equinox Ventures led the round" → Equinox Ventures is a COMPANY (investor with a relationship)
- "We use Grafana Cloud for monitoring" → Grafana Cloud is a PRODUCT
- "She left Datadog to join Nextera" → Datadog AND Nextera are COMPANIES (someone worked/works there)

Rule of thumb: If a person WORKS AT, FOUNDED, INVESTED IN, or has a professional role at the organization → COMPANY. If it's a tool, product, or vendor being used/evaluated → PRODUCT.

PEOPLE EXTRACTION RULES:
- Extract every person mentioned by name.
- Include email, company, and job title ONLY if explicitly stated. Use null otherwise.
- The person writing these notes is the "user" — do not extract them as a person.

COMPANY EXTRACTION RULES:
- Only extract companies where someone has a professional role (works at, founded, invests through, advises).
- Do NOT extract products, tools, platforms, or technologies as companies.
- Include website and industry only if explicitly stated.

RELATIONSHIP EXTRACTION RULES:
Use the most specific label that fits. Fall back to ASSOCIATED_WITH only if nothing else applies.

Person → Company:
- WORKS_AT: Person currently has a role at a company ("Sarah is CTO at Acme").
- WORKED_AT: Person previously worked there ("she left Datadog to join Nextera" → WORKED_AT Datadog).
- FOUNDED: Person created or co-founded the company ("André runs CarbonPath" / "co-founder of").
- INVESTED_IN: Person or company invested in a company ("Nadia's fund led the round").

Person → Person:
- KNOWS: Two people have a direct professional connection described in the notes. Do NOT connect everyone who appears in the same document — only people explicitly described as knowing each other.
- REPORTS_TO: Direct management relationship ("she reports to Marcus", "Marcus manages the team").
- RELATED_TO: Family or personal relationship ("his brother", "married to").

Company → Company:
- PARTNERED_WITH: Active business partnership between two companies.
- ACQUIRED: One company acquired or merged with another.

Fallback:
- ASSOCIATED_WITH: A relationship that clearly exists but doesn't fit any category above.

Do NOT create relationships between the user and extracted people (the system handles this separately).

PRODUCT/TOOL EXTRACTION:
- Extract products, tools, platforms, and technologies mentioned. Include context for how they're being used or evaluated.

MEETING CONTEXT RULES:
- date: YYYY-MM-DD if mentioned, null if not.
- key_points: 3-10 bullet points of the most important things discussed. Be specific, use names.
- decisions: Specific decisions that were made (not discussion topics).
- follow_ups: Action items with who is responsible and any deadlines mentioned.

Return ONLY valid JSON matching the exact schema provided. No markdown, no preamble.

EXAMPLE OUTPUT:

{
  "people": [
    {"name": "Sarah Chen", "email": "sarah@acme.com", "company": "Acme Corp", "title": "CTO"},
    {"name": "James Park", "email": null, "company": "Acme Corp", "title": null}
  ],
  "companies": [
    {"name": "Acme Corp", "website": null, "industry": "Technology"}
  ],
  "products": [
    {"name": "Temporal", "context": "Using for workflow orchestration"},
    {"name": "Auth0", "context": "Evaluated for authentication, rejected due to pricing"}
  ],
  "relationships": [
    {"from_name": "Sarah Chen", "to_name": "Acme Corp", "label": "WORKS_AT"},
    {"from_name": "Sarah Chen", "to_name": "Previous Corp", "label": "WORKED_AT"},
    {"from_name": "Sarah Chen", "to_name": "James Park", "label": "KNOWS"},
    {"from_name": "James Park", "to_name": "Sarah Chen", "label": "REPORTS_TO"}
  ],
  "meeting_context": {
    "date": "2025-03-09",
    "key_points": ["Sarah promoted to CTO at Acme Corp last month", "Acme evaluating Clerk for enterprise auth"],
    "decisions": ["Two-week POC with Clerk, Auth0 as fallback"],
    "follow_ups": ["Sarah sends API docs by Friday", "User intros James to recruiter contact"]
  }
}

NOW EXTRACT FROM THE FOLLOWING TEXT:
"""


def extract_entities(raw_text: str) -> dict[str, object]:
    """Extract entities and relationships from raw text using an LLM.

    Args:
        raw_text: The cleaned plain text to extract from.

    Returns:
        Structured extraction result matching EXTRACTION_JSON_SCHEMA.

    Raises:
        ExtractionError: If the LLM call fails or returns invalid JSON.
    """
    if not raw_text.strip():
        return EMPTY_EXTRACTION

    provider: str = settings.LLM_PROVIDER

    if provider == "anthropic":
        return _extract_anthropic(raw_text)
    elif provider == "openai":
        return _extract_openai(raw_text)
    elif provider == "openrouter":
        return _extract_openrouter(raw_text)
    else:
        raise ExtractionError(f"Unsupported LLM provider: {provider}")


def _extract_anthropic(raw_text: str) -> dict[str, object]:
    """Extract using Anthropic Claude with tool use for structured output."""
    import anthropic

    api_key: str = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise ExtractionError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=api_key)

    tool_definition = {
        "name": "extract_entities",
        "description": "Extract people, companies, relationships, and meeting context from text.",
        "input_schema": EXTRACTION_JSON_SCHEMA,
    }

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=EXTRACTION_SYSTEM_PROMPT,
        tools=[tool_definition],
        tool_choice={"type": "tool", "name": "extract_entities"},
        messages=[
            {
                "role": "user",
                "content": raw_text,
            }
        ],
        timeout=60.0,
    )

    # Find the tool use block in the response
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_entities":
            result = block.input
            if isinstance(result, dict):
                return validate_extraction_output(result)

    raise ExtractionError("No tool_use block found in Anthropic response")


def _extract_openai(raw_text: str) -> dict[str, object]:
    """Extract using OpenAI with structured output / JSON mode."""
    import openai

    api_key: str = settings.OPENAI_API_KEY
    if not api_key:
        raise ExtractionError("OPENAI_API_KEY not configured")

    client = openai.OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Extract all entities from the following text. "
                    f"Return JSON matching this schema:\n"
                    f"{json.dumps(EXTRACTION_JSON_SCHEMA, indent=2)}\n\n"
                    f"Text:\n{raw_text}"
                ),
            },
        ],
        timeout=60.0,
    )

    content = response.choices[0].message.content
    if not content:
        raise ExtractionError("Empty response from OpenAI")

    try:
        result = json.loads(content)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"Invalid JSON from OpenAI: {e}") from e

    if not isinstance(result, dict):
        raise ExtractionError(f"Expected dict, got {type(result)}")

    return validate_extraction_output(result)


def _extract_openrouter(raw_text: str) -> dict[str, object]:
    """Extract using OpenRouter (OpenAI-compatible API)."""
    import openai

    api_key: str = settings.OPENROUTER_API_KEY
    if not api_key:
        raise ExtractionError("OPENROUTER_API_KEY not configured")

    base_url: str = settings.OPENROUTER_BASE_URL
    model: str = settings.OPENROUTER_MODEL

    client = openai.OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers={
            "HTTP-Referer": "https://unforgetting.app",
            "X-Title": "Unforgetting",
        },
    )

    try:
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Extract all entities from the following text. "
                        f"Return JSON matching this schema:\n"
                        f"{json.dumps(EXTRACTION_JSON_SCHEMA, indent=2)}\n\n"
                        f"Text:\n{raw_text}"
                    ),
                },
            ],
            timeout=60.0,
        )
    except Exception as e:
        raise ExtractionError(f"OpenRouter API call failed: {e}") from e

    choice = response.choices[0] if response.choices else None
    if not choice:
        raise ExtractionError("OpenRouter returned no choices")

    content = choice.message.content or ""
    content = content.strip()

    # Log for debugging
    finish_reason = getattr(choice, "finish_reason", "unknown")
    logger.info(
        "OpenRouter extraction: model=%s, finish_reason=%s, content_len=%d",
        model, finish_reason, len(content),
    )

    if not content:
        refusal = getattr(choice.message, "refusal", None)
        raise ExtractionError(
            f"Empty response from OpenRouter (finish_reason={finish_reason}, "
            f"refusal={refusal})"
        )

    # Strip markdown code fences if present
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        content = "\n".join(lines)

    try:
        result = json.loads(content)
    except json.JSONDecodeError as e:
        raise ExtractionError(
            f"Invalid JSON from OpenRouter: {e}\nRaw: {content[:500]}"
        ) from e

    if not isinstance(result, dict):
        raise ExtractionError(f"Expected dict, got {type(result)}")

    return validate_extraction_output(result)
