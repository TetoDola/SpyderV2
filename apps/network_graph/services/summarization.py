"""Summarization service.

Three summary types:
1. Per-meeting: structured output with key points, decisions, follow-ups
2. Per-person: running profile with append-rewrite pattern
3. Per-company: health summary aggregated from employee interactions
"""

from __future__ import annotations

import json
import logging

from django.conf import settings

from apps.network_graph.dsl import DSLContext, update_profile
from apps.network_graph.models import Connection, Node
from apps.network_graph.schema import (
    COMPANY_SUMMARY_MAX_WORDS,
    COMPANY_SUMMARY_SCHEMA,
    EDGE_WORKS_AT,
    MEETING_SUMMARY_SCHEMA,
    PERSON_PROFILE_SCHEMA,
    PERSON_SUMMARY_MAX_WORDS,
)

logger = logging.getLogger(__name__)


class SummarizationError(Exception):
    """Raised when summarization fails."""


# ---------------------------------------------------------------------------
# Per-meeting summary
# ---------------------------------------------------------------------------

MEETING_SUMMARY_SYSTEM_PROMPT = """You are a meeting summarization engine for a personal CRM.
Given a raw transcript/document and a list of participants with their existing profiles,
produce a structured meeting summary.

Rules:
- one_liner: A single sentence capturing the most important takeaway.
- key_points: 2-5 bullet points of what was discussed.
- decisions: Specific decisions that were made (empty list if none).
- follow_ups: Action items that need to happen next (empty list if none).
- Be specific and use people's names. Do not be generic.
- Return valid JSON matching the provided schema exactly.
"""


def summarize_meeting(
    ctx: DSLContext,
    meeting_node: Node,
    raw_text: str,
    participant_nodes: list[Node],
) -> dict[str, object]:
    """Generate a structured summary for a meeting and store it on the node."""
    # Build context about participants
    participant_context = ""
    for p in participant_nodes:
        existing_summary = p.summary if isinstance(p.summary, dict) else {}
        role = existing_summary.get("role", "Unknown role")
        context_items = existing_summary.get("key_context", [])
        context_str = "; ".join(context_items[:3]) if isinstance(context_items, list) else ""
        participant_context += f"- {p.title}: {role}. {context_str}\n"

    user_prompt = (
        f"Participants:\n{participant_context}\n\n"
        f"Transcript/Document:\n{raw_text}\n\n"
        f"Return JSON matching this schema:\n{json.dumps(MEETING_SUMMARY_SCHEMA, indent=2)}"
    )

    result = _call_llm(MEETING_SUMMARY_SYSTEM_PROMPT, user_prompt)

    # Store on meeting node
    update_profile(ctx, node_id=str(meeting_node.pk), new_data={"summary": result})

    return result


# ---------------------------------------------------------------------------
# Per-person running profile
# ---------------------------------------------------------------------------

PERSON_PROFILE_SYSTEM_PROMPT = """You are a personal CRM profile writer.
Given a person's existing profile and new interaction context,
produce an updated running profile.

Rules:
- Merge new information with existing context. Do NOT discard previous context.
- Compress if the profile exceeds {max_words} words. Prioritize recent and actionable info.
- role: Their current job title and company.
- how_we_know_each_other: How the user first met or knows this person.
- key_context: 3-7 bullet points of the most important things to remember.
- follow_ups_involving_them: Open action items related to this person.
- Return valid JSON matching the provided schema exactly.
""".replace("{max_words}", str(PERSON_SUMMARY_MAX_WORDS))


def summarize_person(
    ctx: DSLContext,
    person_node: Node,
    meeting_summary: dict[str, object],
    raw_text: str,
) -> dict[str, object]:
    """Update a person's running profile with new interaction context."""
    existing = person_node.summary if isinstance(person_node.summary, dict) else {}

    user_prompt = (
        f"Person: {person_node.title}\n\n"
        f"Existing profile:\n"
        f"{json.dumps(existing, indent=2) if existing else 'None — first interaction.'}"
        f"\n\n"
        f"New meeting summary:\n{json.dumps(meeting_summary, indent=2)}\n\n"
        f"Raw text from interaction:\n{raw_text[:2000]}\n\n"
        f"Return JSON matching this schema:\n{json.dumps(PERSON_PROFILE_SCHEMA, indent=2)}"
    )

    result = _call_llm(PERSON_PROFILE_SYSTEM_PROMPT, user_prompt)

    update_profile(ctx, node_id=str(person_node.pk), new_data={"summary": result})

    return result


# ---------------------------------------------------------------------------
# Per-company health summary
# ---------------------------------------------------------------------------

COMPANY_SUMMARY_SYSTEM_PROMPT = """\
You are a company relationship health analyzer for a personal CRM.
Given a company and profiles of all known contacts at that company,
produce a relationship health summary.

Rules:
- Aggregate context from all employee profiles.
- relationship_health: "strong", "moderate", or "weak" based on recency and depth of interactions.
- key_context: 3-5 bullet points of the most important things about this company relationship.
- open_follow_ups: Aggregated from all employee profiles.
- Keep under {max_words} words total.
- Return valid JSON matching the provided schema exactly.
""".replace("{max_words}", str(COMPANY_SUMMARY_MAX_WORDS))


def summarize_company(
    ctx: DSLContext,
    company_node: Node,
) -> dict[str, object]:
    """Generate or update a company's relationship health summary."""
    # Find all PERSON nodes linked via WORKS_AT
    works_at_connections = Connection.objects.filter(
        target=company_node,
        relationship_label=EDGE_WORKS_AT,
    ).select_related("source")

    employee_nodes = [conn.source for conn in works_at_connections]
    if not employee_nodes:
        return {}

    # Build employee profiles context
    employee_context = ""
    for emp in employee_nodes:
        summary = emp.summary if isinstance(emp.summary, dict) else {}
        employee_context += f"- {emp.title}: {json.dumps(summary)}\n"

    user_prompt = (
        f"Company: {company_node.title}\n\n"
        f"Known contacts ({len(employee_nodes)}):\n{employee_context}\n\n"
        f"Return JSON matching this schema:\n{json.dumps(COMPANY_SUMMARY_SCHEMA, indent=2)}"
    )

    result = _call_llm(COMPANY_SUMMARY_SYSTEM_PROMPT, user_prompt)

    update_profile(ctx, node_id=str(company_node.pk), new_data={"summary": result})

    return result


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------


def _call_llm(system_prompt: str, user_prompt: str) -> dict[str, object]:
    """Call the configured LLM and return parsed JSON."""
    provider: str = settings.LLM_PROVIDER

    if provider == "anthropic":
        return _call_anthropic(system_prompt, user_prompt)
    elif provider == "openai":
        return _call_openai(system_prompt, user_prompt)
    elif provider == "openrouter":
        return _call_openrouter(system_prompt, user_prompt)
    else:
        raise SummarizationError(f"Unsupported LLM provider: {provider}")


def _call_anthropic(system_prompt: str, user_prompt: str) -> dict[str, object]:
    """Call Anthropic Claude for summarization."""
    import anthropic

    api_key: str = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise SummarizationError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    return _parse_json_response(text)


def _call_openai(system_prompt: str, user_prompt: str) -> dict[str, object]:
    """Call OpenAI for summarization."""
    import openai

    api_key: str = settings.OPENAI_API_KEY
    if not api_key:
        raise SummarizationError("OPENAI_API_KEY not configured")

    client = openai.OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    content = response.choices[0].message.content or ""
    return _parse_json_response(content)


def _call_openrouter(system_prompt: str, user_prompt: str) -> dict[str, object]:
    """Call OpenRouter (OpenAI-compatible) for summarization."""
    import openai

    api_key: str = settings.OPENROUTER_API_KEY
    if not api_key:
        raise SummarizationError("OPENROUTER_API_KEY not configured")

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

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    content = response.choices[0].message.content or ""
    return _parse_json_response(content)


def _parse_json_response(text: str) -> dict[str, object]:
    """Parse JSON from LLM response, handling markdown code fences."""
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (fences)
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise SummarizationError(f"Invalid JSON from LLM: {e}\nRaw: {text[:500]}") from e

    if not isinstance(result, dict):
        raise SummarizationError(f"Expected dict, got {type(result)}")

    return result
