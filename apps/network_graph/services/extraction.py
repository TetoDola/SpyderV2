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


# Default extraction result when no entities are found
EMPTY_EXTRACTION: dict[str, object] = {
    "people": [],
    "companies": [],
    "relationships": [],
    "meeting_context": {
        "date": None,
        "key_points": [],
        "decisions": [],
    },
}

EXTRACTION_SYSTEM_PROMPT = """You are an entity extraction engine for a personal CRM.
Given a transcript, document, or note, extract structured data.

Rules:
- Extract ALL people mentioned by name. Include email, company, and job title if stated.
- Extract ALL companies mentioned. Include website and industry if stated.
- Extract relationships between entities (who knows whom, who works where, who attended what).
- For relationship labels, use one of: KNOWS, WORKS_AT, ATTENDED, DISCUSSED.
- Extract meeting context: date (ISO 8601 if possible), key discussion points, and decisions made.
- Only extract what is explicitly stated or clearly implied. Do not hallucinate.
- If a field is not mentioned, use null.
- Return valid JSON matching the provided schema exactly.
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
                "content": f"Extract all entities from the following text:\n\n{raw_text}",
            }
        ],
    )

    # Find the tool use block in the response
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_entities":
            result = block.input
            if isinstance(result, dict):
                return result

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

    return result


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

    return result
