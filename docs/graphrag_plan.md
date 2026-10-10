# graphrag_plan.md — the GraphRAG indexer, end to end

**Status:** draft v1 · 2026-10-09 · **Track B** of two deliverables (see `current_tasks.md`)

Built to the design record in `docs/rag_docs/`. This file is the **plan**; the
package that implements it is `graphrag/`.

---

## 1. What this is

A knowledge-graph indexer. It takes a folder of documents and produces:

- a **graph** — entities and typed relationships, each carrying the text chunks it came from
- **embeddings** — node and chunk vectors in Qdrant
- **communities** — Leiden clusters with LLM-written reports

The harness's `kb_search` tool reads that index at query time. **The indexer never
runs inside the harness, a test, or `make validate`** (`Plan.md` D19).

### Design decisions taken

| Decision | Choice | Why |
|---|---|---|
| Extraction | **LLM, one prompt per chunk** | `NER_model.md`'s encoder swap rejected. LLM returns `{"entities":[…],"triples":[…]}` |
| Store | **Qdrant + Neo4j**, Docker | `Plan.md` D6/D7; GraphRAG needs a graph store |
| Embeddings | **`all-MiniLM-L6-v2`**, local, 384-dim | no embedding API, no cost, reproducible |
| System-1 model | **Laya**, local at `127.0.0.1:8000` | routing, reranking, merge judging — never generation |
| Ollama | **not used** | its `laya` is an MLX/Apple build; unusable on Linux |
| Reranking + routing | **per `docs/rag_docs/`** | two-door seeds, evidence rerank, vector fallback, U5/U7/U8 |

---

## 2. The pipeline

### Ingestion — I1 … I11

```
data/corpus_test/*.txt          the Wikipedia medical test set
        │
 I1 ─── Load            document records
 I2 ─── Chunk           paragraph-aware, overlap, provenance id  Doc::7
 I3 ─── Extract         LLM per chunk  →  triples.jsonl          ← the one big cost
 I4 ─── Checkpoint      content-hash cache; never re-bill
 I5 ─── Validate gate   reject vague / self-loop / >7-token / malformed
 I6 ─── Normalize       canonical merge key + UPPER_SNAKE relations
 I7 ─── Aggregate       one edge, mentions++ , source_chunks[]
 I8 ─── Type backfill   only edges with mentions >= 2 get a vote
 I9 ─── Merge funnel    blocking → fuzzy → Laya/LLM judge → rebuild + audit
I10 ─── Communities     hierarchical Leiden
I11 ─── Embed           MiniLM → Qdrant (entities, text_units, community_reports)
```

### Query — Q0 … QG

```
Q0  probe        embed the question once, reuse everywhere
Q1  route        Laya choice → local | global | basic | none
Q2  seeds        door 1: names   ∪   door 2: vectors
Q3  expand       2-hop typed walk ∪ chunk fetch
Q4  rank         Laya rerank blended with cosine → top-k
Q5  narrate      LLM composes, every claim cited
Q6  guard        Laya checks claims against their chunks
QG  global       map over community reports → filter → reduce
```

**No path ends in silence.** No seed → vector fallback; nothing above threshold
→ honest refusal with a named reason.

---

## 3. Three invariants that cannot be broken

| # | Invariant | Enforced by |
|---|---|---|
| 1 | **Every citation resolves to a real text unit in the corpus** | `source_chunks` on every edge; an unresolvable id is a **hard failure**, not a warning (`PROMPT.md` §7.3.4) |
| 2 | **A citation never resolves to a generated entity/edge description or a community report** | source ids are typed (`text_unit:` / `community:`); summaries are model output and are not sources (`Plan.md` D20) |
| 3 | **Extraction is paid once** | `triples.jsonl` + content-hash cache; every downstream stage rebuilds for free |

Plus the standing rule from `rag_docs/solution.md`:
**over-merging is worse than under-merging.** One false merge poisons every
future traversal; a split node only costs some edge weight.

---

## 4. The main medical corpus — GitHub Actions batch job

The local box is 2 cores, no GPU, 7.8 GB RAM. A multi-GB corpus will not index
here inside the model-access window. So the heavy run moves to GitHub Actions.

```
┌─ GitHub Actions (scheduled + manual dispatch) ─────────────────────┐
│                                                                     │
│  1. stream corpus          fetch / walk documents                    │
│  2. chunk                  paragraph-aware, provenance ids           │
│  3. extract                LLM  ── every chunk, checkpointed         │
│  4. structure              validate → normalize → aggregate → merge  │
│  5. embed                  MiniLM → Qdrant                          │
│  6. UPLOAD  ───────────►   HuggingFace, every 1–1.5 hours            │
│                             checkpoints survive runner death         │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
              download artifacts → local Qdrant + Neo4j
```

### Why upload every 1–1.5 hours

- A 6–12 hour job on a 2–4 CPU runner **will** be interrupted — runner limits,
  network blips, cancelled jobs.
- Uploading on a cadence means **the run is never lost**: the last checkpoint is
  already on HuggingFace and the next run resumes from it.
- It is the same content-hash checkpoint discipline as `triples.jsonl`, one level
  up.

### Artifacts uploaded

| File | What |
|---|---|
| `graph.json` | nodes + edges with `mentions`, `aliases`, `source_chunks` |
| `merge_log.json` | every nomination, verdict and refusal — the audit trail |
| `triples.jsonl` | the extraction checkpoint (**delete this and you re-pay the whole run**) |
| `failed_chunks.json` | chunk ids that failed permanently, with reason |
| `communities.json` | Leiden levels |
| `rag-cost.json` | tokens, calls, wall time, cost per stage |
| embeddings | Qdrant snapshot or raw vectors, chunked for LFS |

### Configuration — all from repository secrets, never committed

| Secret | Used by |
|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID` | extraction, descriptions, community reports |
| `HF_TOKEN` | upload/download |
| `HF_REPO_ID` | target Space or Dataset repo |
| `NEO4J_PASSWORD`, `QDRANT_URL` | the stores |

> **HuggingFace Spaces vs Dataset repo.** A Space is an app container and will
> happily hold files, but artifacts are not an app — a **dataset repo** is the
> right home, with LFS for vectors. Spaces can also hold the artifacts if that is
> preferred, but the demo app and the data should not be the same object.
> **Decide before the first upload.**

### Runner reality

| | |
|---|---|
| `ubuntu-latest` | 4 CPU / 16 GB — better than our 2-core box, still no GPU |
| Free minutes | unlimited on **public** repos; 2,000/month on private |
| Consequence | the corpus and any code committed are **public**. Wikipedia is CC BY-SA and fine; check licensing on anything else |
| LLM quota | the real limit. A free tier 429s almost immediately — extraction wants tens of thousands of calls |

---

## 5. Why this can still be slow, stated plainly

LLM extraction's floor is **output-token throughput**, not call count. Batching
cuts HTTP overhead; it cannot beat the provider's tokens/second.

| Corpus | Chunks | Output tokens | @5k tok/s | @10k tok/s |
|---|---|---|---|---|
| 500 MB | 114k | 28M | 1.6 h | 47 min |
| 1 GB | 228k | 57M | 3.2 h | 1.6 h |
| 4 GB | 900k | 225M | 12.5 h | 6.3 h |

Levers that genuinely move it, in order of effect:

1. **Tight output** — `max ~10 triples`, terse JSON. Output is the bottleneck; halve it, halve the time.
2. **Real quota** — concurrency 20+ with 429 backoff. Free tiers die on call ~4.
3. **Pre-filter** — drop references, tables, TOC, and out-of-domain documents before they cost anything.
4. **Batch ~8 chunks/call** — fewer HTTP rounds; does not reduce tokens.

**Not used: the encoder.** It would have removed the floor entirely (and made
4 GB a 2–6 hour local CPU job). It is rejected by decision — see
`docs/decisions.md`.

---

## 6. Testing plan

| Suite | Network | LLM | Gate |
|---|---|---|---|
| `graphrag/tests/` unit | none | none | runs in `make test`; **no API key needed** |
| smoke | none | fixture | 3 documents offline, asserts ids resolve |
| live | yes | real key | **manual only**, never in `make validate` |

The first milestone is the unit suite, because chunking, normalization, the
validation gate and aggregation are all pure functions and must be provable
before a single token is spent.

---

## 7. Open questions

1. **HF Spaces or dataset repo?** (above — decide before first upload)
2. **Which medical corpus** for the main run, and its licence? Still `PROMPT.md` §19.4.
3. **Entity types** — `rag_docs` uses `PERSON/ORGANIZATION/LOCATION/PRODUCT/EVENT/MISC`;
   ours needs `symptom, condition, test, drug, procedure, body_system, guideline, population`.
   The medical list is not domain-reviewed yet.
4. **Graph size at query time** — a 2-hop walk from a hub can exceed the 255
   cardinality ceiling for System-1 reranking. Design the 2-stage split before it bites.
5. **Community hierarchy level** for global search — a recorded setting, not a default nobody knows.