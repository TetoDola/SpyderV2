"""Entity resolution service.

Resolves extracted entities against existing graph nodes.
Separate cascades for PERSON and COMPANY entities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.network_graph.dsl import DSLContext, create_node, flag_for_review
from apps.network_graph.models import Node

logger = logging.getLogger(__name__)

# Only auto-link at confidence >= this threshold
AUTO_LINK_THRESHOLD = 0.9


@dataclass
class ResolvedEntity:
    """Result of resolving a single extracted entity."""

    name: str
    node_id: str
    node_type: str
    confidence: float
    auto_linked: bool
    is_new: bool


def resolve_people(
    ctx: DSLContext,
    people: list[dict[str, object]],
) -> list[ResolvedEntity]:
    """Resolve extracted people against existing PERSON nodes.

    Cascade:
    1. Email exact match              → auto-link (confidence 1.0)
    2. Phone exact match              → auto-link (confidence 0.95)
    3. Name exact + same company prop → queue for confirmation (confidence 0.7)
    4. Name exact, no company context → queue for confirmation (confidence 0.5)
    5. No match                       → create ghost PERSON node
    """
    resolved: list[ResolvedEntity] = []

    for person in people:
        name = str(person.get("name", "")).strip()
        email = str(person.get("email", "") or "").strip()
        company = str(person.get("company", "") or "").strip()
        title = str(person.get("title", "") or "").strip()

        if not name:
            continue

        result = _resolve_person(ctx, name, email, company, title)
        resolved.append(result)

    return resolved


def _resolve_person(
    ctx: DSLContext,
    name: str,
    email: str,
    company: str,
    title: str,
) -> ResolvedEntity:
    """Run the person resolution cascade."""

    # 1. Email exact match → auto-link
    if email:
        match = Node.objects.filter(
            node_type="PERSON",
            properties__Email=email,
        ).first()
        if match:
            return ResolvedEntity(
                name=name,
                node_id=str(match.pk),
                node_type="PERSON",
                confidence=1.0,
                auto_linked=True,
                is_new=False,
            )

    # 2. Phone exact match → auto-link
    # (skipped if no phone — extraction doesn't currently pull phone numbers)

    # 3. Name exact match + same company → queue
    name_matches = list(Node.objects.filter(title__iexact=name, node_type="PERSON"))

    if name_matches and company:
        for match in name_matches:
            match_company = ""
            if isinstance(match.properties, dict):
                match_company = str(match.properties.get("Company", "")).strip()
            if match_company.lower() == company.lower():
                # Queue for confirmation — same name + same company, likely match
                flag_for_review(
                    ctx,
                    node_id=str(match.pk),
                    reason=f"Name + company match: {name} at {company}",
                    extracted_name=name,
                    extracted_email=email,
                    extracted_company=company,
                    extracted_title=title,
                    confidence=0.7,
                )
                return ResolvedEntity(
                    name=name,
                    node_id=str(match.pk),
                    node_type="PERSON",
                    confidence=0.7,
                    auto_linked=False,
                    is_new=False,
                )

    # 4. Name exact match, no company context → queue
    if name_matches:
        match = name_matches[0]
        flag_for_review(
            ctx,
            node_id=str(match.pk),
            reason=f"Name-only match: {name}",
            extracted_name=name,
            extracted_email=email,
            extracted_company=company,
            extracted_title=title,
            confidence=0.5,
        )
        return ResolvedEntity(
            name=name,
            node_id=str(match.pk),
            node_type="PERSON",
            confidence=0.5,
            auto_linked=False,
            is_new=False,
        )

    # 5. No match → create ghost node
    ghost = create_node(
        ctx,
        node_type="PERSON",
        title=name,
        properties={
            "Email": email,
            "Company": company,
            "Title": title,
        },
        is_ghost=True,
    )
    return ResolvedEntity(
        name=name,
        node_id=str(ghost.pk),
        node_type="PERSON",
        confidence=0.0,
        auto_linked=False,
        is_new=True,
    )


def resolve_companies(
    ctx: DSLContext,
    companies: list[dict[str, object]],
) -> list[ResolvedEntity]:
    """Resolve extracted companies against existing COMPANY nodes.

    Cascade:
    1. Name exact match (case-insensitive) → auto-link (confidence 1.0)
    2. Website exact match                  → auto-link (confidence 0.95)
    3. No match                             → create COMPANY node (not ghost)
    """
    resolved: list[ResolvedEntity] = []

    for company in companies:
        name = str(company.get("name", "")).strip()
        website = str(company.get("website", "") or "").strip()
        industry = str(company.get("industry", "") or "").strip()

        if not name:
            continue

        result = _resolve_company(ctx, name, website, industry)
        resolved.append(result)

    return resolved


def _resolve_company(
    ctx: DSLContext,
    name: str,
    website: str,
    industry: str,
) -> ResolvedEntity:
    """Run the company resolution cascade."""

    # 1. Name exact match (case-insensitive)
    match = Node.objects.filter(
        title__iexact=name,
        node_type="COMPANY",
    ).first()
    if match:
        return ResolvedEntity(
            name=name,
            node_id=str(match.pk),
            node_type="COMPANY",
            confidence=1.0,
            auto_linked=True,
            is_new=False,
        )

    # 2. Website exact match
    if website:
        match = Node.objects.filter(
            node_type="COMPANY",
            properties__Website=website,
        ).first()
        if match:
            return ResolvedEntity(
                name=name,
                node_id=str(match.pk),
                node_type="COMPANY",
                confidence=0.95,
                auto_linked=True,
                is_new=False,
            )

    # 3. No match → create real COMPANY node (not ghost — lower risk)
    props: dict[str, str] = {}
    if website:
        props["Website"] = website
    if industry:
        props["Industry"] = industry

    node = create_node(
        ctx,
        node_type="COMPANY",
        title=name,
        properties=props,
        is_ghost=False,
    )
    return ResolvedEntity(
        name=name,
        node_id=str(node.pk),
        node_type="COMPANY",
        confidence=1.0,
        auto_linked=True,
        is_new=True,
    )
