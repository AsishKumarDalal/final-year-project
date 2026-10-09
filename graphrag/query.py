"""Query layer — Q0 … QG (``graphrag`` Track B).

    Q0  probe    embed the question once; the vector is reused by seeds,
                 ranking and fallback
    Q1  route    System-1 choice → local | global | basic | none (D17)
    Q2  seeds    door 1: names  ∪  door 2: vectors
    Q3  expand   2-hop typed walk  ∪  chunk fetch
    Q4  rank     System-1 rerank blended with cosine → top-k
    Q5  narrate  LLM composes from ranked evidence; every claim cited
    Q6  guard    System-1 checks each claim against its chunk
    QG  global   map over community reports → filter → reduce

**No path ends in silence.** Router fails → local. No seeds → vector fallback.
Nothing above threshold → honest refusal with a named reason.

Response contract (PROMPT.md §10 shape, extended):

    {"facts", "source_ids", "source_type": "graphrag",
     "search_mode", "search_mode_confidence"}

``source_ids`` are typed: ``text_unit:<id>`` resolves to a corpus passage;
``community:<id>`` names generated interpretation and must never populate
``source_facts`` (§7.3.6). An id that resolves to neither is a hard failure,
not a warning (§7.3.4).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from .embeddings import cosine
from .system1 import (
    System1Client,
    choice_probability,
    routing_question,
    score_questions,
    score_value,
    support_question,
)

SEARCH_MODES = ("local", "global", "basic", "none")
REFUSAL_OUT_OF_SCOPE = "out_of_scope"
REFUSAL_UNGROUNDABLE = "ungroundable"
REFUSAL_STORES_DOWN = "stores_down"


class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    def search(self, collection: str, vector: Sequence[float], *,
               top_k: int = ..., payload_filter: Mapping[str, Any] | None = ...): ...


@dataclass
class RankedEvidence:
    text: str
    source_id: str          # "text_unit:<chunk>" or "community:<id>"
    kind: str               # "fact" | "chunk" | "report"
    cosine_score: float = 0.0
    system1_score: float = 0.0
    final_score: float = 0.0
    seed_touch: bool = False

    def as_dict(self) -> dict:
        return {"text": self.text, "source_id": self.source_id, "kind": self.kind,
                "score": round(self.final_score, 4)}


@dataclass
class QueryResult:
    facts: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    source_type: str = "graphrag"
    search_mode: str = "local"
    search_mode_confidence: float = 0.0
    generated_interpretation: str = ""
    refusal: str | None = None
    evidence: list[RankedEvidence] = field(default_factory=list)

    def as_dict(self) -> dict:
        payload: dict[str, Any] = {
            "facts": list(self.facts),
            "source_ids": list(self.source_ids),
            "source_type": self.source_type,
            "search_mode": self.search_mode,
            "search_mode_confidence": round(self.search_mode_confidence, 4),
        }
        if self.generated_interpretation:
            payload["generated_interpretation"] = self.generated_interpretation
        if self.refusal:
            payload["refusal"] = self.refusal
        return payload


def normalise_query(text: str) -> list[str]:
    """Tokens for the name door: lowercase alphanumerics, stopwords dropped."""
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    stop = {"what", "which", "who", "when", "where", "how", "does", "do", "is",
            "are", "the", "a", "an", "of", "for", "in", "on", "to", "and", "or",
            "it", "its", "this", "that", "with", "by", "from", "about"}
    return [t for t in tokens if t not in stop and len(t) > 1]


@dataclass
class QueryEngine:
    """All query stages. Stores and models are injected; nothing is global."""

    vector_store: VectorStore
    embedder: Embedder
    system1: System1Client | None = None
    # entity_key -> {"display", "type", "mentions", "aliases"}; the seed index
    entity_index: dict[str, dict[str, Any]] = field(default_factory=dict)
    # chunk_id -> chunk text; provenance for every citation
    chunk_texts: dict[str, str] = field(default_factory=dict)
    # community_id -> {"summary", "member_text_units"}; global search reads these
    community_reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    # adjacency for the walk: entity_key -> [(relation, neighbour, mentions, chunks)]
    adjacency: dict[str, list[tuple[str, str, int, list[str]]]] = field(
        default_factory=dict)
    narrate_fn: Any = None  # (prompt) -> (text, usage); the generative LLM
    top_k_evidence: int = 15
    seed_vector_threshold: float = 0.35
    alpha_cosine: float = 0.5  # blend: final = α·cosine + (1-α)·system1

    # ------------------------------------------------------------- Q0 + Q1
    def probe(self, query: str) -> list[float]:
        return self.embedder.embed([query])[0]

    def route(self, query: str) -> tuple[str, float]:
        """Q1. System-1 routing (D17). Failure defaults to local — the cheaper,
        more precise mode. Fail toward the safe option."""
        if self.system1 is None:
            return "local", 0.0
        try:
            answers = self.system1.decide(query, {"search_mode": routing_question()})
        except Exception:  # noqa: BLE001 — router failure must not fail the query
            return "local", 0.0
        answer = answers.get("search_mode", {})
        probs = answer.get("probabilities", {}) or {}
        best = max(SEARCH_MODES, key=lambda mode: float(probs.get(mode, 0.0)))
        return best, round(float(probs.get(best, 0.0)), 4)

    # ------------------------------------------------------------------ Q2
    def door_names(self, query: str) -> list[str]:
        """Door 1: exact → normalised → substring over the entity index."""
        tokens = normalise_query(query)
        if not tokens:
            return []
        query_blob = " ".join(tokens)
        seeds: list[str] = []
        for key, info in self.entity_index.items():
            surfaces = [key, info.get("display", "").lower()] + [
                a.lower() for a in info.get("aliases", [])]
            for surface in surfaces:
                if not surface:
                    continue
                if surface == query_blob or query_blob in surface or surface in query_blob:
                    seeds.append(key)
                    break
                if any(tok in surface.split() for tok in tokens):
                    seeds.append(key)
                    break
        return sorted(set(seeds))

    def door_vectors(self, probe: Sequence[float], *, top_k: int = 8) -> list[str]:
        """Door 2: cosine over entity embeddings. Catches paraphrases door 1
        cannot see ("the EV maker" → Tesla). Geometry is promiscuous, so the
        threshold gates and the walk still has to prove a path exists."""
        try:
            hits = self.vector_store.search(
                "entities", probe, top_k=top_k)
        except Exception:  # noqa: BLE001 — a down store degrades, never fails
            return []
        return [h.payload.get("entity_key", "") for h in hits
                if h.score >= self.seed_vector_threshold and h.payload.get("entity_key")]

    def seeds(self, query: str, probe: Sequence[float]) -> list[str]:
        """Union of both doors. Doors are for recall; agreement is confidence."""
        return sorted(set(self.door_names(query)) | set(self.door_vectors(probe)))

    # ------------------------------------------------------------------ Q3
    def walk(self, seeds: Sequence[str], *, hops: int = 2,
             limit: int = 300) -> list[RankedEvidence]:
        """Typed 2-hop walk. The structural guarantee: if a connection exists
        within k hops, it is in the evidence."""
        seen: set[tuple[str, str, str]] = set()
        evidence: list[RankedEvidence] = []
        frontier = list(seeds)
        visited = set(seeds)
        for _ in range(max(1, hops)):
            next_frontier: list[str] = []
            for node in frontier:
                for relation, neighbour, mentions, chunks in self.adjacency.get(node, []):
                    key = (node, relation, neighbour)
                    if key not in seen:
                        seen.add(key)
                        for chunk in chunks:
                            text = self.chunk_texts.get(chunk, "")
                            evidence.append(RankedEvidence(
                                text=f"{node} --{relation}--> {neighbour}"
                                     + (f" | {text[:400]}" if text else ""),
                                source_id=f"text_unit:{chunk}",
                                kind="fact",
                                seed_touch=(node in seeds or neighbour in seeds),
                            ))
                    if neighbour not in visited:
                        visited.add(neighbour)
                        next_frontier.append(neighbour)
            frontier = next_frontier
            if len(evidence) >= limit:
                break
        return evidence[:limit]

    def fetch_chunks(self, probe: Sequence[float], *, top_k: int = 10) -> list[RankedEvidence]:
        """Vector fallback over chunk embeddings: vanilla RAG inside GraphRAG.
        No multi-hop reasoning, but still cited."""
        try:
            hits = self.vector_store.search("text_units", probe, top_k=top_k)
        except Exception:  # noqa: BLE001
            return []
        evidence = []
        for hit in hits:
            chunk_id = hit.payload.get("text_unit_id", "")
            text = self.chunk_texts.get(chunk_id, "")
            if not chunk_id or not text:
                continue
            evidence.append(RankedEvidence(
                text=text[:1200], source_id=f"text_unit:{chunk_id}",
                kind="chunk", cosine_score=hit.score))
        return evidence

    # ------------------------------------------------------------------ Q4
    def rank(self, query: str, probe: Sequence[float],
             evidence: Sequence[RankedEvidence]) -> list[RankedEvidence]:
        """Seed-priority + cosine, then the System-1 rerank blended in.
        The anti-'lost in the middle' step: the right fact enters the prompt
        first, not buried at position 47."""
        ranked = list(evidence)
        for item in ranked:
            item.cosine_score = round(cosine(list(probe),
                                             self.embedder.embed([item.text])[0]), 4)
            item.final_score = item.cosine_score
        ranked.sort(key=lambda item: (item.seed_touch, item.cosine_score), reverse=True)
        candidates = ranked[: min(len(ranked), 60)]
        if self.system1 is not None and candidates:
            try:
                questions = score_questions(
                    query, [{"text": c.text} for c in candidates][:50])
                answers = self.system1.decide(
                    "Score how likely each candidate is to answer the query.",
                    questions)
                for index, item in enumerate(candidates[:50]):
                    item.system1_score = round(
                        score_value(answers.get(f"cand_{index}", {})), 4)
                    item.final_score = round(
                        self.alpha_cosine * item.cosine_score
                        + (1.0 - self.alpha_cosine) * item.system1_score, 4)
            except Exception:  # noqa: BLE001 — degrade to cosine, never to nonsense
                for item in candidates:
                    item.final_score = item.cosine_score
        ranked.sort(key=lambda item: (item.final_score, item.seed_touch), reverse=True)
        return ranked[: self.top_k_evidence]

    # ------------------------------------------------------------- Q5 + Q6
    def narrate(self, query: str, evidence: Sequence[RankedEvidence],
                *, mode: str) -> tuple[str, list[str]]:
        """Q5. The narrator only narrates: the walk already found the
        connection, so a small job needs only a small, cited answer."""
        if self.narrate_fn is None:
            lines = [f"- {item.text[:300]} [{item.source_id}]" for item in evidence[:8]]
            return ("Evidence:\n" + "\n".join(lines), [i.source_id for i in evidence[:8]])
        bullets = "\n".join(
            f"[{item.source_id}] {item.text[:600]}" for item in evidence)
        prompt = (
            f"Question ({mode} search): {query}\n\nEvidence:\n{bullets}\n\n"
            "Answer from the evidence only. Every factual sentence must end with "
            "its [source id]. If the evidence does not contain the answer, say so "
            "and cite nothing. No diagnosis, no treatment advice. "
            "Return ONLY valid JSON: "
            '{"answer":"...","cited_ids":["text_unit:..."]}')
        text, _usage = self.narrate_fn(prompt)
        try:
            payload = json.loads(text[text.find("{"): text.rfind("}") + 1])
            return str(payload.get("answer", "")), list(payload.get("cited_ids", []))
        except (json.JSONDecodeError, ValueError, AttributeError):
            return text, [item.source_id for item in evidence]

    def guard(self, answer: str, evidence: Sequence[RankedEvidence]) -> float:
        """Q6/U8. Per-claim support against cited chunks. Annotates in v1 —
        it must earn blocking power by measurement, not by assertion."""
        if self.system1 is None or not answer.strip():
            return 1.0
        by_source = {item.source_id: item.text for item in evidence}
        claims = [c.strip() for c in re.split(r"(?<=[.!?])\s+", answer) if c.strip()][:6]
        if not claims:
            return 1.0
        scores: list[float] = []
        for claim in claims:
            chunk_text = ""
            for source_id in by_source:
                if source_id in claim or source_id in answer:
                    chunk_text = by_source[source_id]
                    break
            if not chunk_text:
                chunk_text = " ".join(by_source.values())[:2000]
            try:
                answers = self.system1.decide(
                    "Check claim support.",
                    {"support": support_question(claim, chunk_text)})
                scores.append(choice_probability(
                    answers.get("support", {}), "supported"))
            except Exception:  # noqa: BLE001 — an unjudgeable claim scores 0
                scores.append(0.0)
        return round(sum(scores) / len(scores), 4) if scores else 1.0

    # ------------------------------------------------------------------ QG
    def global_search(self, query: str, probe: Sequence[float], *,
                      top_communities: int = 6) -> QueryResult:
        """Map over community summaries → filter → reduce. The expensive path,
        and the reason community pruning (U6) exists."""
        if not self.community_reports:
            return self._refuse("global", 0.0, REFUSAL_UNGROUNDABLE,
                                "no community reports indexed")
        scored: list[tuple[float, str]] = []
        for community_id, report in self.community_reports.items():
            summary = report.get("summary", "")
            if not summary:
                continue
            scored.append((cosine(probe, self.embedder.embed([summary])[0]),
                           community_id))
        scored.sort(reverse=True)
        picked = scored[:top_communities]
        member_units: list[str] = []
        context_lines: list[str] = []
        for score, community_id in picked:
            report = self.community_reports[community_id]
            context_lines.append(f"[{community_id}] {report.get('summary','')[:800]}")
            for unit in report.get("member_text_units", [])[:6]:
                if unit not in member_units:
                    member_units.append(unit)
        interpretation = (
            "Overview across communities "
            + ", ".join(c for _, c in picked) + ":\n" + "\n".join(context_lines))
        return QueryResult(
            facts=[],
            source_ids=[f"community:{c}" for _, c in picked],
            search_mode="global",
            search_mode_confidence=0.0,
            generated_interpretation=interpretation,
            evidence=[RankedEvidence(
                text=self.chunk_texts.get(u, "")[:600], source_id=f"text_unit:{u}",
                kind="chunk") for u in member_units[: self.top_k_evidence]],
        )

    # -------------------------------------------------------------- assemble
    def ask(self, query: str, *, mode: str | None = None) -> QueryResult:
        """One entry point. Mode may be forced (tests) or routed (Q1)."""
        try:
            probe = self.probe(query)
        except Exception:  # noqa: BLE001 — no embeddings, no retrieval
            return self._refuse("basic", 0.0, REFUSAL_STORES_DOWN,
                                "embedding backend unavailable")
        if mode is None:
            mode, confidence = self.route(query)
        else:
            confidence = 1.0
        if mode == "none":
            return self._refuse(mode, confidence, REFUSAL_OUT_OF_SCOPE,
                                "not a medical information question")
        if mode == "global":
            result = self.global_search(query, probe)
            result.search_mode_confidence = confidence
            return result
        if mode == "basic":
            evidence = self.fetch_chunks(probe, top_k=self.top_k_evidence)
            if not evidence:
                return self._refuse(mode, confidence, REFUSAL_UNGROUNDABLE,
                                    "nothing above threshold")
            ranked = self.rank(query, probe, evidence)
            answer, cited = self.narrate(query, ranked, mode=mode)
            return QueryResult(facts=[answer], source_ids=cited, search_mode=mode,
                               search_mode_confidence=confidence, evidence=ranked)
        # local (default): walk ∪ fetch, then rank — both are free, so run both.
        seed_list = self.seeds(query, probe)
        walked = self.walk(seed_list) if seed_list else []
        fetched = self.fetch_chunks(probe, top_k=10)
        combined = walked + [e for e in fetched
                             if e.source_id not in {w.source_id for w in walked}]
        if not combined:
            return self._refuse(mode, confidence, REFUSAL_UNGROUNDABLE,
                                "cannot find this in the corpus")
        ranked = self.rank(query, probe, combined)
        answer, cited = self.narrate(query, ranked, mode=mode)
        support = self.guard(answer, ranked)
        if support < 0.5:
            return self._refuse(mode, confidence, REFUSAL_UNGROUNDABLE,
                                f"answer support {support} below 0.5")
        return QueryResult(facts=[answer], source_ids=cited, search_mode=mode,
                           search_mode_confidence=confidence, evidence=ranked)

    def _refuse(self, mode: str, confidence: float, reason: str,
                detail: str) -> QueryResult:
        return QueryResult(facts=[], source_ids=[], search_mode=mode,
                           search_mode_confidence=confidence, refusal=f"{reason}: {detail}")


def validate_source_ids(result: QueryResult,
                        known_text_units: set[str]) -> list[str]:
    """Hard-failure check (§7.3.4): every ``text_unit:`` id must resolve to a
    real corpus passage. ``community:`` ids are generated interpretation and
    must never appear in ``source_ids`` meant as facts."""
    problems: list[str] = []
    for source_id in result.source_ids:
        if source_id.startswith("text_unit:"):
            if source_id[len("text_unit:"):] not in known_text_units:
                problems.append(f"unresolvable: {source_id}")
        elif source_id.startswith("community:"):
            problems.append(f"generated text cited as fact: {source_id}")
        else:
            problems.append(f"malformed source id: {source_id}")
    return problems