"""External docs search — delegates to graphrag's QueryEngine (the single
retrieval implementation). No hand-rolled walk.

Mechanism: the harness answers from internal kb_search first; when that is not
enough the model calls search_external_docs(query, mode), which runs a FULL
graph walk via graphrag.query.QueryEngine (local 2-hop or global/community).
Docs return as text_unit facts; the LLM answers grounded in them. Citations
resolve to text_unit ids ONLY — a community summary or generated description
never satisfies a citation (D20).

Both run modes share ONE engine:
  offline  QueryEngine(MemoryVectorStore + seed entity_index/adjacency, system1=None)
  live     QueryEngine(QdrantStore + Neo4j adjacency + MiniLM + Laya)
"""
from __future__ import annotations

from medharness.contracts.models import SearchResult


class ExternalSearchClient:
    """Protocol: search(query, mode) -> SearchResult. Injected, never global."""

    def search(self, query: str, *, mode: str | None = None) -> SearchResult:
        raise NotImplementedError


class _DimEmbedder:
    """Adapts embed(texts)->vectors to graphrag's Embedder protocol (.dim + .embed)."""

    def __init__(self, fn, dim: int):
        self._fn = fn
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._fn(texts)


class GraphRAGExternalSearch:
    """Wraps a graphrag QueryEngine. Same adapter for offline and live."""

    def __init__(self, engine):
        self.engine = engine

    def search(self, query: str, *, mode: str | None = None) -> SearchResult:
        res = self.engine.ask(query, mode=mode)   # local | global | basic | none
        ev = [e for e in res.evidence if e.source_id.startswith("text_unit:")]
        facts = [e.text for e in ev]
        source_ids = [e.source_id for e in ev]
        if not facts:                              # global/narrate fallback
            facts = list(res.facts)
            source_ids = [s for s in res.source_ids if s.startswith("text_unit:")]
        return SearchResult(
            facts=facts, source_ids=source_ids, search_mode=res.search_mode,
            generated_interpretation=res.generated_interpretation,
            refusal=res.refusal,
            not_found=(res.refusal is not None or not facts))


def build_offline_engine():
    """A graphrag QueryEngine over the seeded simple corpus, no network/Docker.
    system1=None so routing defaults to local (safe, deterministic)."""
    from graphrag.query import QueryEngine
    from graphrag.stores import MemoryVectorStore
    from medharness.stores.seed_corpus import DOCS, ENTITIES, EDGES, hashed_embed

    dim = 32
    embed = hashed_embed(dim)
    embedder = _DimEmbedder(embed, dim)
    store = MemoryVectorStore(dim=dim)
    store.ensure_collections()

    chunk_texts = {f"{doc}::0": text for doc, text in DOCS.items()}
    entity_index: dict[str, dict] = {}
    ent_ids, ent_vecs, ent_pays = [], [], []
    for ents in ENTITIES.values():
        for name, etype in ents:
            key = name.lower().replace(" ", "_")
            entity_index[key] = {"display": name, "type": etype,
                                 "mentions": 1, "aliases": []}
            ent_ids.append(key)
            ent_vecs.append(embed([name])[0])
            ent_pays.append({"entity_key": key})
    store.upsert("entities", ent_ids, ent_vecs, ent_pays)

    u_ids = list(chunk_texts)
    store.upsert("text_units", u_ids, embed([chunk_texts[c] for c in u_ids]),
                 [{"text_unit_id": c} for c in u_ids])

    adjacency: dict[str, list] = {}
    for head, tail, rel, doc in EDGES:
        hk = head.lower().replace(" ", "_")
        tk = tail.lower().replace(" ", "_")
        adjacency.setdefault(hk, []).append((rel, tk, 1, [f"{doc}::0"]))

    return QueryEngine(vector_store=store, embedder=embedder, system1=None,
                       entity_index=entity_index, chunk_texts=chunk_texts,
                       adjacency=adjacency)


def build_offline_external_search() -> "GraphRAGExternalSearch":
    return GraphRAGExternalSearch(build_offline_engine())
