# current_tasks.md — Track A medical harness build (simple stores, dev speed)

**Updated:** 2026-10-09 · **Status:** Track A S0–S11, 75 tests green, `make arch` passes;
**first LIVE Laya run done** (2 adapter bugs found + fixed), dev retrieval now FAISS.
Run `python3 -m medharness.demo` (fixtures, offline). Live: `MEDH_LIVE=1` (Laya :8000 —
it IS up, `decisions : laya @ … (available=True)`), `MEDH_ENABLE_LLM=1` (hosted Qwen).
Dev retrieval: `python3 dev/run_e2e.py --index --corpus-dir data/corpus_micro` then
`--ask "..."` (FAISS + networkx checkpoint in `dev/data/`). The old coding-agent
`harness/` (Track H) was deprecated → `deprecated/harness/` (zero code dependency).
**User order:** fast + simple. Vector = **FAISS** (dev) / Qdrant (prod), graph =
networkx (dev) / Neo4j (prod). Embeddings = local MiniLM. Decisions = Laya
(System-1, local `laya-serve`). Generation = hosted Kaggle Qwen
(`docs/hosting_qwen_kaggle.md`, client-side `<tool_call>` pattern — NO `tools=`
param, server ignores it).
**Amendment note:** `PROMPT.md` §7.4 says Qdrant+Neo4j in Docker; this build uses
FAISS + networkx for dev speed, with Qdrant/Neo4j as the production target.
Recorded in `docs/decisions.md` 2026-10-09.

## Right now

- 🔴 **OPEN for M11+ / baseline:** live Laya **over-elicitates red flags** — chest-pain
  text fires dyspnea (0.71) and severe abdominal pain (0.78) as well as chest pain
  (0.93). Recall is good, precision is poor. Measure on `evals/red_flags` before any
  threshold change; never tune on `holdout`. Expected §14.3 finding (no medical training).
- 🔴 **OPEN, baseline measurement:** full 12-question set latency ~12.4 s vs the §7.1
  ≤150 ms budget (~80×). Single question warm ~0.5 s. Report p95 at M12; do not "fix"
  by dropping questions (protocol is fixed and versioned, §8.2).
- 🔴 **Live acuity confidence is low** (~0.20–0.27 on simple text) → `abstained=True`
  is common on the live path. Abstain must gate on salient keys only (already true);
  watch that §9's abstain+flag≥0.40 branch never fires spuriously.
- **Next:** record live fixtures for the 20 synthetic cases (M2 deliverable), then
  M11 eval sets (`evals/sets/{scenarios,red_flags,refusals,holdout}.jsonl`) and the
  M12 baseline report — including the bad numbers above.

## Build order (S = step, each = code + test + tick below)

- [x] **S0 scaffold** — `src/medharness/` tree, `pyproject`, arch-check script,
      `data/questions/*.json` (§8 verbatim), `data/tables/*.json`, `.env.example`
- [x] **S1 simple stores** — `stores/vector.py` (numpy cosine, top-k, filter) +
      `stores/graph.py` (networkx entities/edges/`source_chunks`, 2-hop walk) +
      `stores/seed_corpus.py` (4 docs → chunks → MiniLM embed → both stores)
- [x] **S2 decision client** — `decision/client.py` Protocol + `http_adapter.py`
      (Laya `POST /v1/systemone`, one batched call) + `fixture_adapter.py` (20+
      recorded cases; ALL tests use fixture, no network)
- [x] **S3 rule engine** — `rules/thresholds.py` v1 + `rules/engine.py` (§9, 5
      branches, downgrade-forbidden) + boundary tests + `make arch` green
- [x] **S4 lookup tools** — `tools/labs.py` + `tools/drugs.py` (pure file lookup,
      `not_found` never guess) + `tools/registry.py` (role matrix §6)
- [x] **S5 kb_search** — `tools/kb.py` over S1 stores (name-seeds ∪ vector-seeds
      → 2-hop walk ∪ chunk fetch → rerank → top-k; `text_unit:`-only ids,
      hard-fail unresolvable; `mode:none` allowed; stores-down → degraded)
- [x] **S6 generation** — `generation/qwen_client.py` (client-side tool loop from
      `test/local_kaggle_llm.py`) + `generation/policy.py` (refusals, crisis
      verbatim from config, banned-phrase, citation gate)
- [x] **S7 orchestrator** — `orchestrator.py assess()` L1→L2→[L3]; raising-LLM
      stub proves 0 calls on escalation; verbatim instruction; full trace §12;
      byte-identical decisions
- [x] **S8 edge + demo** — FastAPI `POST /assess`, `GET /health`, role header;
      end-to-end demo on synthetic cases (escalated + safe)
- [x] **S9 external docs tool** — `tools/external_search.py`: `search_external_docs(query, mode)`.
      Two-tier retrieval: internal `kb_search` first; when the model judges it
      insufficient it calls `search_external_docs` → **full graph walk** (local
      2-hop over networkx offline, or `graphrag.QueryEngine` local+community live)
      → docs return as `text_unit:` facts → LLM answers grounded. `GlobalSearchClient`
      degrades to a named refusal offline. Verified: scripted kb→external→answer cites
      `text_unit:chest_pain::0`. 46 tests green.
- [x] **S10 handoff** — `receipt.py` (reproducible fingerprint) + `handoff.py`
      (clinician handoff + intake JSON, disclaimer verbatim) + `security_demo.py`.
- [x] **S11 live Laya** — `laya-serve` up on `:8000`; `decision/client.py` fixed to
      parse the **live** wire shape (`noul` numeric, `score` numeric keys + `legend`)
      and to derive the expected acuity from the **distribution**, not the argmax
      (§8.1 line 189). `tests/test_decision_client.py` new. 75 tests green.
- [x] **S12 FAISS dev retrieval** — `dev/faiss_store.py` + checkpoint-style
      `dev/run_e2e.py --index|--ask` (FAISS + networkx + communities in `dev/data/`).

## Now / next / later — archive (pre-Track-A state, kept for record)

### Was-right-now (2026-10-09, superseded by the build above)

## Right now

- 🔴 *(archived)* **Track H P1 core was a build**: the old coding agent
  `harness/` — now **deprecated**, moved to `deprecated/harness/`. Superseded by
  the medical harness (`src/medharness/`).
- 🔴 **Fresh pipeline run in progress** (background): 16-doc corpus (~700 KB,
  191 chunks), parallel-reports patch live. Old run killed, artifacts + stores
  wiped. Timing lands in `data/index_run.json` + `data/rag-cost.json` when done —
  report it to the user, don't leave it unread.
- 🔴 **2 docs still missing**: `Sepsis` + `Anemia` skipped on Wikipedia 429.
  Retry `python -m graphrag.wikipedia "Sepsis" "Anemia"` after a long pause
  (45s was not enough) before calling the corpus done.

- 🔴 **Fresh pipeline run in progress** (background): 16-doc corpus (~700 KB,
  191 chunks), parallel-reports patch live. Old run killed, artifacts + stores
  wiped. Timing lands in `data/index_run.json` + `data/rag-cost.json` when done —
  report it to the user, don't leave it unread.
- 🔴 **2 docs still missing**: `Sepsis` + `Anemia` skipped on Wikipedia 429.
  Retry `python -m graphrag.wikipedia "Sepsis" "Anemia"` after a long pause
  (45s was not enough) before calling the corpus done.

## Right now

- **Track B unit suite: 152 tests, green** (stdlib only, no network, no key).
- **Full pipeline re-running** on the 5-article Wikipedia medical corpus
  (`data/corpus_test/`, ~220 KB) after the `reasoning_effort="minimal"` fix.
  First run failed 59/59 chunks (reasoning saturation → empty completions);
  state reset, re-running with batch 4 / workers 4. Watch for `data/rag-cost.json`.
- **Stores are up**: Qdrant 1.19.2 + Neo4j 5-community via `graphrag/compose.yaml`.
- **Decision logs consolidated**: `docs/decision.md` merged into
  `docs/decisions.md` (single log) and removed; `AGENTS.md` file map updated.

---

## The split — two separate things

This repository now carries **two independent deliverables**. They are not the same
system and must not be tangled together.

| # | Deliverable | Lives in | Purpose |
|---|---|---|---|
| **A** | **The medical decision-support harness** | `src/medharness/`, `PROMPT.md`, `Plan.md` §2 | The product. Triage, escalation, cited explanation. Built (S0–S10). |
| **B** | **The GraphRAG indexer** | `graphrag/` (this new folder) | The knowledge-graph builder that produces the index deliverable B reads. **Separate project.** |

**The one interface between them:** deliverable B writes a graph and vector
index; deliverable A's `kb_search` tool reads it. B is **build-time and offline**
(`Plan.md` D19). A never imports B, and B never imports A.

---

## Right now: coding deliverable B — the GraphRAG system

**Folder:** `graphrag/` — a self-contained Python package with its own tests,
its own config, and no dependency on the medical harness.

**Built to the design record in `docs/rag_docs/`:**

| Doc | What we take from it |
|---|---|
| `README.md` | The three defining choices: LLM extraction, cheap-nominate/expensive-decide, graph holds structure / text stays retrievable |
| `graph_making.md` | The pipeline: chunk → extract → validate → aggregate → checkpoint |
| `solution.md` | The entity-merge funnel: blocking → fuzzy → judge, and its three failure modes |
| `embed_research.md` | Two-door seed finding, vector fallback, evidence reranking |
| `system-1-model.md` | System-1 insertion points (U1–U8), confidence gates, the evaluation ladder |
| `full_Architecture.md` | Ingestion I1–I11, query Q0–QG, artifact chain |

**Explicit decisions taken 2026-09-10:**

- **LLM extraction, not the encoder.** `NER_model.md`'s writer→labeler swap is
  **rejected**. Extraction stays a prompted LLM returning
  `{"entities": [...], "triples": [...]}`.
- **Reranking and routing follow `rag_docs/`** — two-door seed finding, evidence
  reranking, vector fallback, and the System-1 router/guardrail.
- **System-1 model is Laya**, hosted locally at `http://127.0.0.1:8000`
  (`/v1/systemone`). Not Ollama — its `laya` build is MLX/Apple-only.
- **Store: Qdrant + Neo4j in Docker**, per `Plan.md` D6/D7 and
  `docs/rag_implementationplan.md`.

---

## Now / next / later

### Now — in this session
1. `current_tasks.md` (this file), `docs/graphrag_plan.md`, and the `Plan.md` track entry.
2. Core package, **stdlib only, no network, no API key needed**: config,
   normalization + validation gate, chunking, graph store, checkpoint.
3. LLM extraction adapter (`graphrag/llm.py`) + cost ledger (`graphrag/cost.py`),
   wired to OpenCode Zen — ✅ done, live-verified.

### Next
4. Entity-merge funnel — blocking → fuzzy → **Laya judges** → rebuild + audit log.
5. Stores — Neo4j graph, Qdrant vectors, local **MiniLM** embeddings.
6. Wikipedia **medical** test corpus scraped into `data/corpus_test/`, indexed,
   `make`-able smoke run with `rag-cost` reporting.

### Later
7. Query layer — Q0 probe, Q1 route (Laya U7), Q2 two-door seeds, Q3 walk∪fetch,
   Q4 rerank (U5), Q5 narrate, Q6 guard (U8), QG global.
8. **Main medical corpus via GitHub Actions** — see `docs/graphrag_plan.md`.

---

## Blocked on

| What | From | Blocks |
|---|---|---|
| ~~`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID` — a **generative** model~~ | ✅ resolved 2026-10-09 | — |
| HF token + space/dataset repo name | user | the GitHub Actions upload job |
| GitHub Actions runner secrets setup | user | the batch indexer |

> ✅ **LLM backend answered via OpenCode Zen.** Key `OPENCODE_ZEN_API`, model
> `space-bunny-free`, base `https://opencode.ai/zen/v1`. Resolved in
> `graphrag/config.py` (`LLM_*` vars still win if set). First live extraction
> ran 2026-10-09: batch of 2 → 2 results, valid contract JSON, fed into the
> graph. See the decision log for measured latency and the reasoning-token tax.

> ⚠️ `LIQUILD_AI_API` (`d1:free`) **cannot** do extraction — it is a decision
> model and every call returns `output_tokens: 0`. It scores choices, it does
> not write JSON. A generative model is required.

---

## Repo-rule notes

- `graphrag/` sits **outside** `Plan.md` §4.1's layout. Recorded as an amendment
  in `docs/decisions.md` on 2026-10-09; `Plan.md` gained a Track B rather than
  this being done quietly.
- No secrets in code. Keys come from `.env` / GitHub Actions secrets only.
- The GraphRAG indexer is **build-time and offline**. It never runs inside
  `make validate` (`Plan.md` D19).