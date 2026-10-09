"""kb_search (S5) over the simple stores (S1). No Qdrant/Neo4j — dev speed.

This is the INTERNAL, fast, shallow retrieval — a flat vector cosine over
text_units, cited by text_unit id. It is deliberately NOT a graph walk. When
this returns too little, the caller escalates to search_external_docs
(tools/external_search.py), which runs the full graph walk.

Citations are text_unit: ids ONLY — a generated description never satisfies a
citation (sect 7.3.4 / D20, release-blocking). No hits -> not_found (never a
guess). Store errors degrade to empty, never a crash.
"""
from __future__ import annotations

from medharness.contracts.models import ToolFact


def kb_search(query: str, vector_store=None, graph_store=None, *,
              top_k: int = 5) -> ToolFact:
    """Search the corpus. Returns cited text_unit facts or not_found."""
    if vector_store is None or graph_store is None:
        from medharness.stores.seed_corpus import DOCS, ENTITIES, EDGES, stable_id
        from medharness.stores.graph import GraphStore
        from medharness.stores.vector import VectorStore
        vector_store, graph_store = VectorStore(dim=32), GraphStore()
        _seed_dev(vector_store, graph_store, DOCS, ENTITIES, EDGES, stable_id)

    facts: list[str] = []
    source_ids: list[str] = []

    # Door 1: vector over text_units (recall). Cosine in [ -1, 1 ].
    try:
        hits = _vector_hits(vector_store, query, top_k=top_k)
    except Exception:
        hits = []
    for hit in hits:
        pay = hit["payload"] if isinstance(hit, dict) else hit.payload
        facts.append(pay["text"])
        source_ids.append(f"text_unit:{pay['text_unit_id']}")

    # Dedup, preserve order.
    seen, f2, s2 = set(), [], []
    for f, s in zip(facts, source_ids):
        if s not in seen:
            seen.add(s)
            f2.append(f)
            s2.append(s)

    if not f2:
        return ToolFact(facts=[], source_ids=[], source_type="graphrag",
                        search_mode="none", not_found=True)
    return ToolFact(facts=f2, source_ids=s2, source_type="graphrag",
                    search_mode="basic")


def _vector_hits(vector_store, query: str, *, top_k: int):
    # Use the store's own embedder seam if present, else a hashed-token embedder.
    embed = getattr(vector_store, "embed", None)
    if embed is None:
        import hashlib
        import math

        def embed(texts):
            out = []
            for t in texts:
                v = [0.0] * getattr(vector_store, "dim", 32)
                for tok in t.lower().split():
                    v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % len(v)] += 1.0
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                out.append([x / n for x in v])
            return out

    return vector_store.search("text_units", embed([query])[0], top_k=top_k)


def _seed_dev(v, g, DOCS, ENTITIES, EDGES, stable_id):
    from medharness.stores.seed_corpus import seed
    import hashlib
    import math

    def fake_embed(texts):
        out = []
        for t in texts:
            vec = [0.0] * 32
            for tok in t.lower().split():
                vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 32] += 1.0
            n = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / n for x in vec])
        return out

    seed(v, g, fake_embed)
