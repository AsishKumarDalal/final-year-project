"""Stores: Qdrant (vectors) and Neo4j (graph). Track B, ingestion I11.

Three Qdrant collections mirror GraphRAG's embedding targets —
``entities``, ``text_units``, ``community_reports`` — with payloads that map
every hit back to Neo4j (``entity_key`` / ``text_unit_id``) and to the corpus
(``doc_id``). That mapping is what makes a citation resolvable.

Design rules:

* Writes are **idempotent**: upsert by stable point id, so re-running never
  duplicates. Neo4j writes use MERGE, never CREATE.
* Drivers are imported **lazily** so unit tests and offline runs never need
  them. :class:`MemoryVectorStore` is the in-memory fake the tests use.
* Both stores may be down at query time. The query layer treats that as
  ``degraded`` — decision and escalation still return, because neither store
  sits on the safety path (PROMPT.md §7.4).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .embeddings import cosine

COLLECTIONS = ("entities", "text_units", "community_reports")


def stable_point_id(collection: str, key: str) -> str:
    """Deterministic UUID-ish id so re-indexing overwrites, never duplicates."""
    digest = hashlib.sha256(f"{collection}:{key}".encode("utf-8")).hexdigest()
    return (
        f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"
    )


@dataclass
class ScoredHit:
    id: str
    score: float
    payload: dict[str, Any]


@dataclass
class MemoryVectorStore:
    """In-memory vector store. Test double with the real query semantics:
    cosine top-k with optional payload filtering."""

    dim: int = 384
    points: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def ensure_collections(self, dim: int | None = None) -> None:
        for name in COLLECTIONS:
            self.points.setdefault(name, {})

    def upsert(self, collection: str, ids: Sequence[str],
               vectors: Sequence[Sequence[float]],
               payloads: Sequence[Mapping[str, Any]]) -> int:
        store = self.points.setdefault(collection, {})
        for point_id, vector, payload in zip(ids, vectors, payloads):
            store[point_id] = {"vector": list(vector), "payload": dict(payload)}
        return len(ids)

    def search(self, collection: str, vector: Sequence[float], *,
               top_k: int = 10,
               payload_filter: Mapping[str, Any] | None = None) -> list[ScoredHit]:
        scored: list[ScoredHit] = []
        for point_id, point in self.points.get(collection, {}).items():
            payload = point["payload"]
            if payload_filter and not all(
                payload.get(k) == v for k, v in payload_filter.items()
            ):
                continue
            scored.append(ScoredHit(
                id=point_id, score=cosine(list(vector), point["vector"]),
                payload=dict(payload)))
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:top_k]

    def count(self, collection: str) -> int:
        return len(self.points.get(collection, {}))

    def delete_collection(self, collection: str) -> None:
        self.points.pop(collection, None)


class QdrantStore:
    """Thin wrapper over ``qdrant-client``. Same method names as the fake."""

    def __init__(self, url: str, *, dim: int = 384, timeout_s: int = 30) -> None:
        self.url = url
        self.dim = dim
        self.timeout_s = timeout_s
        self._client: Any = None

    def _client_or_raise(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
        except ImportError as exc:
            raise RuntimeError(
                "qdrant-client is not installed in this interpreter."
            ) from exc
        self._client = QdrantClient(url=self.url, timeout=self.timeout_s)
        self._vector_params = VectorParams(size=self.dim, distance=Distance.COSINE)
        return self._client

    def ensure_collections(self, dim: int | None = None) -> None:
        from qdrant_client.models import Distance, VectorParams
        client = self._client_or_raise()
        params = VectorParams(size=dim or self.dim, distance=Distance.COSINE)
        for name in COLLECTIONS:
            if not client.collection_exists(name):
                client.create_collection(collection_name=name, vectors_config=params)

    def upsert(self, collection: str, ids: Sequence[str],
               vectors: Sequence[Sequence[float]],
               payloads: Sequence[Mapping[str, Any]]) -> int:
        from qdrant_client.models import PointStruct
        client = self._client_or_raise()
        client.upsert(
            collection_name=collection,
            points=[
                PointStruct(id=point_id, vector=list(vector), payload=dict(payload))
                for point_id, vector, payload in zip(ids, vectors, payloads)
            ],
        )
        return len(ids)

    def search(self, collection: str, vector: Sequence[float], *,
               top_k: int = 10,
               payload_filter: Mapping[str, Any] | None = None) -> list[ScoredHit]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        client = self._client_or_raise()
        query_filter = None
        if payload_filter:
            query_filter = Filter(
                must=[FieldCondition(key=k, match=MatchValue(value=v))
                      for k, v in payload_filter.items()]
            )
        hits = client.query_points(
            collection_name=collection, query=list(vector), limit=top_k,
            query_filter=query_filter, with_payload=True,
        ).points
        return [ScoredHit(id=str(h.id), score=float(h.score),
                          payload=dict(h.payload or {})) for h in hits]

    def count(self, collection: str) -> int:
        return self._client_or_raise().count(collection_name=collection).count

    def delete_collection(self, collection: str) -> None:
        self._client_or_raise().delete_collection(collection_name=collection)


@dataclass
class Neo4jStore:
    """Property-graph persistence. MERGE-only writes; provenance edges kept."""

    uri: str
    user: str
    password: str
    _driver: Any = field(default=None, repr=False)

    def _driver_or_raise(self) -> Any:
        if self._driver is not None:
            return self._driver
        if not self.password:
            raise RuntimeError("NEO4J_PASSWORD is not set.")
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RuntimeError("neo4j driver is not installed.") from exc
        self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def write_graph(self, nodes: Iterable[Mapping[str, Any]],
                    edges: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        """Upsert nodes and typed relationships. Returns counts."""
        driver = self._driver_or_raise()
        node_count = edge_count = 0
        with driver.session() as session:
            for node in nodes:
                session.run(
                    "MERGE (e:Entity {key: $key}) "
                    "SET e.display=$display, e.type=$type, e.mentions=$mentions, "
                    "e.aliases=$aliases",
                    key=node["key"], display=node.get("display", node["key"]),
                    type=node.get("type", "condition"),
                    mentions=int(node.get("mentions", 0)),
                    aliases=list(node.get("aliases", [])),
                )
                node_count += 1
            for edge in edges:
                session.run(
                    "MATCH (a:Entity {key: $head}), (b:Entity {key: $tail}) "
                    "MERGE (a)-[r:RELATES {relation: $relation}]->(b) "
                    "SET r.mentions=$mentions, r.source_chunks=$chunks",
                    head=edge["head"], tail=edge["tail"],
                    relation=edge["relation"], mentions=int(edge.get("mentions", 0)),
                    chunks=list(edge.get("source_chunks", [])),
                )
                edge_count += 1
        return {"nodes": node_count, "edges": edge_count}

    def neighbours(self, key: str, *, hops: int = 2,
                   limit: int = 500) -> list[dict[str, Any]]:
        """k-hop typed walk from one entity. The local-search expansion."""
        driver = self._driver_or_raise()
        with driver.session() as session:
            records = session.run(
                "MATCH path = (seed:Entity {key: $key})"
                "-[r:RELATES*1..%d]-(other:Entity) " % max(1, hops)
                + "RETURN seed.key AS seed, [rel IN r | rel.relation] AS relations, "
                  "other.key AS node, other.display AS display, "
                  "other.type AS type, length(path) AS depth "
                  "LIMIT $limit",
                key=key, limit=limit,
            )
            return [dict(record) for record in records]

    def write_communities(self, communities: Sequence[Mapping[str, Any]]) -> int:
        driver = self._driver_or_raise()
        count = 0
        with driver.session() as session:
            for community in communities:
                session.run(
                    "MERGE (c:Community {id: $id}) "
                    "SET c.level=$level, c.title=$title, c.summary=$summary",
                    id=community["id"], level=int(community.get("level", 0)),
                    title=community.get("title", ""),
                    summary=community.get("summary", ""),
                )
                for member in community.get("members", []):
                    session.run(
                        "MATCH (e:Entity {key: $key}), (c:Community {id: $id}) "
                        "MERGE (e)-[:MEMBER_OF]->(c)",
                        key=member, id=community["id"],
                    )
                count += 1
        return count