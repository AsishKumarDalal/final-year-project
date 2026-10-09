#!/usr/bin/env python3
"""Checkpoint-style end-to-end runner (dev/test fast path, manual only).

    python3 dev/run_e2e.py --index [--corpus-dir data/corpus_test] [--limit N]
    python3 dev/run_e2e.py --ask "question text" [--role patient]

--index ingests the corpus once and checkpoints it to --checkpoint-dir
  (default ``dev/data``):
  chunks -> MiniLM embeddings -> FAISS (dev vector store) +
  LLM extraction (hosted Qwen tunnel) -> networkx graph store +
  communities (Leiden/Louvain) + community reports (same tunnel LLM).
--ask loads the checkpoint and runs the FULL medharness system
  (Laya decide -> rules escalate -> Qwen explain). No harness code is
  reimplemented here — everything comes from ``medharness.*``.

Not part of ``make validate``. Network use is manual-only by design.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))          # so `import graphrag` resolves
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "dev"))

DEFAULT_EXTRACTION_BASE_URL = os.getenv(
    "KAGGLE_LLM_BASE_URL",
    "https://pond-breathing-foto-advocate.trycloudflare.com/v1")
DEFAULT_EXTRACTION_MODEL = os.getenv("KAGGLE_LLM_MODEL", "qwen2.5-32b-instruct")

SAFE_Q = "What is a myocardial infarction and what role does aspirin play?"
ESCALATE_T = "Severe chest pain with pressure spreading to my left arm since this morning."


# ------------------------------------------------------------------ --index
def _extraction_client(base_url: str, timeout_s: int):
    from openai import OpenAI

    return OpenAI(base_url=base_url.rstrip("/"), api_key="sk-local",
                  timeout=timeout_s)


def do_index(args) -> int:
    from graphrag.chunking import chunk_document
    from graphrag.config import Settings
    from graphrag.checkpoint import ExtractionCheckpoint
    from graphrag.graph import Graph
    from graphrag.llm import build_extraction_prompt, parse_batch_output
    from graphrag.embeddings import MiniLMEmbedder, describe_for_embedding
    from graphrag.stores import stable_point_id
    from medharness.stores.graph import GraphStore
    from faiss_store import FaissVectorStore

    settings = Settings.from_env()
    corpus_dir = Path(args.corpus_dir)
    ckpt = Path(args.checkpoint_dir)
    ckpt.mkdir(parents=True, exist_ok=True)

    docs: list[tuple[str, str]] = []
    for path in sorted(corpus_dir.glob("*")):
        if path.suffix.lower() not in (".txt", ".md"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            docs.append((path.stem.replace("_", " "), text))
        if args.limit and len(docs) >= args.limit:
            break
    if not docs:
        print(f"FAIL: no .txt/.md documents in {corpus_dir}")
        return 1
    print(f"[1/6] loaded {len(docs)} documents from {corpus_dir}")

    chunks = []
    for title, text in docs:
        chunks.extend(chunk_document(
            title, text, target_chars=settings.chunk_target_chars,
            overlap_chars=settings.chunk_overlap_chars))
    print(f"[2/6] chunked into {len(chunks)} chunks")

    checkpoint = ExtractionCheckpoint(
        triples_path=ckpt / "triples.jsonl", cache_dir=ckpt / "cache",
        failed_path=ckpt / "failed_chunks.json")
    pending = [c for c in chunks if not checkpoint.is_done(c.chunk_id)]
    print(f"[3/6] extracting: pending={len(pending)} cached={len(chunks) - len(pending)} "
          f"(model={args.model} batch={args.batch})")

    client = _extraction_client(args.extraction_base_url, args.timeout_s)
    counts = {"extracted": 0, "cache_hits": 0, "failed": 0}
    for start in range(0, len(pending), args.batch):
        batch = pending[start: start + args.batch]
        results: dict[str, dict] = {}
        uncached = []
        for chunk in batch:
            hit = checkpoint.cache_get(chunk.content_hash)
            if hit is not None:
                results[chunk.chunk_id] = hit
                counts["cache_hits"] += 1
            else:
                uncached.append(chunk)
        if uncached:
            prompt = build_extraction_prompt(
                [c.text for c in uncached], max_triples=args.max_triples)
            try:
                resp = client.chat.completions.create(
                    model=args.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0, max_tokens=2048)
                content = resp.choices[0].message.content or ""
                fresh = parse_batch_output(content, expected=len(uncached))
            except Exception as exc:  # noqa: BLE001 — record, never crash the run
                for chunk in uncached:
                    checkpoint.record_failure(
                        chunk.chunk_id, f"{type(exc).__name__}: {exc}"[:200])
                counts["failed"] += len(uncached)
                print(f"      batch @{start}: FAILED {type(exc).__name__}")
                continue
            for chunk, result in zip(uncached, fresh):
                results[chunk.chunk_id] = result
        for chunk in batch:
            result = results.get(chunk.chunk_id)
            if result is None:
                continue
            checkpoint.record(
                chunk_id=chunk.chunk_id, doc_id=chunk.doc_id, title=chunk.title,
                content_hash=chunk.content_hash, result=result)
            counts["extracted"] += 1
        print(f"      extracted {counts['extracted']}/{len(pending)} "
              f"(failed={counts['failed']})")

    print("[4/6] building graph (graphrag Graph + networkx GraphStore)")
    graph = Graph()
    triples_added = entities = 0
    for entry in checkpoint.iter_results():
        entities += graph.register_entities(entry.get("entities", []))
        for triple in entry.get("triples", []) or []:
            res = graph.add_triple(
                str(triple.get("head", "")), str(triple.get("relation", "")),
                str(triple.get("tail", "")),
                source_chunk=entry.get("chunk_id", ""),
                description=(f"{triple.get('head', '')} {triple.get('relation', '')} "
                             f"{triple.get('tail', '')}".strip() or None))
            if res.status in ("added", "merged"):
                triples_added += 1
    graph.save(ckpt / "graph.json")

    nx_store = GraphStore()
    for edge in graph.edges.values():
        nx_store.add_edge(edge.head, edge.tail, edge.relation,
                          next(iter(sorted(edge.source_chunks)), ""))
    import networkx as nx

    (ckpt / "networkx.json").write_text(
        json.dumps(nx.node_link_data(nx_store.graph, edges="edges"),
                   ensure_ascii=False))
    print(f"      nodes={len(graph.nodes)} edges={len(graph.edges)} "
          f"triples={triples_added} entities={entities}")

    print("[5/6] communities + reports (same tunnel LLM narrates)")
    from graphrag.communities import partition
    from graphrag.reports import (CommunityReport, build_report_context,
                                  member_fact_lines, member_text_units,
                                  parse_report, report_prompt)
    weighted = [(e.head, e.relation, e.tail, float(e.mentions))
                for e in graph.edges.values()]
    communities = partition(weighted, levels=2)
    (ckpt / "communities.json").write_text(json.dumps(
        [c.as_dict() for c in communities], ensure_ascii=False, indent=2))
    edge_dicts = [
        {"head": e.head, "relation": e.relation, "tail": e.tail,
         "mentions": e.mentions, "source_chunks": sorted(e.source_chunks),
         "descriptions": list(e.descriptions)} for e in graph.edges.values()]
    displays = {k: n.display for k, n in graph.nodes.items()}
    reports: list[CommunityReport] = []
    for community in sorted(communities, key=lambda c: (c.level, c.id)):
        lines = member_fact_lines(community, edge_dicts, displays)
        subs = [r for r in reports if r.level == community.level - 1]
        context = build_report_context(community, member_lines=lines,
                                       sub_reports=subs)
        try:
            resp = client.chat.completions.create(
                model=args.model,
                messages=[{"role": "user",
                           "content": report_prompt(community, context)}],
                temperature=0.2, max_tokens=600)
            title, summary = parse_report(
                resp.choices[0].message.content or "")
        except Exception:  # noqa: BLE001 — a failed report degrades, never blocks
            title, summary = community.title, ""
        reports.append(CommunityReport(
            community_id=community.id, level=community.level,
            title=title or community.title, summary=summary,
            member_text_units=member_text_units(community, edge_dicts),
            model_id=args.model))
    (ckpt / "community_reports.json").write_text(json.dumps(
        [r.as_dict() for r in reports], ensure_ascii=False, indent=2))
    print(f"      communities={len(communities)} reports={len(reports)}")

    print("[6/6] embedding with MiniLM -> FAISS")
    embedder = MiniLMEmbedder(model_id=settings.embedding_model)
    chunk_texts = {c.chunk_id: c.text for c in chunks}
    (ckpt / "chunks.json").write_text(
        json.dumps(chunk_texts, ensure_ascii=False))
    store = FaissVectorStore(dim=embedder.dim)
    unit_ids = sorted(chunk_texts)
    store.upsert("text_units",
                 [stable_point_id("text_units", i) for i in unit_ids],
                 embedder.embed([chunk_texts[i] for i in unit_ids]),
                 [{"text_unit_id": i, "doc_id": i.split("::")[0]} for i in unit_ids])
    node_keys = sorted(graph.nodes)
    node_texts = [describe_for_embedding(
        graph.nodes[k].display, graph.nodes[k].type,
        graph.nodes[k].mentions, sorted(graph.nodes[k].aliases))
        for k in node_keys]
    if node_keys:
        store.upsert("entities",
                     [stable_point_id("entities", k) for k in node_keys],
                     embedder.embed(node_texts),
                     [{"entity_key": k, "display": graph.nodes[k].display}
                      for k in node_keys])
    if reports:
        rep_texts = [(r.summary or r.title) for r in reports]
        store.upsert("community_reports",
                     [stable_point_id("community_reports", r.community_id)
                      for r in reports],
                     embedder.embed(rep_texts),
                     [{"community_id": r.community_id, "level": r.level}
                      for r in reports])
    store.save(ckpt / "faiss")
    meta = {"corpus_dir": str(corpus_dir), "extraction_model": args.model,
            "extraction_base_url": args.extraction_base_url,
            "embed_model": settings.embedding_model, "dim": embedder.dim,
            "docs": len(docs), "chunks": len(chunks),
            "nodes": len(graph.nodes), "edges": len(graph.edges),
            "communities": len(communities), "reports": len(reports),
            **counts, "wall_s": round(time.perf_counter() - _T0, 1)}
    (ckpt / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"DONE — checkpoint in {ckpt}: {meta}")
    return 0


# -------------------------------------------------------------------- --ask
def _load_checkpoint(ckpt: Path):
    from graphrag.graph import Graph
    from faiss_store import FaissVectorStore

    chunk_texts = json.loads((ckpt / "chunks.json").read_text())
    graph = Graph.load(ckpt / "graph.json")
    store = FaissVectorStore.load(ckpt / "faiss")
    meta = json.loads((ckpt / "meta.json").read_text())
    reports_path = ckpt / "community_reports.json"
    community_reports = {}
    if reports_path.exists():
        for raw in json.loads(reports_path.read_text()):
            community_reports[raw["community_id"]] = {
                "summary": raw.get("summary", ""),
                "member_text_units": raw.get("member_text_units", [])}
    return chunk_texts, graph, store, meta, community_reports


def do_ask(args) -> int:
    # All system behaviour comes from medharness — nothing reimplemented here.
    from graphrag.query import QueryEngine
    from graphrag.system1 import System1Client
    from graphrag.embeddings import MiniLMEmbedder
    from medharness.contracts.models import HarnessRequest
    from medharness.decision.http_adapter import LayaHttpClient
    from medharness.decision.fixture_adapter import FixtureDecisionClient
    from medharness.generation.qwen_client import QwenClient
    from medharness.orchestrator import Orchestrator
    from medharness.tools.external_search import GraphRAGExternalSearch
    from medharness.handoff import render_handoff

    ckpt = Path(args.checkpoint_dir)
    for needed in ("chunks.json", "graph.json", "meta.json"):
        if not (ckpt / needed).exists():
            raise SystemExit(
                f"checkpoint {ckpt} missing {needed} — run --index first")
    chunk_texts, graph, store, meta, community_reports = _load_checkpoint(ckpt)

    entity_index = {
        k: {"display": n.display, "type": n.type, "mentions": n.mentions,
            "aliases": sorted(n.aliases)} for k, n in graph.nodes.items()}
    adjacency: dict[str, list] = {}
    for e in graph.edges.values():
        src = sorted(e.source_chunks)
        adjacency.setdefault(e.head, []).append((e.relation, e.tail, e.mentions, src))
        adjacency.setdefault(e.tail, []).append((e.relation + "_REV", e.head, e.mentions, src))

    embedder = MiniLMEmbedder(model_id=meta.get(
        "embed_model", "sentence-transformers/all-MiniLM-L6-v2"))
    laya_url = os.getenv("LAYA_BASE_URL", "http://127.0.0.1:8000")
    engine = QueryEngine(
        vector_store=store, embedder=embedder,
        system1=System1Client(base_url=laya_url,
                              model_id=os.getenv("LAYA_MODEL_ID", "laya")),
        entity_index=entity_index, chunk_texts=chunk_texts,
        adjacency=adjacency, community_reports=community_reports)
    ext = GraphRAGExternalSearch(engine)

    live = LayaHttpClient(base_url=laya_url)
    if os.getenv("MEDH_LIVE", "0") == "1" and not live.available():
        raise SystemExit(f"Laya unavailable at {laya_url} (MEDH_LIVE=1 forces it)")
    decision = live if live.available() else FixtureDecisionClient()
    print(f"decisions : laya @ {laya_url} (available={live.available()}) "
          f"-> {'Laya' if live.available() else 'fixtures'}")

    llm = QwenClient()  # hosted Qwen from KAGGLE_LLM_BASE_URL
    orch = Orchestrator(decision_client=decision, llm_client=llm,
                        external_search=ext)
    print(f"llm       : {llm.base_url} model={llm.model}")
    print(f"retrieval : {ckpt} -> FAISS + networkx "
          f"(chunks={len(chunk_texts)} nodes={len(entity_index)} "
          f"units={store.count('text_units')})\n")

    questions = [args.ask] if args.ask else [SAFE_Q, ESCALATE_T]
    for question in questions:
        print(f"═══ Q ({args.role}): {question}")
        resp = orch.assess(HarnessRequest(text=question, actor_role=args.role))
        print(f"decision={resp.decision} escalated={resp.escalated} "
              f"llm_called={resp.decision_trace.get('llm_called')}")
        if resp.escalated:
            print(render_handoff(resp, reported_text=question))
        else:
            print(f"\nANSWER:\n{resp.explanation}\n")
            for c in (resp.citations or [])[:8]:
                print(f"  - {c}")
        print()
    return 0


_T0 = time.perf_counter()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Checkpoint e2e: --index then --ask")
    parser.add_argument("--checkpoint-dir", default="dev/data")
    sub = parser.add_mutually_exclusive_group(required=True)
    sub.add_argument("--index", action="store_true")
    sub.add_argument("--ask", nargs="?", const="", default=None,
                     help="question text; omit for the safe+escalate demo pair")
    parser.add_argument("--corpus-dir", default="data/corpus_test")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch", type=int, default=1,
                        help="extraction batch size (hosted Qwen needs 1 for clean JSON)")
    parser.add_argument("--max-triples", type=int, default=10)
    parser.add_argument("--role", default="patient")
    parser.add_argument("--extraction-base-url", default=DEFAULT_EXTRACTION_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_EXTRACTION_MODEL)
    parser.add_argument("--timeout-s", type=int, default=600)
    args = parser.parse_args(argv)
    if args.index:
        return do_index(args)
    return do_ask(args)


if __name__ == "__main__":
    raise SystemExit(main())
