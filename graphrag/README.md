# graphrag/ — Track B, the GraphRAG indexer

A standalone knowledge-graph builder. **Not part of the harness.** Nothing in
`src/harness/` imports this, and this imports nothing from there.

Plan: [`../docs/graphrag_plan.md`](../docs/graphrag_plan.md) · Design record:
[`../docs/rag_docs/`](../docs/rag_docs/) · Status:
[`../current_tasks.md`](../current_tasks.md)

## What is built so far

| Module | Stage | What it does |
|---|---|---|
| `config.py` | — | env-driven settings; **fails loudly** if a generative LLM is missing |
| `textnorm.py` | I5, I6 | merge keys, relation labels, and the validation gate |
| `chunking.py` | I2 | paragraph-aware chunking with stable provenance ids |
| `graph.py` | I5, I7, I8 | property graph, aggregation, provenance, pruning, type backfill |
| `checkpoint.py` | I4 | `triples.jsonl` + content-hash cache + `failed_chunks.json` |
| `llm.py` | I3 | extraction adapter: batched prompts, retries, salvage, batch-mismatch rejection |
| `cost.py` | — | per-stage token/latency ledger — the data behind `rag-cost` |

Not yet built: extraction adapter (I3), merge funnel (I9), stores (I10, I11),
query layer (Q0–QG).

## Rules this package keeps
1. **LLM output is a suggestion, not data.** Every triple is validated,
   normalised and counted before it exists. A rejection returns a *reason*, so a
   silent drop is distinguishable from a chunk that was never extracted.
2. **Over-merging is worse than under-merging.** The suffix whitelist strips
   `inc`/`corp`/`ltd` and keeps `motors`/`group`/`energy` — because "General
   Motors" and "Tesla Energy" are real names.
3. **Mention counts are free confidence.** Real relationships are re-extracted
   across documents; junk appears once.
4. **Every edge keeps its `source_chunks`.** That list is what makes a citation
   resolve to real corpus text (`PROMPT.md` §7.3.4), and the reason a generated
   description can never satisfy one (`Plan.md` D20).
5. **Extraction is paid once.** `triples.jsonl` plus the content-hash cache mean
   re-running after a merge-logic change costs no tokens.
6. **No secret in code.** Keys come from `.env` locally, repository secrets in CI.

## Tests

Stdlib `unittest`. **No network, no API key, no LLM.**

```bash
python3 -m unittest discover -s graphrag/tests -t .
```

pytest also collects these, so they run under `make test` once M0 exists.

## Configuration

All from the environment; `.env` is read if present and real env vars win.

| Variable | Default | Needed for |
|---|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_ID` | Zen fallback / **required** / **required** | **extraction** (a *generative* model) |
| `OPENCODE_ZEN_BASE_URL` | `https://opencode.ai/zen/v1` | Zen endpoint override |
| `OPENCODE_ZEN_API` | — | Zen key (used when `LLM_API_KEY` is unset) |
| `OPENCODE_MODEl` / `OPENCODE_MODEL` | — | Zen model id: currently `space-bunny-free` |
| `LAYA_BASE_URL` / `LAYA_MODEL_ID` | `http://127.0.0.1:8000` / `laya` | System-1 decisions |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | local Docker | the graph store |
| `QDRANT_URL` | `http://127.0.0.1:6333` | the vector store |
| `GRAPHRAG_CORPUS_DIR` | `corpus_test` | which corpus under `data/` |
| `GRAPHRAG_CHUNK_CHARS` / `GRAPHRAG_CHUNK_OVERLAP` | `4800` / `400` | chunk size |
| `GRAPHRAG_MAX_TRIPLES` | `10` | **the dominant cost lever** — output tokens |

> The locally hosted System-1 model **cannot** extract. It returns calibrated
> probabilities and `output_tokens: 0`; it never writes text. `config.require_llm()`
> says so explicitly rather than failing later with a parse error.

## Extraction contract

```json
{"entities": [{"name": "...", "type": "..."}],
 "triples":  [{"head": "...", "relation": "...", "tail": "..."}]}
```

Entity types are the medical set (`symptom`, `condition`, `test`, `drug`,
`procedure`, `body_system`, `guideline`, `population`) — **not yet
domain-reviewed**, see `docs/graphrag_plan.md` §7.