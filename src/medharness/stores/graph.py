"""Simple graph store (S1). networkx in-process — no Neo4j, no Docker.

Dev-speed swap for PROMPT.md sect 7.4 (Neo4j). Nodes = entities, edges carry
`relation`, `mentions`, `source_chunks` (every edge keeps provenance so a
citation resolves to real corpus text — sect 7.3.4). Writes merge, never
duplicate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx


@dataclass
class GraphStore:
    graph: nx.MultiDiGraph = field(default_factory=nx.MultiDiGraph)

    def add_entity(self, key: str, **attrs: object) -> None:
        if self.graph.has_node(key):
            cur = self.graph.nodes[key]
            cur["mentions"] = int(cur.get("mentions", 0)) + int(attrs.get("mentions", 1))
        else:
            self.graph.add_node(key, mentions=int(attrs.get("mentions", 1)),
                                type=str(attrs.get("type", "condition")),
                                display=str(attrs.get("display", key)))

    def add_edge(self, head: str, tail: str, relation: str,
                 source_chunk: str, mentions: int = 1) -> None:
        self.add_entity(head)
        self.add_entity(tail)
        for _, data in self.graph.get_edge_data(head, tail, default={}).items():
            if data.get("relation") == relation:
                data["mentions"] = int(data.get("mentions", 0)) + mentions
                chunks = data.setdefault("source_chunks", [])
                if source_chunk not in chunks:
                    chunks.append(source_chunk)
                return
        self.graph.add_edge(head, tail, relation=relation, mentions=mentions,
                            source_chunks=[source_chunk])

    def neighbours(self, key: str, *, hops: int = 2, limit: int = 500) -> list[dict]:
        if key not in self.graph:
            return []
        out: list[dict] = []
        seen = {key}
        frontier = [(key, 0, [])]
        while frontier and len(out) < limit:
            node, depth, rels = frontier.pop(0)
            if depth >= hops:
                continue
            for nbr in list(self.graph.successors(node)) + list(self.graph.predecessors(node)):
                edge = self.graph.get_edge_data(node, nbr) or self.graph.get_edge_data(nbr, node) or {}
                first = next(iter(edge.values()), {})
                step = list(rels) + [str(first.get("relation", "RELATES"))]
                if nbr not in seen:
                    seen.add(nbr)
                    out.append({"seed": key, "relations": step, "node": nbr,
                                "type": self.graph.nodes[nbr].get("type", ""),
                                "depth": depth + 1})
                    frontier.append((nbr, depth + 1, step))
        return out[:limit]

    def node_count(self) -> int:
        return self.graph.number_of_nodes()

    def edge_count(self) -> int:
        return self.graph.number_of_edges()

    def walk_edges(self, key: str, *, hops: int = 2, limit: int = 400) -> list[dict]:
        """BFS yielding edges WITH their source_chunks (text_unit ids). The
        provenance that makes an external-search citation resolvable."""
        out: list[dict] = []
        visited = {key}
        frontier = [(key, 0)]
        while frontier and len(out) < limit:
            node, depth = frontier.pop(0)
            if depth >= hops:
                continue
            for src, dst in list(self.graph.out_edges(node)) + list(self.graph.in_edges(node)):
                data_map = self.graph.get_edge_data(src, dst) or {}
                for data in data_map.values():
                    out.append({"head": src, "tail": dst,
                                "relation": str(data.get("relation", "RELATES")),
                                "source_chunks": list(data.get("source_chunks", [])),
                                "depth": depth + 1})
                if dst not in visited:
                    visited.add(dst)
                    frontier.append((dst, depth + 1))
        return out[:limit]
