# dev/ — dev & test-only tooling (fast local alternatives)

Not product code. Not run by `make validate`. Safe to change or delete.

## Why this exists
Production (Track A reading Track B) uses **Qdrant + Neo4j + LLM extraction**
(`graphrag/pipeline.py`). For fast local iteration and end-to-end testing we use
**fast in-process alternatives** with zero Docker:

| Concern | Production | Dev/test (here) |
|---|---|---|
| Vector store | Qdrant (Docker) | `dev/faiss_store.py` FAISS FlatIP (cosine) |
| Graph store | Neo4j (Docker) | networkx (`medharness.stores.graph.GraphStore`) |
| Embeddings | `all-MiniLM-L6-v2` (384-dim) | same model, cached locally |
| Index build | `graphrag/pipeline.py` | `run_e2e.py --index` (same prompt/parse helpers) |
| Decisions | Laya `laya-serve` on `:8000` | Laya if reachable, else recorded fixtures |
| Generation | hosted Qwen | hosted Qwen (`KAGGLE_LLM_BASE_URL`) |

## Files
- `faiss_store.py` — dev FAISS store, same method names as
  `graphrag.stores.MemoryVectorStore` (`upsert/search/count/save/load`).
  `IndexFlatIP` over normalised vectors (= cosine); payloads in a dict sidecar;
  `payload_filter` falls back to brute force (small dev corpus). Lazy `faiss`
  import with a named error.
- `build_fast_engine.py` — legacy loader: `data/graph.json` into
  `MemoryVectorStore` + in-memory adjacency → same `QueryEngine`. Kept for
  reference; new runs use `--index` checkpoints.
- `run_e2e.py` — checkpoint runner: `--index` ingests `--corpus-dir` once
  (MiniLM → FAISS, hosted-Qwen extraction → networkx, communities +
  reports from the same tunnel LLM, all under `--checkpoint-dir`
  default `dev/data`, resumable via `triples.jsonl`); `--ask "..."` loads the
  checkpoint and runs the FULL medharness system (`Orchestrator.assess`,
  Laya → rules → Qwen, global search included). No harness logic lives
  here — only wiring.

## Run
```bash
python3 dev/run_e2e.py --index --corpus-dir data/corpus_test --limit 1   # -> dev/data/
python3 dev/run_e2e.py --ask "What is hypertension?" --role patient
python3 dev/run_e2e.py --ask   # safe + escalate demo pair
MEDH_LIVE=1 python3 dev/run_e2e.py --ask "..."   # force Laya at :8000
```

Extraction uses `KAGGLE_LLM_BASE_URL` (default the pond-breathing tunnel) +
`KAGGLE_LLM_MODEL`, `--batch 1` (hosted Qwen emits clean JSON only unbatched).
Checkpoint layout (`dev/data/`): `chunks.json`, `graph.json`, `networkx.json`,
`communities.json`, `community_reports.json`, `faiss/`, `triples.jsonl`,
`meta.json`.

Recorded in `docs/decisions.md` (dev-vs-production stores) and `docs/runbook.md`.
