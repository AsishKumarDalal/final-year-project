# current_tasks.md — what is being built right now

**Updated:** 2026-10-09 · **Status:** active — Track H docs done, P1 core next

## Right now

- 🔴 **Track H P1 core is the build**: `harness/agent/{prompt,llm}.py` →
  `tools/registry.py` → `agent/loop.py` (sequential) → `main.py`. Blocked on
  one approval: `pip install openai` (Implement.md S2 — ask before installing).
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
| **A** | **The medical decision-support harness** | `src/harness/`, `PROMPT.md`, `Plan.md` §2 | The product. Triage, escalation, cited explanation. Unbuilt. |
| **B** | **The GraphRAG indexer** | `graphrag/` (this new folder) | The knowledge-graph builder that produces the index deliverable B reads. **Separate project.** |

**The one interface between them:** deliverable B writes a graph and vector
index; deliverable A's `kb_search` tool reads it. B is **build-time and offline**
(`Plan.md` D19). A never imports B, and B never imports A.

---

## Right now: coding deliverable B — the GraphRAG system

**Folder:** `graphrag/` — a self-contained Python package with its own tests,
its own config, and no dependency on `src/harness/`.

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
1. `current_tasks.md` (this file), `graphrag_plan.md`, and the `Plan.md` track entry.
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
8. **Main medical corpus via GitHub Actions** — see `graphrag_plan.md`.

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