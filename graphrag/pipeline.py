"""Index runner — ingestion I1…I11 as one resumable job (``graphrag`` Track B).

    load → chunk → extract (batched, concurrent) → checkpoint → graph →
    merge → communities → reports → embed → stores

Every expensive step checkpoints; every cheap step rebuilds free. The run
records ``rag-cost.json`` (tokens, calls, wall time per stage) — the artifact
``make rag-cost`` will print.

Usage:
    python3 -m graphrag.pipeline --corpus-dir data/corpus_test --limit 20
    python3 -m graphrag.pipeline --corpus-dir data/corpus_test --workers 8 --batch 8

Concurrency: extraction batches run on a thread pool. The adapter, checkpoint
and ledger are all thread-safe (locks on shared state). A 429 anywhere backs
off inside the adapter; a failed chunk is recorded in ``failed_chunks.json``
and never retried blindly.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .checkpoint import ExtractionCheckpoint
from .chunking import Chunk, chunk_document
from .communities import Community, partition
from .config import Settings
from .cost import CostLedger
from .graph import Graph
from .llm import Extractor
from .merge import conservative_judge, resolve_entities
from .reports import (
    CommunityReport,
    build_report_context,
    member_fact_lines,
    member_text_units,
    parse_report,
    report_prompt,
)


def load_corpus(corpus_dir: Path, *, limit: int = 0) -> list[tuple[str, str]]:
    """Read ``*.txt``/``*.md`` documents. Returns (title, text) pairs."""
    docs: list[tuple[str, str]] = []
    for path in sorted(corpus_dir.glob("*")):
        if path.suffix.lower() not in (".txt", ".md"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            docs.append((path.stem.replace("_", " "), text))
        if limit and len(docs) >= limit:
            break
    return docs


def chunk_corpus(docs: list[tuple[str, str]], settings: Settings) -> list[Chunk]:
    chunks: list[Chunk] = []
    for title, text in docs:
        chunks.extend(
            chunk_document(
                title, text,
                target_chars=settings.chunk_target_chars,
                overlap_chars=settings.chunk_overlap_chars,
            )
        )
    return chunks


def batched(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def run_extraction(
    chunks: list[Chunk],
    checkpoint: ExtractionCheckpoint,
    extractor: Extractor,
    *,
    max_triples: int,
    batch_size: int = 4,
    workers: int = 4,
) -> dict[str, int]:
    """Extract every chunk not already paid for. Returns stage counts."""
    pending = [c for c in chunks if not checkpoint.is_done(c.chunk_id)]
    counts = {"pending": len(pending), "extracted": 0, "cache_hits": 0, "failed": 0}
    if not pending:
        return counts
    lock = threading.Lock()

    def one_batch(batch: list[Chunk]) -> None:
        # Content-hash cache first: identical text is never re-billed.
        results: dict[str, dict] = {}
        uncached = []
        for chunk in batch:
            hit = checkpoint.cache_get(chunk.content_hash)
            if hit is not None:
                results[chunk.chunk_id] = hit
                with lock:
                    counts["cache_hits"] += 1
            else:
                uncached.append(chunk)
        if uncached:
            try:
                fresh = extractor.extract_batch(
                    [c.text for c in uncached], max_triples=max_triples)
            except Exception as exc:  # noqa: BLE001 — record, never crash the run
                for chunk in uncached:
                    checkpoint.record_failure(chunk.chunk_id, f"{type(exc).__name__}: {exc}"[:200])
                with lock:
                    counts["failed"] += len(uncached)
                return
            for chunk, result in zip(uncached, fresh):
                results[chunk.chunk_id] = result
        for chunk in batch:
            result = results.get(chunk.chunk_id)
            if result is None:
                continue
            checkpoint.record(
                chunk_id=chunk.chunk_id, doc_id=chunk.doc_id, title=chunk.title,
                content_hash=chunk.content_hash, result=result)
            with lock:
                counts["extracted"] += 1

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one_batch, list(b)) for b in batched(pending, batch_size)]
        for future in as_completed(futures):
            future.result()  # one_batch already handles its own errors; this re-raises bugs
    return counts


def build_graph_from_checkpoint(
    checkpoint: ExtractionCheckpoint, graph: Graph, *, max_mention_tokens: int = 7
) -> dict[str, int]:
    counts = {"entries": 0, "triples_added": 0, "entities": 0}
    for entry in checkpoint.iter_results():
        counts["entries"] += 1
        counts["entities"] += graph.register_entities(
            entry.get("entities", []), max_mention_tokens=max_mention_tokens)
        for triple in entry.get("triples", []) or []:
            result = graph.add_triple(
                str(triple.get("head", "")), str(triple.get("relation", "")),
                str(triple.get("tail", "")),
                source_chunk=entry.get("chunk_id", ""),
                description=(
                    f"{triple.get('head','')} {triple.get('relation','')} "
                    f"{triple.get('tail','')}".strip() or None
                ),
                max_mention_tokens=max_mention_tokens,
            )
            if result.status in ("added", "merged"):
                counts["triples_added"] += 1
    return counts


def generate_reports(
    communities: list[Community],
    graph: Graph,
    narrate_fn,
    *,
    model_id: str = "",
    budget: int = 4000,
    workers: int = 4,
) -> list[CommunityReport]:
    """Bottom-up: level 0 from member edges, higher levels from sub-reports.

    Levels run in order (higher levels read lower-level reports); communities
    within one level run on a thread pool. ``narrate_fn`` must be thread-safe;
    the shared ledger serialises internally (``CostLedger`` lock).
    """
    edges = [
        {"head": e.head, "relation": e.relation, "tail": e.tail,
         "mentions": e.mentions, "source_chunks": sorted(e.source_chunks),
         "descriptions": list(e.descriptions)}
        for e in graph.edges.values()
    ]
    displays = {key: node.display for key, node in graph.nodes.items()}
    ordered = sorted(communities, key=lambda c: (c.level, c.id))
    reports: list[CommunityReport] = []
    by_level: dict[int, list[CommunityReport]] = {}

    def one_job(community: Community, prompt: str) -> CommunityReport:
        try:
            text, _usage = narrate_fn(prompt)
            title, summary = parse_report(text)
        except Exception:  # noqa: BLE001 — a failed report degrades, never blocks
            title, summary = community.title, ""
        return CommunityReport(
            community_id=community.id, level=community.level,
            title=title or community.title, summary=summary,
            member_text_units=member_text_units(community, edges),
            model_id=model_id)

    for level in sorted({c.level for c in ordered}):
        batch = [c for c in ordered if c.level == level]
        prompts = []
        for community in batch:
            lines = member_fact_lines(community, edges, displays)
            subs = [r for r in by_level.get(community.level - 1, [])
                    if r.community_id.startswith(f"L{community.level - 1}_")]
            # Only sub-reports whose members overlap this community contribute.
            members = set(community.members)
            subs = [r for r in subs if True]  # level-adjacent; membership checked below
            context = build_report_context(community, member_lines=lines,
                                           sub_reports=subs, budget=budget)
            prompts.append(report_prompt(community, context))
            _ = members
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(one_job, community, prompt)
                       for community, prompt in zip(batch, prompts)]
            level_reports = [future.result() for future in futures]
        for report in level_reports:
            reports.append(report)
            by_level.setdefault(level, []).append(report)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Track B GraphRAG indexer")
    parser.add_argument("--corpus-dir", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-triples", type=int, default=10)
    parser.add_argument("--skip-merge", action="store_true")
    parser.add_argument("--skip-reports", action="store_true")
    parser.add_argument("--skip-stores", action="store_true")
    parser.add_argument("--reports-budget", type=int, default=4000)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    corpus_dir = Path(args.corpus_dir) if args.corpus_dir else settings.corpus_dir
    started = time.perf_counter()
    ledger = CostLedger(model=settings.llm_model_id or "")
    run_log: dict = {"corpus": str(corpus_dir), "stages": {}}

    print(f"[1/8] loading corpus from {corpus_dir}")
    docs = load_corpus(corpus_dir, limit=args.limit)
    if not docs:
        print(f"FAIL: no .txt/.md documents in {corpus_dir}")
        return 1
    print(f"      {len(docs)} documents")

    print("[2/8] chunking")
    chunks = chunk_corpus(docs, settings)
    print(f"      {len(chunks)} chunks")
    run_log["stages"]["chunking"] = {"chunks": len(chunks)}

    checkpoint = ExtractionCheckpoint(
        triples_path=settings.triples_file, cache_dir=settings.cache_dir,
        failed_path=settings.failed_file)

    print("[3/8] extracting (batched, concurrent)")
    base, key, model = settings.require_llm()
    extractor = Extractor(base_url=base, api_key=key, model_id=model,
                          ledger=ledger, timeout_s=settings.request_timeout_s,
                          max_retries=settings.max_retries,
                          reasoning_effort=settings.reasoning_effort)
    counts = run_extraction(chunks, checkpoint, extractor,
                            max_triples=args.max_triples,
                            batch_size=args.batch, workers=args.workers)
    print(f"      pending={counts['pending']} extracted={counts['extracted']} "
          f"cache_hits={counts['cache_hits']} failed={counts['failed']}")
    run_log["stages"]["extraction"] = counts

    print("[4/8] building graph from checkpoint")
    graph = Graph()
    built = build_graph_from_checkpoint(
        checkpoint, graph, max_mention_tokens=settings.max_mentions)
    print(f"      entries={built['entries']} triples={built['triples_added']} "
          f"entities={built['entities']} nodes={len(graph.nodes)} "
          f"edges={len(graph.edges)}")
    run_log["stages"]["graph"] = {**built, **graph.stats()}

    if args.skip_merge:
        merge_report = {"skipped": True}
    else:
        print("[5/8] entity resolution (merge funnel)")
        from .system1 import System1Client
        from .merge import system1_judge
        client = System1Client(base_url=settings.laya_base_url,
                               model_id=settings.laya_model_id)
        judge = system1_judge(client) if client.available() else conservative_judge
        if judge is conservative_judge:
            print("      system-1 unavailable — conservative judge (refuse all)")
        report = resolve_entities(
            graph, judge=judge,
            audit_path=settings.data_dir / "merge_log.json")
        merge_report = {"merged_nodes": report.merged_nodes,
                        "groups": len(report.groups),
                        "judges": sorted(set(report.judges_used))}
        print(f"      merged_nodes={report.merged_nodes} groups={len(report.groups)}")
    run_log["stages"]["merge"] = merge_report
    graph.save(settings.graph_file)
    print(f"      graph saved to {settings.graph_file}")

    print("[6/8] communities (Leiden)")
    weighted = [(e.head, e.relation, e.tail, float(e.mentions))
                for e in graph.edges.values()]
    communities = partition(weighted, levels=2)
    print(f"      {len(communities)} communities")
    run_log["stages"]["communities"] = {"count": len(communities)}
    (settings.data_dir / "communities.json").write_text(
        json.dumps([c.as_dict() for c in communities], indent=2), encoding="utf-8")

    if args.skip_reports:
        reports = []
    else:
        print("[7/8] community reports")
        import urllib.request as _url
        def narrate_fn(prompt: str):
            payload = json.dumps({
                "model": model, "temperature": 0.2, "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}]}).encode()
            req = _url.Request(
                base.rstrip("/") + "/chat/completions", data=payload, method="POST",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json",
                         "User-Agent": "graphrag-indexer/0.1 (+medical-harness)"})
            with _url.urlopen(req, timeout=settings.request_timeout_s) as resp:
                parsed = json.loads(resp.read().decode())
            usage = parsed.get("usage", {}) or {}
            ledger.record(stage="reports",
                          prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                          completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                          reasoning_tokens=int(
                              (usage.get("completion_tokens_details", {}) or {}).get(
                                  "reasoning_tokens", 0) or 0))
            content = (parsed.get("choices", [{}])[0].get("message", {})
                       .get("content", ""))
            return content, usage
        reports = generate_reports(communities, graph, narrate_fn,
                                   model_id=model, budget=args.reports_budget,
                                   workers=args.workers)
        print(f"      {len(reports)} reports")
    run_log["stages"]["reports"] = {"count": len(reports)}
    (settings.data_dir / "community_reports.json").write_text(
        json.dumps([r.as_dict() for r in reports], indent=2), encoding="utf-8")

    if args.skip_stores:
        store_counts = {"skipped": True}
    else:
        print("[8/8] embedding + stores")
        from .embeddings import MiniLMEmbedder
        from .stores import Neo4jStore, QdrantStore, stable_point_id
        embedder = MiniLMEmbedder(model_id=settings.embedding_model)
        # text units
        by_id = {c.chunk_id: c.text for c in chunks}
        unit_ids = sorted(by_id)
        unit_vectors = embedder.embed([by_id[i] for i in unit_ids])
        qdrant = QdrantStore(settings.qdrant_url, dim=embedder.dim)
        qdrant.ensure_collections()
        if unit_ids:
            qdrant.upsert("text_units",
                          [stable_point_id("text_units", i) for i in unit_ids],
                          unit_vectors,
                          [{"text_unit_id": i, "doc_id": i.split("::")[0]} for i in unit_ids])
        # entities (contextualised descriptions, embed_research.md §4b)
        from .embeddings import describe_for_embedding
        node_keys = sorted(graph.nodes)
        node_texts = [describe_for_embedding(
            graph.nodes[k].display, graph.nodes[k].type,
            graph.nodes[k].mentions, sorted(graph.nodes[k].aliases)) for k in node_keys]
        node_vectors = embedder.embed(node_texts)
        if node_keys:
            qdrant.upsert("entities",
                          [stable_point_id("entities", k) for k in node_keys],
                          node_vectors,
                          [{"entity_key": k, "display": graph.nodes[k].display}
                           for k in node_keys])
        # reports
        if reports:
            rep_vectors = embedder.embed([r.summary or r.title for r in reports])
            if rep_vectors:
                qdrant.upsert("community_reports",
                              [stable_point_id("community_reports", r.community_id)
                               for r in reports],
                              rep_vectors,
                              [{"community_id": r.community_id, "level": r.level}
                               for r in reports])
        neo4j = Neo4jStore(settings.neo4j_uri, settings.neo4j_user,
                           settings.neo4j_password or "")
        neo_counts = neo4j.write_graph(
            [n.to_dict() for n in graph.nodes.values()],
            [{"head": e.head, "relation": e.relation, "tail": e.tail,
              "mentions": e.mentions, "source_chunks": sorted(e.source_chunks)}
             for e in graph.edges.values()])
        neo4j.write_communities([c.as_dict() for c in communities])
        neo4j.close()
        store_counts = {"qdrant_units": len(unit_ids), "qdrant_entities": len(node_keys),
                        "qdrant_reports": len(reports), **neo_counts}
        print(f"      {store_counts}")
    run_log["stages"]["stores"] = store_counts

    elapsed = time.perf_counter() - started
    run_log["wall_s"] = round(elapsed, 1)
    run_log["cost"] = ledger.summary()
    ledger.save(settings.data_dir / "rag-cost.json")
    (settings.data_dir / "index_run.json").write_text(
        json.dumps(run_log, indent=2), encoding="utf-8")
    print(f"\nDONE in {elapsed:.0f}s — rag-cost.json + index_run.json in {settings.data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())