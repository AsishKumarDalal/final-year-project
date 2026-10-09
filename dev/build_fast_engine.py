"""Fast local QueryEngine for dev/test — reuses graphrag's OWN modules.

The graph is built by the REAL pipeline (hosted-Qwen extraction + Laya merge):
    python3 -m graphrag.pipeline --corpus-dir data/corpus_test --skip-stores --skip-reports
which saves `data/graph.json` but skips Qdrant/Neo4j. This loader then loads that
graph into FAST local stores:

  Graph.load          the property graph the pipeline built  = fast GRAPH store (in-memory)
  MemoryVectorStore   in-process numpy cosine                 = fast VECTOR store (replaces Qdrant)
  QueryEngine         the same engine production uses
  MiniLMEmbedder      same embedder as production (StubEmbedder fallback)
  System1Client       real Laya routing (local/global/basic); defaults to local if Laya is down

Only the STORES differ from production (in-process vs Docker). Same LLM, same Laya,
same engine. Not part of `make validate`. See dev/README.md.
"""
from __future__ import annotations

import os
from pathlib import Path


def build_fast_engine(corpus_dir=None, *, embedder_name: str = "minilm"):
    """embedder_name: 'minilm' (default, real all-MiniLM-L6-v2, cached locally)
    or 'stub' (hashed tokens, no download, instant)."""
    from graphrag.config import Settings
    from graphrag.pipeline import chunk_corpus, load_corpus
    from graphrag.graph import Graph
    from graphrag.stores import MemoryVectorStore
    from graphrag.query import QueryEngine
    from graphrag.system1 import System1Client
    from graphrag.embeddings import describe_for_embedding, StubEmbedder, MiniLMEmbedder

    settings = Settings.from_env()
    graph = Graph.load(settings.graph_file)   # built by the pipeline (--skip-stores)

    docs = load_corpus(Path(corpus_dir) if corpus_dir else settings.corpus_dir)
    chunk_texts = {c.chunk_id: c.text for c in chunk_corpus(docs, settings)}

    entity_index = {k: {"display": n.display, "type": n.type,
                        "mentions": n.mentions, "aliases": sorted(n.aliases)}
                    for k, n in graph.nodes.items()}
    adjacency: dict[str, list] = {}
    for e in graph.edges.values():
        src = sorted(e.source_chunks)
        adjacency.setdefault(e.head, []).append((e.relation, e.tail, e.mentions, src))
        adjacency.setdefault(e.tail, []).append((e.relation + "_REV", e.head, e.mentions, src))

    if embedder_name == "stub":
        emb = StubEmbedder()
    else:
        emb = MiniLMEmbedder(model_id=settings.embedding_model)

    store = MemoryVectorStore(dim=emb.dim)
    store.ensure_collections()
    unit_ids = sorted(chunk_texts)
    if unit_ids:
        store.upsert("text_units", unit_ids,
                     emb.embed([chunk_texts[i] for i in unit_ids]),
                     [{"text_unit_id": i, "doc_id": i.split("::")[0]} for i in unit_ids])
    node_keys = sorted(entity_index)
    if node_keys:
        node_texts = [describe_for_embedding(
            entity_index[k]["display"], entity_index[k]["type"],
            entity_index[k]["mentions"], entity_index[k]["aliases"]) for k in node_keys]
        store.upsert("entities", node_keys, emb.embed(node_texts),
                     [{"entity_key": k, "display": entity_index[k]["display"]}
                      for k in node_keys])

    system1 = System1Client(base_url=os.getenv("LAYA_BASE_URL", "http://127.0.0.1:8000"),
                            model_id=os.getenv("LAYA_MODEL_ID", "laya"))
    return QueryEngine(vector_store=store, embedder=emb, system1=system1,
                       entity_index=entity_index, chunk_texts=chunk_texts,
                       adjacency=adjacency)


def build_fast_external_search(corpus_dir=None, **kw):
    from medharness.tools.external_search import GraphRAGExternalSearch
    return GraphRAGExternalSearch(build_fast_engine(corpus_dir, **kw))
