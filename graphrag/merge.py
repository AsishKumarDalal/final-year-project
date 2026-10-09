"""Entity resolution — the merge funnel (ingestion stage I9, ``graphrag`` Track B).

Transcribed from ``docs/rag_docs/solution.md``: the funnel that fixed the real
"Tesla problem" (``Tesla, Inc.`` / ``Tesla`` / ``Tesla Motors, Inc.`` as three
nodes for one company).

    ① BLOCKING   bucket names by shared token (cap 60/bucket — the quadratic
                 monster stays out)
    ② FUZZY      0.6·Jaro-Winkler + 0.4·token-overlap ≥ 0.5 NOMINATES ONLY —
                 it never merges anything
    ③ CLUSTER    union-find chains nominations into small batches
    ④ JUDGE      System-1 (Laya) scores each batch with graph evidence;
                 below τ_low the generative LLM adjudicates the tail only
    ⑤ CANONICAL  code picks: most mentions wins, tie → longest name.
                 Never the model (it once picked "Eberhard" over
                 "Martin Eberhard")
    ⑥ REBUILD    rewrite every edge through the key map; mentions summed,
                 source_chunks unioned; audit written to merge_log.json

The load-bearing rule: **cheap layers nominate, expensive layers decide.**
Strings can nominate ("tesla" ~ "tesla motors" at 0.73) but cannot decide —
"Motors" (noise: former name) vs "Energy" (meaning: real division) is world
knowledge, not character similarity. And **over-merging is worse than
under-merging**: two fragments cost edge weight; one false merge poisons every
future traversal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .graph import Graph
from .system1 import System1Client, choice_probability, merge_question

NOMINATE_THRESHOLD = 0.5
BUCKET_CAP = 60
MAX_BATCH = 15
TAU_HIGH = 0.85  # at/above: merge
TAU_LOW = 0.60  # below: escalate to the LLM judge
FUZZY_JW_WEIGHT = 0.6
FUZZY_OVERLAP_WEIGHT = 0.4


# ------------------------------------------------------------ string metrics
def jaro_winkler(first: str, second: str) -> float:
    """Pure-python Jaro-Winkler. No dependency for a 30-line function."""
    s1, s2 = first or "", second or ""
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    len1, len2 = len(s1), len(s2)
    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0
    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(len2, i + match_distance + 1)
        for j in range(start, end):
            if s2_matches[j]:
                continue
            if s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    transpositions //= 2
    jaro = (
        matches / len1 + matches / len2 + (matches - transpositions) / matches
    ) / 3.0
    prefix = 0
    for a, b in zip(s1, s2):
        if a != b or prefix == 4:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1.0 - jaro)


def fuzzy_score(first: str, second: str) -> float:
    """0.6·Jaro-Winkler + 0.4·token-overlap, per solution.md §4."""
    tokens1 = set(first.split())
    tokens2 = set(second.split())
    overlap = (
        len(tokens1 & tokens2) / len(tokens1 | tokens2) if (tokens1 | tokens2) else 0.0
    )
    return FUZZY_JW_WEIGHT * jaro_winkler(first, second) + FUZZY_OVERLAP_WEIGHT * overlap


# ------------------------------------------------------------------ blocking
def block_candidates(keys: Sequence[str]) -> list[tuple[str, str]]:
    """Bucket keys by shared token; emit pairs only within a bucket.

    Two names sharing no token ("Musk" vs "Elon") are never compared — blocking
    buys speed with a little recall, which is fine because blocking only needs
    the obvious candidates.
    """
    buckets: dict[str, list[str]] = {}
    for key in keys:
        for token in set(key.split()):
            bucket = buckets.setdefault(token, [])
            if len(bucket) < BUCKET_CAP and key not in bucket:
                bucket.append(key)
    pairs: set[tuple[str, str]] = set()
    for bucket in buckets.values():
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                pairs.add(tuple(sorted((bucket[i], bucket[j]))))
    return sorted(pairs)


def nominate(
    keys: Sequence[str], *, threshold: float = NOMINATE_THRESHOLD
) -> list[tuple[str, str, float]]:
    """Fuzzy-nominate candidate duplicate pairs. Merges nothing."""
    nominations: list[tuple[str, str, float]] = []
    for first, second in block_candidates(keys):
        if first == second:
            continue
        score = fuzzy_score(first, second)
        if score >= threshold:
            nominations.append((first, second, round(score, 4)))
    return nominations


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        root = self.parent.setdefault(item, item)
        while self.parent[root] != root:
            self.parent[root] = self.parent[self.parent[root]]
            root = self.parent[root]
        return root

    def union(self, first: str, second: str) -> None:
        root1, root2 = self.find(first), self.find(second)
        if root1 != root2:
            self.parent[root2] = root1

    def groups(self) -> list[list[str]]:
        clusters: dict[str, list[str]] = {}
        for item in self.parent:
            clusters.setdefault(self.find(item), []).append(item)
        return [sorted(members) for members in clusters.values() if len(members) > 1]


def cluster_nominations(
    nominations: Iterable[tuple[str, str, float]], *, max_batch: int = MAX_BATCH
) -> list[list[str]]:
    """Chain nominations into judge batches, each capped at ``max_batch``."""
    union = UnionFind()
    for first, second, _ in nominations:
        union.union(first, second)
    batches: list[list[str]] = []
    for group in union.groups():
        for start in range(0, len(group), max_batch):
            batches.append(group[start : start + max_batch])
    return batches


# --------------------------------------------------------------------- judge
@dataclass
class MergeVerdict:
    groups: list[list[str]]
    refused: list[str]
    judge: str  # "system1" | "llm" | "none"
    probabilities: dict[str, float] = field(default_factory=dict)


def entity_context(graph: Graph, key: str) -> str:
    """Render one entity with its type, mentions and real edges — the evidence
    that turned a sycophantic string-matcher into a reliable adjudicator."""
    node = graph.nodes.get(key)
    if node is None:
        return f'"{key}" (unknown)'
    edge_bits: list[str] = []
    for edge in graph.edges.values():
        if edge.head == key:
            edge_bits.append(
                f"{edge.relation} -> {graph.nodes[edge.tail].display} (x{edge.mentions})"
            )
        elif edge.tail == key:
            edge_bits.append(
                f"<- {edge.relation} from {graph.nodes[edge.head].display} "
                f"(x{edge.mentions})"
            )
    edges = "; ".join(edge_bits[:8]) or "no edges"
    aliases = f" aliases: {sorted(node.aliases)[:4]}" if node.aliases else ""
    return f'"{node.display}" (type={node.type}, mentions={node.mentions}{aliases}) | {edges}'


JudgeFn = Callable[[list[str], Graph], MergeVerdict]


def system1_judge(
    client: System1Client,
    *,
    tau_high: float = TAU_HIGH,
    tau_low: float = TAU_LOW,
) -> JudgeFn:
    """Build the Laya judge: one ``choice`` question per candidate pair,
    all pairs of a batch answered in one call."""

    def judge(batch: list[str], graph: Graph) -> MergeVerdict:
        if len(batch) < 2:
            return MergeVerdict(groups=[], refused=list(batch), judge="system1")
        questions: dict[str, dict] = {}
        pairs: list[tuple[str, str]] = []
        for i in range(len(batch)):
            for j in range(i + 1, len(batch)):
                first, second = batch[i], batch[j]
                pairs.append((first, second))
                questions[f"pair_{len(pairs)}"] = merge_question(
                    [first, second],
                    context_lines=[
                        entity_context(graph, first),
                        entity_context(graph, second),
                    ],
                )
        answers = client.decide(
            "Decide which candidate name pairs refer to the same entity.", questions
        )
        union = UnionFind()
        probabilities: dict[str, float] = {}
        refused: list[str] = []
        for index, (first, second) in enumerate(pairs, start=1):
            answer = answers.get(f"pair_{index}", {})
            prob = choice_probability(answer, "merge")
            probabilities[f"{first} ~ {second}"] = round(prob, 4)
            if prob >= tau_high:
                union.union(first, second)
            elif prob < tau_low:
                refused.extend([first, second])
            else:
                # Ambiguous middle: merge AND log — that band is where the
                # interesting failures live (system-1-model.md U3 gate).
                union.union(first, second)
        groups = union.groups()
        merged_names = {name for group in groups for name in group}
        refused = sorted(set(refused) - merged_names)
        return MergeVerdict(
            groups=groups, refused=refused, judge="system1", probabilities=probabilities
        )

    return judge


def conservative_judge(batch: list[str], graph: Graph) -> MergeVerdict:
    """No-model fallback and test double: refuse everything.

    Bias is deliberate — a missed merge costs edge weight, a wrong merge
    poisons traversals.
    """
    return MergeVerdict(groups=[], refused=list(batch), judge="none")


# -------------------------------------------------------------------- rebuild
def pick_canonical(graph: Graph, members: Sequence[str]) -> str:
    """Most mentions wins; tie → longest display name. Never the model."""
    scored = [
        (graph.nodes[m].mentions if m in graph.nodes else 0,
         len(graph.nodes[m].display) if m in graph.nodes else 0,
         m)
        for m in members
    ]
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


@dataclass
class MergeReport:
    merged_nodes: int = 0
    groups: list[list[str]] = field(default_factory=list)
    canonical: dict[str, str] = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)
    probabilities: dict[str, float] = field(default_factory=dict)
    judges_used: list[str] = field(default_factory=list)


def apply_verdict(graph: Graph, verdict: MergeVerdict) -> dict[str, str]:
    """Rewrite edges through the verdict's key map; fold mentions, aliases,
    source chunks and descriptions. Returns the key map for the audit log."""
    key_map: dict[str, str] = {}
    for group in verdict.groups:
        canonical = pick_canonical(graph, group)
        for member in group:
            if member != canonical:
                key_map[member] = canonical
    if not key_map:
        return {}
    new_edges: dict[tuple[str, str, str], Any] = {}
    for (head, relation, tail), edge in list(graph.edges.items()):
        new_head = key_map.get(head, head)
        new_tail = key_map.get(tail, tail)
        if new_head == new_tail:
            continue  # a merge turned this edge into a self-loop: drop it
        new_key = (new_head, relation, new_tail)
        existing = new_edges.get(new_key)
        if existing is None:
            edge.head, edge.tail = new_head, new_tail
            new_edges[new_key] = edge
        else:
            existing.mentions += edge.mentions
            existing.source_chunks |= edge.source_chunks
            for description in edge.descriptions:
                if description not in existing.descriptions:
                    existing.descriptions.append(description)
    graph.edges = new_edges
    for old_key, canonical in key_map.items():
        node = graph.nodes.pop(old_key, None)
        if node is None:
            continue
        target = graph.nodes.get(canonical)
        if target is None:
            node.key = canonical
            graph.nodes[canonical] = node
            continue
        target.mentions += node.mentions
        target.aliases.add(node.display)
        target.aliases |= node.aliases
        if len(node.display) > len(target.display):
            target.aliases.add(target.display)
            target.display = node.display
    return key_map


def resolve_entities(
    graph: Graph,
    *,
    judge: JudgeFn,
    threshold: float = NOMINATE_THRESHOLD,
    audit_path: Path | None = None,
) -> MergeReport:
    """Run the full funnel. Every nomination, verdict and refusal is logged."""
    report = MergeReport()
    keys = sorted(graph.nodes)
    nominations = nominate(keys, threshold=threshold)
    batches = cluster_nominations(nominations)
    audit: list[dict[str, Any]] = []
    for batch in batches:
        verdict = judge(batch, graph)
        key_map = apply_verdict(graph, verdict)
        report.judges_used.append(verdict.judge)
        report.probabilities.update(verdict.probabilities)
        report.refused.extend(verdict.refused)
        for group in verdict.groups:
            report.groups.append(group)
            canonical = pick_canonical(graph, group)
            for member in group:
                if member != canonical:
                    report.canonical[member] = canonical
        audit.append(
            {
                "batch": batch,
                "nominations": [
                    {"pair": [a, b], "score": s}
                    for a, b, s in nominations
                    if a in batch and b in batch
                ],
                "verdict": verdict.groups,
                "refused": verdict.refused,
                "judge": verdict.judge,
                "probabilities": verdict.probabilities,
                "key_map": key_map,
            }
        )
    report.merged_nodes = len(report.canonical)
    report.refused = sorted(set(report.refused))
    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return report