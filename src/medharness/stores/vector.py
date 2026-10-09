"""Simple vector store (S1). In-memory numpy cosine — no Qdrant, no Docker.

Dev-speed swap for PROMPT.md sect 7.4 (Qdrant). Same semantics the harness
needs: upsert by stable id, cosine top-k with optional payload filter.
Drop-in Qdrant adapter later keeps this interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ScoredHit:
    id: str
    score: float
    payload: dict


@dataclass
class VectorStore:
    dim: int = 384
    points: dict[str, dict[str, dict]] = field(default_factory=dict)

    def ensure_collections(self, *names: str) -> None:
        for n in names:
            self.points.setdefault(n, {})

    def upsert(self, collection: str, ids: list[str], vectors: list[list[float]],
               payloads: list[dict]) -> int:
        store = self.points.setdefault(collection, {})
        for pid, vec, pay in zip(ids, vectors, payloads):
            store[pid] = {"vector": np.asarray(vec, dtype=np.float32), "payload": dict(pay)}
        return len(ids)

    def search(self, collection: str, vector: list[float], *,
               top_k: int = 10, payload_filter: dict | None = None) -> list[ScoredHit]:
        q = np.asarray(vector, dtype=np.float32)
        qn = float(np.linalg.norm(q)) or 1.0
        scored: list[ScoredHit] = []
        for pid, pt in self.points.get(collection, {}).items():
            if payload_filter and not all(
                    pt["payload"].get(k) == v for k, v in payload_filter.items()):
                continue
            v = pt["vector"]
            denom = qn * float(np.linalg.norm(v)) or 1.0
            scored.append(ScoredHit(id=pid, score=float(q @ v / denom),
                                    payload=dict(pt["payload"])))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    def count(self, collection: str) -> int:
        return len(self.points.get(collection, {}))
