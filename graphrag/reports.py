"""Community reports (ingestion, after I10) and the global-search reader.

Bottom-up map: leaf communities are summarised from their members' edges
(prioritised by combined endpoint degree, filling a token budget); higher
levels summarise from sub-community reports, substituting shorter summaries
when the context overflows. Reports are embedded only after they are final —
they are a query-time artefact, so embedding earlier wastes work.

Reports are **generated interpretation**, never `source_facts` (PROMPT.md
§7.3.6): a citation may point at a community's *member text units*, never at
the report prose (Plan D20).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .communities import Community

REPORT_TOKEN_BUDGET = 4000


@dataclass
class CommunityReport:
    community_id: str
    level: int
    title: str
    summary: str
    member_text_units: list[str]
    model_id: str = ""

    def as_dict(self) -> dict:
        return {
            "community_id": self.community_id,
            "level": self.level,
            "title": self.title,
            "summary": self.summary,
            "member_text_units": list(self.member_text_units),
            "model_id": self.model_id,
        }


def _truncate(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    return text[:budget].rsplit(" ", 1)[0]


def build_report_context(
    community: Community,
    *,
    member_lines: Sequence[str],
    sub_reports: Sequence[CommunityReport] = (),
    budget: int = REPORT_TOKEN_BUDGET,
) -> str:
    """Assemble the map-step input. Member edges first (most specific), then
    sub-community summaries — shortest first when space runs out."""
    lines = [f"Community {community.id} ({len(community.members)} entities)."]
    lines.append("Member facts (prioritise high-mention edges):")
    lines.extend(member_lines)
    if sub_reports:
        lines.append("Sub-community summaries:")
        ordered = sorted(sub_reports, key=lambda r: len(r.summary))
        for report in ordered:
            lines.append(f"- [{report.community_id}] {report.summary}")
    return _truncate("\n".join(lines), budget * 4)


def report_prompt(community: Community, context: str) -> str:
    return (
        "Summarise the community below as 3-6 sentences: what these entities "
        "have in common, the main relationships between them, and what is "
        "notable. Plain statements only, no advice, no diagnosis. "
        "Return ONLY valid JSON: "
        '{"title":"...","summary":"..."}'
        f"\n\nCommunity {community.id}:\n{context}"
    )


def parse_report(content: str) -> tuple[str, str]:
    from .llm import strip_json_fences
    cleaned = strip_json_fences(content)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in report completion")
    payload = json.loads(cleaned[start : end + 1])
    return str(payload.get("title", "")), str(payload.get("summary", ""))


def member_fact_lines(
    community: Community,
    edges: Sequence[Mapping[str, Any]],
    displays: Mapping[str, str],
    *,
    limit: int = 60,
) -> list[str]:
    """Edges touching the community, ordered by combined endpoint degree —
    the prioritisation GraphRAG's leaf step requires."""
    degree: dict[str, int] = {}
    for edge in edges:
        degree[edge["head"]] = degree.get(edge["head"], 0) + int(edge.get("mentions", 1))
        degree[edge["tail"]] = degree.get(edge["tail"], 0) + int(edge.get("mentions", 1))
    members = set(community.members)
    scored: list[tuple[int, str]] = []
    for edge in edges:
        if edge["head"] in members or edge["tail"] in members:
            weight = degree.get(edge["head"], 0) + degree.get(edge["tail"], 0)
            head = displays.get(edge["head"], edge["head"])
            tail = displays.get(edge["tail"], edge["tail"])
            scored.append((weight, f"{head} --{edge['relation']}--> {tail}"))
    scored.sort(key=lambda item: -item[0])
    return [line for _, line in scored[:limit]]


def member_text_units(
    community: Community, edges: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Every text unit behind the community's edges — the *citable* half of a
    report. Citations resolve to these, never to the summary prose."""
    members = set(community.members)
    units: list[str] = []
    for edge in edges:
        if edge["head"] in members or edge["tail"] in members:
            for chunk in edge.get("source_chunks", []):
                if chunk not in units:
                    units.append(chunk)
    return units