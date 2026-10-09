"""The property graph, its validation gate, and aggregation.

Transcribed from ``docs/rag_docs/graph_making.md`` §6 ("Graph construction").

The data model is two dictionaries:

    nodes[key] = {display, type, mentions, aliases}
    edges[(head_key, RELATION, tail_key)] = {
        head, relation, tail, mentions, source_chunks, descriptions
    }

Three properties carried straight over from the design record:

* **The gate.** Every triple the LLM produces passes through validation,
  normalisation and aggregation before it exists. LLM output is a suggestion,
  not data.
* **Mention counts are free confidence.** Real relationships get re-extracted
  across documents and accumulate mentions; hallucinated or inverted junk tends
  to appear once. ``prune_min_mentions`` exploits that.
* **Every edge keeps its source chunks.** That list is what makes a citation
  resolve back to real corpus text (PROMPT.md §7.3.4) — and the reason a
  generated description can never satisfy a citation (Plan.md D20).

``add_triple`` returns a machine-readable status string rather than a bool so
every rejection is counted and auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .textnorm import (
    display_name,
    normalize_entity,
    normalize_entity_type,
    normalize_relation,
    split_compound,
    validate_entity_name,
    validate_triple,
)


@dataclass
class Node:
    key: str
    display: str
    type: str = "condition"
    mentions: int = 0
    aliases: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "display": self.display,
            "type": self.type,
            "mentions": self.mentions,
            "aliases": sorted(self.aliases),
        }


@dataclass
class Edge:
    head: str
    relation: str
    tail: str
    mentions: int = 0
    source_chunks: set[str] = field(default_factory=set)
    descriptions: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.head, self.relation, self.tail)

    def to_dict(self) -> dict:
        return {
            "head": self.head,
            "relation": self.relation,
            "tail": self.tail,
            "mentions": self.mentions,
            "source_chunks": sorted(self.source_chunks),
            "descriptions": list(self.descriptions),
        }


@dataclass(frozen=True)
class AddResult:
    """Outcome of one ``add_triple`` call."""

    status: str  # added | merged | rejected
    reason: str | None = None
    edge_key: tuple[str, str, str] | None = None


class Graph:
    """Entities, typed relationships, and the provenance behind both."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}
        self.rejected: dict[str, int] = {}

    # -------------------------------------------------------------- ingest
    def upsert_node(self, surface: str, node_type: str | None = None) -> Node:
        """Create or update the node for an entity surface form.

        The merge key is the normalised form; the display name keeps the full
        surface so the graph still reads like the source text.
        """
        key = normalize_entity(surface)
        if not key:
            raise ValueError(f"cannot normalise entity surface: {surface!r}")
        node = self.nodes.get(key)
        if node is None:
            node = Node(key=key, display=display_name(surface))
            self.nodes[key] = node
        surface_display = display_name(surface)
        if not surface_display:
            return node
        # The longest surface wins the display slot; every other surface form is
        # kept as an alias. Display must not flap between "Tesla, Inc." and
        # "Tesla" depending on which chunk arrived last — solution.md records
        # that a hub displayed under the wrong name misleads every reader.
        if len(surface_display) > len(node.display):
            if node.display and node.display != surface_display:
                node.aliases.add(node.display)
            node.display = surface_display
        elif surface_display != node.display:
            node.aliases.add(surface_display)
        if node_type:
            candidate = normalize_entity_type(node_type)
            if node.type == "condition" and candidate != "condition":
                node.type = candidate
        return node

    def add_triple(
        self,
        head: str,
        relation: str,
        tail: str,
        *,
        source_chunk: str | None = None,
        description: str | None = None,
        max_mention_tokens: int = 7,
    ) -> AddResult:
        """Validate, normalise and aggregate one triple.

        A compound name (``"A and B"``) is expanded into two triples rather than
        rejected: the extraction is not wrong, it is compressed.
        """
        surfaces = split_compound(head) or [head]
        tails = split_compound(tail) or [tail]
        if len(surfaces) > 1 or len(tails) > 1:
            results: list[AddResult] = []
            for h in surfaces:
                for t in tails:
                    results.append(
                        self.add_triple(
                            h,
                            relation,
                            t,
                            source_chunk=source_chunk,
                            description=description,
                            max_mention_tokens=max_mention_tokens,
                        )
                    )
            added = [r for r in results if r.status in ("added", "merged")]
            if added:
                return added[0]
            return results[0]

        reason = validate_triple(
            head, relation, tail, max_mention_tokens=max_mention_tokens
        )
        if reason:
            self.rejected[reason] = self.rejected.get(reason, 0) + 1
            return AddResult(status="rejected", reason=reason)

        head_node = self.upsert_node(head)
        tail_node = self.upsert_node(tail)
        rel = normalize_relation(relation)
        if not rel:
            self.rejected["empty_relation"] = self.rejected.get("empty_relation", 0) + 1
            return AddResult(status="rejected", reason="empty_relation")

        key = (head_node.key, rel, tail_node.key)
        edge = self.edges.get(key)
        if edge is None:
            edge = Edge(head=head_node.key, relation=rel, tail=tail_node.key)
            self.edges[key] = edge
            status = "added"
        else:
            status = "merged"

        edge.mentions += 1
        if source_chunk:
            edge.source_chunks.add(source_chunk)
        if description and description not in edge.descriptions:
            edge.descriptions.append(description)
        head_node.mentions += 1
        tail_node.mentions += 1
        return AddResult(status=status, edge_key=key)

    def register_entities(
        self, entities: Iterable[dict], *, max_mention_tokens: int = 7
    ) -> int:
        """Register entity mentions extracted independently of any triple."""
        count = 0
        for entity in entities or []:
            name = (entity or {}).get("name", "")
            if validate_entity_name(name, max_mention_tokens=max_mention_tokens):
                self.rejected["bad_entity"] = self.rejected.get("bad_entity", 0) + 1
                continue
            try:
                self.upsert_node(name, (entity or {}).get("type"))
                count += 1
            except ValueError:
                continue
        return count

    # ------------------------------------------------------------- analysis
    def backfill_types(self, min_mentions: int = 2) -> int:
        """Vote node types from edge relations (ingestion stage I8).

        Two rules, both from the design record:

        1. Only edges with ``mentions >= min_mentions`` get a vote. solution.md
           records why: an inverted one-off edge once typed "Tesla Motors" as a
           PERSON and blocked a legitimate merge, so single-sighting edges are
           noise.
        2. Votes are **weighted by mention count**, because mention count is
           free confidence (graph_making.md §6). One edge seen 412 times is
           strong evidence; two edges seen once each are weak. A node needs a
           winning weight of at least ``min_mentions`` to be retyped.

        Only nodes still holding the default ``condition`` type are touched —
        an explicit type from extraction outranks a guess.
        """
        weights: dict[str, dict[str, int]] = {}
        for edge in self.edges.values():
            if edge.mentions < min_mentions:
                continue
            for node_key in (edge.head, edge.tail):
                node = self.nodes.get(node_key)
                if node is None or node.type != "condition":
                    continue
                guess = self._infer_type_from_relation(edge.relation, node_key, edge)
                if not guess or guess == "condition":
                    continue
                tally = weights.setdefault(node_key, {})
                tally[guess] = tally.get(guess, 0) + edge.mentions
        applied = 0
        for node_key, tally in weights.items():
            best_type, best_weight = max(tally.items(), key=lambda kv: kv[1])
            if best_weight >= min_mentions:
                self.nodes[node_key].type = best_type
                applied += 1
        return applied

    @staticmethod
    def _infer_type_from_relation(
        relation: str, node_key: str, edge: Edge
    ) -> str | None:
        rel = relation.lower()
        is_head = node_key == edge.head
        if "prescribe" in rel or "treat" in rel or "drug" in rel or "dose" in rel:
            return "drug" if is_head else "condition"
        if "diagnos" in rel or "test" in rel or "screen" in rel:
            return "test" if is_head else "condition"
        if "symptom" in rel:
            return "symptom" if is_head else "condition"
        if "procedure" in rel or "surgery" in rel:
            return "procedure" if is_head else "condition"
        return None

    def prune_min_mentions(self, min_mentions: int = 2) -> int:
        """Drop edges seen fewer than ``min_mentions`` times.

        Justification from graph_making.md §6: real relationships are
        re-extracted across documents; junk appears once.
        """
        doomed = [k for k, e in self.edges.items() if e.mentions < min_mentions]
        for key in doomed:
            del self.edges[key]
        for node in self.nodes.values():
            if node.mentions:
                node.mentions = max(0, node.mentions - len(doomed))
        return len(doomed)

    def stats(self) -> dict:
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "edges_with_provenance": sum(1 for e in self.edges.values() if e.source_chunks),
            "rejected": dict(sorted(self.rejected.items())),
            "max_mentions_edge": max((e.mentions for e in self.edges.values()), default=0),
        }

    # ----------------------------------------------------------- provenance
    def resolvable_text_units(self) -> set[str]:
        """Every chunk id any edge can be traced back to."""
        out: set[str] = set()
        for edge in self.edges.values():
            out.update(edge.source_chunks)
        return out

    def dangling_edges(self) -> list[tuple[str, str, str]]:
        """Edges pointing at nodes that do not exist. Must always be empty."""
        return [
            key
            for key in self.edges
            if key[0] not in self.nodes or key[2] not in self.nodes
        ]

    # --------------------------------------------------------- persistence
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges.values()],
            "rejected": dict(sorted(self.rejected.items())),
            "stats": self.stats(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Graph":
        graph = cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        for raw in payload.get("nodes", []):
            node = Node(
                key=raw["key"],
                display=raw["display"],
                type=raw.get("type", "condition"),
                mentions=int(raw.get("mentions", 0)),
                aliases=set(raw.get("aliases", [])),
            )
            graph.nodes[node.key] = node
        for raw in payload.get("edges", []):
            edge = Edge(
                head=raw["head"],
                relation=raw["relation"],
                tail=raw["tail"],
                mentions=int(raw.get("mentions", 0)),
                source_chunks=set(raw.get("source_chunks", [])),
                descriptions=list(raw.get("descriptions", [])),
            )
            graph.edges[edge.key] = edge
        graph.rejected = dict(payload.get("rejected", {}))
        return graph