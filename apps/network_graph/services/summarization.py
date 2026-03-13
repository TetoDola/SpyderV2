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

MEETING_SUMMARY_SYSTEM_PROMPT = """\
You are a meeting summarization engine for a personal CRM.

Given raw notes from a meeting and a list of participants with their existing profiles, produce a structured summary that helps the user quickly recall what happened and what they need to do.

Rules:
- one_liner: One sentence capturing THE most important takeaway. Be specific — use names and concrete facts, not generic descriptions.
- key_points: 3-8 bullet points covering what was discussed. Each point should be a self-contained fact that makes sense without reading the full notes. Include names.
- decisions: Only include items where a specific decision was reached. "Discussed X" is not a decision. "Decided to do X" is.
- follow_ups: Action items with the responsible person named. Format: "{Person} {action} {deadline if mentioned}". If the user needs to do something, prefix with "You:".
- Do NOT include pleasantries, small talk, or meta-commentary about the meeting itself.

Return ONLY valid JSON matching this exact schema:

{
  "one_liner": "Discussed Series A timeline with Sarah — agreed to intro her to James at Sequoia",
  "key_points": [
    "Sarah promoted to CTO at Acme Corp six weeks ago",
    "Acme migrating to microservices, hitting distributed tracing pain points"
  ],
  "decisions": [
    "Two-week Clerk POC led by Yuki, Auth0 as fallback"
  ],
  "follow_ups": [
    "You: Send beta access link to Sam by end of week",
    "Sam: Set up Temporal pairing session with Kenji within 3 weeks"
  ]
}
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

PERSON_PROFILE_SYSTEM_PROMPT = """\
You are a personal CRM profile writer. Your job is to maintain a running profile for a person in the user's professional network.

You will receive:
1. The person's EXISTING profile (may be empty if this is the first interaction)
2. NEW interaction context (what just happened involving this person)

Produce an updated profile that merges old and new information.

Rules:
- role: Their current job title and company. If they changed roles, state the current one. Example: "CTO at Acme Corp" or "VP Engineering at Nextera (promoted from Team Lead, ~6 weeks ago)"
- how_we_know_each_other: How the user originally connected with this person. Once established, this should rarely change.
- key_context: 3-7 bullet points of the most important things to know about this person RIGHT NOW. Prioritize: active projects, current goals, recent changes, stated needs. Drop stale context.
- follow_ups_involving_them: Active action items only. Remove completed items. Remove items older than 2 months with no updates.
- MERGE, don't replace. New info supplements existing context. Only remove old info if it's contradicted or stale.
- If existing profile + new context exceeds {max_words} words, compress by dropping the least actionable or oldest items. Never drop role or how_we_know_each_other.
- Write from the user's perspective. "Met at YC Demo Day" not "The user met this person at YC Demo Day."

Return ONLY valid JSON matching this exact schema:

{
  "role": "VP of Engineering at Nextera Systems",
  "how_we_know_each_other": "Met at YC Demo Day 2024, reconnected when he moved to Zurich",
  "last_interaction": "2025-03-09",
  "key_context": [
    "Promoted to VP ~6 weeks ago, now oversees platform, data eng, and DevEx teams",
    "Nextera migrating from monolith to distributed services, using Temporal",
    "Hiring Staff Engineer for platform team, 180-220k CHF"
  ],
  "follow_ups_involving_them": [
    "Send beta access link by end of week",
    "Sam setting up Temporal pairing session with Kenji"
  ]
}
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

Given a company and the profiles of all known contacts at that company, produce a relationship health summary that helps the user understand their position with this organization.

You will receive:
1. Company name and any known properties
2. List of all PERSON profiles connected to this company via WORKS_AT

Rules:
- relationship_health: "strong" / "moderate" / "weak" based on:
  - strong: 3+ contacts, recent interactions (within 1 month), senior-level access, active follow-ups
  - moderate: 1-2 contacts, interactions within 3 months, some engagement
  - weak: 1 contact, no recent interaction, no active follow-ups
- total_contacts: Count of known people at this company.
- key_context: 3-5 bullets summarizing the relationship with this company. Focus on: what stage they're at, what they need, opportunities for the user.
- open_follow_ups: Active action items involving anyone at this company. Aggregate from all person profiles.
- Keep total response under {max_words} words.
- If there's only one contact, the company summary is essentially that person's context scoped to their company role.

Return ONLY valid JSON matching this exact schema:

{
  "company_name": "Nextera Systems",
  "relationship_health": "strong",
  "total_contacts": 4,
  "last_interaction": "2025-03-09",
  "key_context": [
    "Series A closed at 12M CHF, led by Equinox Ventures",
    "Migrating to distributed services architecture, 8 months in",
    "Potential consulting engagement: 2-day/week advisory on developer platform strategy"
  ],
  "open_follow_ups": [
    "Send beta access link to Sam",
    "Marcus scheduling consulting scoping call"
  ]
}
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
        timeout=60.0,
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
        timeout=60.0,
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
        timeout=60.0,
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
