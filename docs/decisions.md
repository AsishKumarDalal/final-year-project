# Decision Log

**Append-only.** Newest at the top of each section. Never rewrite an entry — supersede it.

For amendment procedure see `Plan.md` §6. For settled design decisions made up front, see `Plan.md` §7 (`decision notes`).

---

## 2026-10-09 — running log (small decisions, kept with the amendments)

- **Laya live locally for the first time; two live-adapter bugs found and fixed**
  (2026-10-09, user-ordered "make up laya"). `pip install "laya[serve]"`
  (laya-0.4.1) + `laya-serve` on `:8000`, `english` checkpoint rev `7b928d82`,
  CPU. **First live run exposed that the live path was silently blind** — the
  harness had only ever been exercised against fixtures:
  - **Bug 1 — `noul` numeric shape.** live laya-serve returns
    `{"type":"noul","noul":0.9306,"confidence":0.9306}` with **no
    `probabilities` dict**, while fixtures use `{"noul":"true",
    "probabilities":{"true":0.94,...}}`. `noul_probability()` only read the
    fixture shape, so **every live red flag parsed to 0.0 and red-flag recall was
    0% on the live path** — the §15.2 release blocker would have failed live.
    Fixed in `decision/client.py`; polarity verified live (true text → 0.9306,
    false text → 0.1113). This is memory.md's upstream issue #156 manifesting as
    a *parsing* bug, not only the label bug already noted.
  - **Bug 2 — `score` distribution numeric keys + argmax.** live `score`
    answers key probabilities numerically (`{"0":0.12,"1":0.37,"2":0.50,"3":0.009}`)
    and supply a `legend`. Nothing read the legend, so a real distribution
    parsed as **uniform 0.25 each**, which always breached §9's
    `P(resuscitation) ≥ 0.10` — **every live query escalated**. Fixed the
    legend mapping (`score_distribution`), then found the *second-order* cause:
    even with the legend, `parse_verdict` took the **argmax** of the distribution
    as the expected level, which contradicts **PROMPT.md §8.1 line 189 — escalate
    on the distribution, "not the point estimate alone"**. Laya genuinely scores
    a routine cold `{routine .12, soon .37, urgent .50, resus .009}`; argmax says
    `urgent` → escalate. The expected ordinal level is now derived from the
    distribution (E[level]) whenever top mass < 0.5, which restores the ordering
    the four levels are meant to carry: cold → E=1.40 → `soon`; severe chest pain
    → top mass .75 → `urgent`; unresponsive → top mass .91 → `resuscitation`.
    No threshold was changed. Regression tests: `tests/test_decision_client.py`
    (new, 6 cases) — fixture shape, live shape, malformed, legend resolution,
    argmax→distribution, and a genuine emergency still escalating.
  - Result: cold → `decision=explain` (LLM reached); chest pain →
    `decision=escalate`, `llm_called=false`. 75 tests green, `make arch` green.
    Baseline material (carry to M12, do not tune against): live single-question
    latency ~0.5 s warm but the **full 12-question set is ~12.4 s vs the §7.1
    ≤150 ms budget (~80×)**; live acuity `confidence` on simple text is
    ~0.20–0.27, so `abstained=True` is common; Laya over-elicitates flags
    (chest-pain text also fired dyspnea 0.71, abdominal pain 0.78) — recall good,
    precision poor, exactly §14.3's expectation for an untrained checkpoint.
- **Dev retrieval moved from MemoryVectorStore to FAISS + a checkpoint-style
  runner** (2026-10-09, user-ordered). `dev/faiss_store.py` — `FaissVectorStore`
  with the same method names as `graphrag.stores.MemoryVectorStore`
  (`ensure_collections/upsert/search/count`, plus `save/load` for
  checkpoints), `faiss.IndexFlatIP` over L2-normalised vectors (= cosine), JSON
  payload sidecar, and a brute-force path when `payload_filter` is set (small
  dev corpus — correctness beats sharding). `faiss` is imported lazily and is a
  **dev-only** dependency — `pyproject.toml` untouched. `dev/run_e2e.py` is now a
  two-command checkpoint flow:
  - `python3 dev/run_e2e.py --index --corpus-dir data/corpus_micro` — chunk →
    extract via the hosted-Qwen tunnel (`qwen2.5-32b-instruct`,
    `https://pond-breathing-foto-advocate.trycloudflare.com/v1`, `--batch 1`
    because batching drops JSON) → build the graphrag `Graph` **and** a networkx
    `GraphStore` for the harness → partition communities → narrate reports →
    embed with MiniLM → write FAISS + `communities.json` +
    `community_reports.json`, all into `dev/data/` (`chunks.json`, `graph.json`,
    `networkx.json`, `communities.json`, `community_reports.json`, `faiss/`,
    `triples.jsonl`, `meta.json`). Resumable (`triples.jsonl` + content-hash
    `cache/`), `--skip-reports` to stay fast, `--limit N` for a small dry run.
  - `python3 dev/run_e2e.py --ask "question"` — loads the checkpoint and runs the
    full `medharness` system unchanged (`Orchestrator.assess`, Laya-or-fixtures,
    hosted Qwen, `GraphRAGExternalSearch`). Community reports are fed to
    `QueryEngine.community_reports`, so global search is available.
  - **No harness logic is reimplemented in `dev/`** — only wiring.
  - **Measured:** fresh 1-doc/1-chunk index = **51 s wall** (~40 s of it the
    single extraction call). 1-doc/15-chunk ≈ 11–12 min + reports. `--ask` prints
    a checkpoint summary (`chunks=… nodes=… units=…`). The `Loading weights …`
    MiniLM bar on every process is silenced with
    `HF_HUB_DISABLE_PROGRESS_BARS=1` (set in the runner; the ~4 s model load
    itself is per-process and unavoidable without a long-lived service).
  - `dev/build_fast_engine.py` is now **legacy** (still works, kept for
    reference); new runs use the checkpoint flow.

- **Dev/test uses fast local stores; production uses Qdrant + Neo4j** (2026-10-09,
  user-ordered). For iterating and end-to-end testing without Docker, the harness
  reads the SAME graph the real pipeline builds but held in-process:
  - **Fast vector store:** `graphrag.stores.MemoryVectorStore` (in-process numpy cosine)
    instead of Qdrant. **Fast graph store:** the in-memory `graphrag.graph.Graph`
    (nodes/edges dicts) instead of Neo4j. **Embeddings:** `MiniLMEmbedder` (same as
    production; `StubEmbedder` fallback). **Same LLM** (hosted Qwen) and **same Laya**
    (System-1 routing) as production — only the STORES differ.
  - How: run the **real** pipeline with `--skip-stores --skip-reports`, which builds +
    saves `data/graph.json` via hosted-Qwen extraction + Laya merge judging but skips the
    Qdrant/Neo4j writes. Then `dev/build_fast_engine.py` loads `graph.json` into
    `MemoryVectorStore` + the in-memory graph and returns the SAME `QueryEngine`
    production uses. `dev/run_e2e.py` wires it to `medharness`.
  - Dev tooling lives in **`dev/`** (not product, not production indexer). Production
    indexing stays `graphrag/pipeline.py` → Qdrant + Neo4j. Product code
    `src/medharness/` does not import `dev/`.
- **Extraction gotcha (hosted Qwen, llama.cpp):** batching 4 chunks/prompt with
  `reasoning_effort` set returns completions with **no JSON** (`InvalidOutputError`).
  Works reliably with `--batch 1` + `GRAPHRAG_REASONING_EFFORT=none` (param not sent).
  The tunnel is also slow (~1 chunk/min under load), so extract the small
  `data/corpus_min/` for fast e2e, full `data/corpus_test/` in the background.
- **Track H `harness/` deprecated → `deprecated/harness/`** (2026-10-09, user-ordered
  cleanup). It was the generic coding-agent ReAct loop — a *mechanics donor* for the
  medical harness, never the product. Verified **zero code dependency** between
  `harness/` and `src/medharness/` before the move. `medharness` still imports clean;
  52 tests + arch gate unaffected. Reference copy only; `deprecated/README.md` explains
  why. Product remains Track A in `src/medharness/`.
- **Track A harness built S0–S8** (2026-10-09, user-ordered, simple stores for dev
  speed). Package `src/medharness/` (renamed from `src/harness` — collided with Track
  H's top-level `harness/` on the import path). Flow: `orchestrator.assess()` = L1 Laya
  decides → L2 `rules/engine.py` escalates (deterministic, sect 9 thresholds v1, 5
  branches, downgrade-forbidden, `llm_called:false`) → L3 Qwen explains (only if not
  escalated). **Vector = in-memory numpy cosine, graph = networkx** (no Qdrant/Neo4j;
  drop-in adapters keep the interface — the sect 7.4 swap is recorded here). Generation
  = hosted Kaggle Qwen via client-side tool calling (regex-parsed tool_call text, no
  `tools=` param — the server ignores it). 39 tests green; `scripts/check_architecture.py`
  proves `rules/` imports only `contracts` + only `engine.py` builds `Escalation`
  (verified fail-closed on a bad import). Escalation isolation proven dynamically: a
  raising-LLM stub is never touched on any escalated case (`tests/test_orchestrator.py`).
  Run offline: `python -m medharness.demo`; live: `MEDH_LIVE=1` / `MEDH_ENABLE_LLM=1`.
  **Not yet:** real MiniLM embeddings wired into `kb.py` (S1 used a hashed-token
  embedder), a live Laya/MEDH_LIVE run, evals + baseline.
- **Generative backend moved to hosted Kaggle Qwen + client-side tool calling** (2026-10-09, user-ordered): `qwen2.5-32b-instruct` (Q4_K_M GGUF, `bartowski/Qwen2.5-32B-Instruct-GGUF`) served by llama-cpp-python server on Kaggle T4 x2 behind a Cloudflare tunnel, OpenAI-compatible at `{tunnel}/v1`, dummy key `sk-local`, current URL `https://pond-breathing-foto-advocate.trycloudflare.com/v1` (URLs rotate on restart — override via `KAGGLE_LLM_BASE_URL`). Host script saved in `docs/hosting_qwen_kaggle.md`. **Tool-schema change:** the llama.cpp server has NO native tool support, so the OpenAI `tools=` parameter path is abandoned — schemas now live in the system prompt in Qwen's `<tools>` XML format, the model emits `<tool_call>{"name":…,"arguments":…}</tool_call>` as plain text, the client regex-parses it (`parse_tool_calls`), executes locally, and returns results in `<tool_response>` user turns. Proved live in `test/local_kaggle_llm.py` (real `<tool_call>` for `get_weather` captured, final grounded answer returned).
- **Track H P1 core done** (2026-10-09): `harness/agent/{prompt,llm,loop}.py`,
  `tools/{registry,files}.py`, `main.py`, 7 unittest tests green, live exam 1
  ("What is 2+2?" → `4`, 1 turn, 0 tool calls) via Zen `space-bunny-free`.
  Judgement calls: SYSTEM_PROMPT wording is ours (reference pins behaviour,
  not text); loop imports are absolute (`harness/` is top level);
  `delegate_task` left to P5 (unknown-tool DENY covers it). `pip install openai`
  3.26.1 into `~/venvs/graphrag` (user-approved).
- **Track H: coding-agent harness in `harness/`** (user-ordered, 2026-10-09):
  builds the `docs/harness_docs/` reference (my-agent ReAct agent) as a third
  independent deliverable. Recorded the same way as Track B (Plan.md §1b):
  `harness/` never imports Track A/B/`graphrag` and vice versa; not part of
  `make validate`. Location is top-level `harness/`, not `src/harness/`, per
  explicit user instruction. Dependency note: reference code needs `pip openai`
  (not installed anywhere yet — approval requested, Implement.md S2); session
  FTS5 needs an interpreter with FTS5 (system python / laya venv OK, graphrag
  venv NOT). Docs written first: `docs/architecture.md`, `docs/runbook.md`,
  `current_tasks.md` (updated). Build order P1→P2→P3→P5→P6→P7, Telegram last.
- **Pulled harness reference docs** (2026-10-09, user request): downloaded
  `my-agent/docs/` (9 md files, ~94 KB) into `docs/harness_docs/` via
  raw.githubusercontent. Reference only — never a dependency, same status as
  `docs/reference/`.
- **Clean rerun launched from zero** (2026-10-09): stores had both exited (255)
  after a daemon hiccup; `docker start` failed (runc desync) so containers were
  `rm`d and recreated via `docker compose --env-file .env up -d` (never
  `source .env` in shell — it spews laya-serve output and hangs). Wiped all 6
  JSON artifacts + `cache/extraction/*`, deleted 3 Qdrant collections, Neo4j
  cleared (0 nodes). Fresh `--batch 4 --workers 4` run in background on 16
  docs / 191 chunks.
- **Fresh rerun on 16-doc corpus** (2026-10-09): wiped `triples.jsonl`, `graph.json`,
  `communities.json`, `community_reports.json`, `merge_log.json`,
  `failed_chunks.json`, `cache/extraction/*`; deleted all 3 Qdrant collections;
  Neo4j already empty (old run never reached stage 8). Scraped 11 more Wikipedia
  medical extracts via `graphrag/wikipedia.py` (cardio-heavy for entity overlap);
  `Sepsis` + `Anemia` skipped twice on Wikipedia HTTP 429 — retry later to reach
  18. Corpus now 16 docs / 191 chunks / 48 extract batches. Relaunched
  `--batch 4 --workers 4` with the parallel-reports patch.
- **Parallel reports** (`graphrag/pipeline.py:160`, `graphrag/cost.py`): `generate_reports`
  ran 196 communities sequentially (~17 min stuck in `[7/8]` on 5 docs). Now
  level-by-level thread pool (`workers=args.workers`, default 4) — levels stay
  ordered so higher levels still read lower-level reports; order of returned
  reports unchanged. `CostLedger.record` gained a lock (same race already
  existed in threaded extraction). Verified: 152 unit tests green
  (`python -m unittest discover -s graphrag/tests`), stub smoke shows 2+
  threads and ~3x speedup on one level. Old sequential run left alone per user.
- **Reasoning fix** (`graphrag/llm.py`): `reasoning_effort="minimal"` by default.
  First full run failed 59/59 chunks with "empty completion" — the model spent
  the whole 2048-token budget reasoning (2047 reasoning tokens, 0 content).
  Measured: `minimal` cuts output 234→91 tokens, 4.1s→1.8s. `"none"` 400s;
  `enable_thinking:false` ignored. Env override `GRAPHRAG_REASONING_EFFORT`;
  on a 400 mentioning reasoning, drop the parameter and retry once.
- **Empty Qdrant upserts skipped** (`graphrag/pipeline.py`): zero-point upserts
  400 ("Empty update request"). An empty graph is still a failed run — the guard
  only stops a confusing secondary error.
- **Failed-run state reset** before re-run: `failed_chunks.json` deleted because
  the 59 failures recorded a model behaviour (reasoning saturation), not chunk
  problems. Retry is correct here; blind retry of genuinely bad chunks stays off.
- **Unit-test fix, not code fix**: the hash-stub embedder has no semantics, so
  the door-2 paraphrase test now asserts wiring (threshold + payload mapping).
  Semantics are verified live, not in unit tests.
- **Self-breakage fixed**: a whitespace-only edit broke `test_llm.py`
  indentation (suite failed to import). Caught by running the suite, fixed
  immediately. Lesson: never assume an edit is trivial — run the tests.
- **`docs/decision.md` merged into this file and removed** (2026-10-09, per
  repo instruction: the two logs were the same file in two places — keep one).
  This file is the single decision log: amendments and running entries
  together, newest first. `memory.md` stays the learnings file.
- **`.env` var names** (never values): `LIQUILD_AI_API`, `LAYA_*`,
  `OPENCODE_ZEN_API`, `OPENCODE_MODEl`, `NEO4J_PASSWORD`.
- **`.gitignore` still stock C++ template** — M0 must add `data/`,
  `__pycache__`, `.venv`, `out/`. Only `.env` is protected today.
- **Track B suite at 152 tests, green** (stdlib only, no network, no key).
- **Env spelling gotcha**: the model var is `OPENCODE_MODEl` (lowercase L).
  `graphrag/config.py` resolves `LLM_MODEL_ID` → `OPENCODE_MODEl` →
  `OPENCODE_MODEL`, in that order. Read the name, don't guess it.
- **Pipeline run stopped by user mid-run** (2026-10-09 ~09:00): extraction,
  merge and communities had completed (graph.json 266 KB, merge_log.json
  146 KB); reports/stores stages did not run. Partial artifacts remain in
  `data/` and the next run resumes from the checkpoint — nothing is lost.

---

## 2026-10-09 — Track B dependency approval: stores, embeddings, community detection

**Change:** explicit approval to add the dependencies Track B's stores and
indexing stages require. Requested by the user as "code the rest of graphrag
full … including all query phase" — recorded here per `Implement.md` §3.1 S2
rather than added silently.

| Package | Used by | Why it cannot be stdlib |
|---|---|---|
| `qdrant-client` | `graphrag/stores.py` | vector index with payload filtering; the retrieval store |
| `neo4j` (driver) | `graphrag/stores.py` | property-graph persistence with provenance edges |
| `sentence-transformers` (+ CPU `torch`) | `graphrag/embeddings.py` | `all-MiniLM-L6-v2`, the pinned embedding model (Plan D18) |
| `igraph` + `leidenalg` | `graphrag/communities.py` | hierarchical Leiden; stdlib Louvain ships as fallback |

**Constraints that survive:** unit tests stay dependency-free (fakes + stubs, no
imports of any package above at test time — verified by running the suite with
only stdlib). The packages are imported lazily inside the store/embedding
functions so a missing install fails with a named error, not an ImportError at
`import graphrag`. `make validate` never touches them (D19).

---

## 2026-10-09 — §19.3 answered via OpenCode Zen (`space-bunny-free`)

**Change:** the generative-LLM backend is resolved. Key `OPENCODE_ZEN_API`, model
`space-bunny-free` (both already in `.env`), base `https://opencode.ai/zen/v1`.
`graphrag/config.py` resolves `LLM_*` first and falls back to the `OPENCODE_*`
vars; `graphrag/llm.py` is the extraction adapter, `graphrag/cost.py` the
per-stage ledger.

**Cause:** extraction needs a generative endpoint and the only key in `.env`
that can serve it is the Zen one (`d1:free` returns `output_tokens: 0`).

**Measured on 2026-10-09 (not assumed):**

| Finding | Value |
|---|---|
| endpoint | `POST /v1/chat/completions`, standard OpenAI envelope |
| auth | Bearer; **requires an explicit `User-Agent`** — default `Python-urllib` gets Cloudflare `403 / error 1010` |
| catalogue | `GET /v1/models` → 43 models, ours present |
| first live batch | 2 chunks → 2 valid results, fed into the graph (5 nodes, 3 edges) |
| latency | **12.4 s for 2 short chunks** (~100 words) — a reasoning model |
| reasoning tax | **1011 of 1193 completion tokens (85%) were `reasoning_tokens`**; 182 tokens of actual JSON |
| truncation | `max_tokens: 500` truncated mid-object on the probe; default budget is now 2048 with one doubled-budget salvage retry |
| entitlement | `nemotron-3.5-lightning-free` → 403, `glm-5.3-flash` → 402 on this key. Only the configured model is confirmed working |

**Consequences:**

1. **The 2-hour math gets worse, not better.** At ~12 s per 2-chunk batch with
   85% reasoning overhead, sustained throughput is roughly **10 chunks/min** —
   before concurrency. Concurrency 20 would still need the quota to allow it,
   which is unmeasured. The encoder rejection (logged 2026-10-09) now carries
   this cost explicitly: if the multi-GB run proves impractical, **revisit the
   encoder first**.
2. **The model invents negated relations.** The live run returned
   `NOT_ADMINISTERED_FOR` — a novel relation type the prompt did not list.
   Downstream, relation labels must be adjudicated (the Laya `choice` over a
   controlled vocabulary already planned), not trusted.
3. **Batch discipline is load-bearing.** The adapter rejects a batch whose
   result count mismatches its input count and retries once — per the rule that
   sloppy alignment is poison for an index where unresolvable citations are a
   hard failure.
4. **429/5xx retry with `Retry-After`; 400/401/403/404/422 never retried.**
   A rejected request re-sent is a re-billed request.

**Still open:** sustained rate limit (calls/min before 429), and output-token
billing for reasoning tokens. Both are measured on the 10-document run, not
before.

---

## 2026-10-09 — Track B added: the GraphRAG indexer as a second deliverable

**Change:** `Plan.md` gains **§1b**, defining a second deliverable alongside the harness. New
top-level files: `current_tasks.md` (live status) and `graphrag_plan.md` (Track B's plan). New
package: `graphrag/`.

**Cause:** the harness cannot be evaluated without a retrieval index, and the index needs its own
implementation to the design record in `docs/rag_docs/` — a lineage that is more developed than
`docs/rag_implementationplan.md` and covers reranking, routing, entity merging and failure modes in
far more detail. Building it inside `src/harness/` would have violated §4.1's layout and mixed a
build-time tool into the runtime dependency graph.

**Why a separate track rather than absorbing `rag_docs/`:**
- The two design lineages differ in substance, not just wording. `rag_docs/` specifies ingestion
  I1–I11 and query Q0–QG; `rag_implementationplan.md` specifies GraphRAG-on-Qdrant/Neo4j. Both
  are kept; `graphrag_plan.md` states which choice is taken at each divergence point.
- Track B is **build-time and offline**. It never runs inside a test, a request path, or
  `make validate` (D19). The dependency direction is one-way and recorded: Track B writes the
  index, Track A's `kb_search` reads it. Neither imports the other.

**Decisions taken, with the doc that settles each:**

| Decision | Choice | Source |
|---|---|---|
| Extraction | **LLM per chunk.** The encoder writer→labeler swap is **rejected** | user decision, 2026-10-09 |
| Reranking, routing, seed finding | **follow `docs/rag_docs/`** | user decision |
| System-1 model | the **local Laya** at `127.0.0.1:8000` | extends `Plan.md` D2/D17 |
| Ollama | **not used** — its `laya` is an MLX/Apple build, unusable on Linux | measured: pull fails |
| Heavy indexing | **GitHub Actions**, checkpointed to HuggingFace every 1–1.5 h | user decision |
| Stores | Qdrant + Neo4j in Docker | `Plan.md` D6/D7 |

**What is NOT changed:** no milestone, threshold, metric, or acceptance criterion in §2 or in
`PROMPT.md` §15/§16. Track A's ordering, its §19 gates, and the safety core (M4–M5) are untouched.
No secret is committed; LLM and HuggingFace credentials arrive via `.env` locally and repository
secrets in CI.

**Cost recorded honestly.** LLM extraction's floor is output-token throughput, not call count:
~6–12 h for 4 GB, ~1.6–3.2 h for 1 GB. The encoder path in `NER_model.md` would have removed that
floor entirely (4 GB in 2–6 h of local CPU, zero token cost) and is the reason the rejection is
logged rather than silently taken — **if the 4 GB run turns out to be impractical, this decision is
the first one to revisit, and revisiting it is an amendment, not a fix.**

**Open question flagged, not silently resolved:** artifacts are planned for HuggingFace, but a
**Space** is an app container and a **dataset repo** is the natural home for a graph export. The
choice must be made before the first upload.

**Validation:** none run, and none possible — `make validate` does not exist until M0. Nothing in
this change is executable. The Track B unit suite (`graphrag/tests/`) is its own gate and runs
offline with no API key.

---

## 2026-10-09 — Human-readable goals added to `Plan.md` and `Implement.md`

**Change:** two non-normative orientation sections. `Plan.md` gains **"Goals in plain language (for
humans)"** before §0; `Implement.md` gains **"§0 What we are building, in plain language"**. No existing
section was edited, removed, renumbered, or reordered.

**Cause (the finding):** the specification is written for implementers. A reviewer, supervisor, or new
team member cannot answer "what is this actually for, and why is it built in this order?" without reading
a 565-line specification that opens with performance budgets. That is a real audience gap, and it was
raised explicitly — the earlier explanation of these goals was clear to coding agents and useless to
humans. Orientation content was missing, not wrong.

**Why it went in these two files:** `README.md` does not exist yet (it is an M0 deliverable), so there was
nowhere human-facing in the repository. Both files were marked 🔒 in `AGENTS.md`, so this went through the
`Plan.md` §6 amendment procedure rather than a quiet edit.

**What it contains:** the product in one sentence; why indexing is built first (the perishable model
window); the seven stages in plain language with what each one produces; the three-layer data flow; the
non-goals; the scorecard in plain terms; what is already known to be hard; and the honest bottom line.

**What it deliberately does NOT do:**

- No normative content. Both sections state that `PROMPT.md` is the specification and `Plan.md` §2 is the
  binding milestone list, and that §2 wins on any conflict.
- No new requirement, threshold, metric, milestone, or acceptance criterion.
- No claim of clinical validation, no accuracy figure, and no statement that the system diagnoses
  anything. The §4 non-goals and the §18 limitations are reproduced in plain language rather than softened.
- No renumbering, so existing cross-references (§4.2, §5, §6, §7) remain valid.

**Validation:** none run, and none is possible — `make validate` does not exist until M0 builds the
Makefile. Nothing in this change is executable, and no code, test, threshold, or data file was touched, so
there is nothing for a gate to break. Recorded here rather than claimed as a passing check.

**Supersedes for humans:** `README.md` (M0) becomes the canonical public entry point and will carry the same
plain-language framing, without overstating anything.

---

## 2026-10-09 — System 1 (Laya) hosted locally via `laya-serve`; the hosted API is rejected on evidence

**Change:** §19.1 is answered in practice. Laya runs as a **local HTTP service** on
`http://127.0.0.1:8000`, served by the official `laya-serve` (`pip install "laya[serve]"`), exposing
`POST /v1/systemone`. It is no longer called as a third-party API.

**Cause:** the hosted System-1 endpoint was measured and rejected; the user then directed local hosting
so the checkpoint is downloaded once and loaded once.

**Measured evidence (not assumption):**

| | hosted `d1:free` | local `laya` |
|---|---|---|
| latency | 384–526 ms (median 432) | 2183–2627 ms (median 2301) |
| identical calls byte-identical | **no** (0.99478 vs 0.99509) | **yes**, 6/6 runs |
| rate limiting | HTTP 429 on the 3rd rapid call | none observed |
| §7.1 budget ≤150 ms | exceeded | exceeded, ~15× |

Determinism is the decisive column: `PROMPT.md` §7.2.1 and §16.3 require byte-identical repeat runs,
which the remote endpoint does not provide and the local one does.

**Dead end — do not retry.** Ollama's `laya` package is an **MLX build and Apple-Silicon only**:
`ollama pull laya` fails with *"this model requires MLX support"*. Also `ollama.com/download/ollama-linux-amd64.tgz`
is a 404; the archive is now `.tar.zst` (1.44 GB, needs `tar --zstd`). Artifacts removed, 3.5 GB reclaimed.

**New dependencies — explicitly approved by the user on 2026-10-09:** `torch 2.14.1+cpu` (CPU-only
index; this host has no GPU, per §7.4 "no GPU required") and `laya[serve]`. Both live in
`~/venvs/laya-server`, outside the repository and outside the harness test process, preserving D2's
rationale (the 421M checkpoint never enters a test process).

**What is NOT changed.** §7.4 "Laya runs on a server and is accessed over HTTP" still holds — we run that
server ourselves. Tests still read recorded fixtures only (D3). `make validate` never touches this
endpoint. Live checks stay a separate manual target (§4.4).

**Server configuration and why:** `LAYA_MODELS=english LAYA_MAX_LOADED=1` keeps exactly one 421M
checkpoint resident (2.0 GB RSS). The default `MAX_LOADED=2` holds english + multilingual ≈ 3 GB and
would OOM on a 7.8 GB host with no swap. `LAYA_HOST=127.0.0.1` overrides the 0.0.0.0 default.

**Measurements to carry forward (findings, not defects):**

1. **Latency: median 2301 ms against a ≤150 ms budget.** Reported plainly at M12. §7.1 is **not**
   relaxed to make this green. Contributing factor is 2 CPU cores with no GPU.
2. **`choice` questions with ≥11 options return uncalibrated confidence** — the `choice:11+` temperature
   in `rl_agent_config.json` is 0.1006, outside the valid [0.5, 5] range, so the runtime clamps it to
   0.5 and warns. `PROMPT.md` §8.3 has exactly 10 body systems, so it lands in the safe `choice:6-10`
   bucket. Adding an 11th option silently changes calibration.
3. **`noul` can follow its option labels instead of the state** (upstream issue #156): the English
   checkpoint returns a confident *wrong "no"* on positive input. All eight red flags in §8.2 are
   `noul`, and §15.2 requires 100% red-flag recall as a release blocker. This must be measured early,
   not discovered at M11. Documented workaround: ask the same question as a two-option `choice` with
   neutral keys.
4. **`act.act_probability` carries no usable signal** (upstream issue #185, reads ~1.0, AUROC 0.30).
   Gate on `confidence` instead.
5. **512-token context, ~320 tokens for state** (English checkpoint). Longer states truncate; the
   response reports `truncated` and `truncated_questions`. Relevant later to `summarize_report`.

**Open question raised for M4 — deliberately not decided here.** §9 escalates when "acuity expected
level ≥ urgent (ordinal index ≥ 2)". Laya returns a *continuous* expected score — 1.79 with
P(urgent) = 0.79 on the test presentation — so floor, round, and argmax each give a different answer
about whether that branch fires. Settled at M4 with a test on both sides of the boundary, not guessed now.

---

## 2026-10-08 — Milestone reorder: index before the safety core

**Change:** `Plan.md` §2 milestone ordering. Original `M0 → M1 → M2 → M3 → M4 → M5 → M6` becomes `M0 → M1 → M6a–M6c → M4 → M5 → M6d → M7 …`.

**Cause:**

Model access is time-limited — **2–3 days**. The plan assumed model access was stable; it is not. `Plan.md` §6 requires recording any milestone that proves an assumption wrong. This is that case, and it is not a rule break: it is the amendment procedure working as designed.

The original ordering was correct on its own terms. It put the safety core first because the rule engine is pure, dependency-free, and provable. That reasoning still holds and nothing about it is retracted.

**Why it changes:**

| What | Consumes models | Perishable |
|---|---|---|
| Chunking, embedding, Leiden, Docker | no | no |
| **Entity/relationship extraction** | **yes** | **yes** |
| **Description summarisation** | **yes** | **yes** |
| **Community reports (map-reduce)** | **yes** | **yes** |

Graph extraction is ~75% of indexing cost (`docs/rag_implementationplan.md` §2). RAG *code* is permanent — Docker, Qdrant, Neo4j, MiniLM and Leiden all outlive the models. What expires is tokens. So the response is not "abandon safety ordering," it is:

> Build enough pipeline to index, index now, cache by content hash — converting a perishable resource into a permanent artifact.

The RAG plan already required this cache control, originally framed as a cost optimisation. It is now the thing standing between a 3-day window and a total loss.

**What is not changed:**

- The safety core still ships **before anything is wired together.** The escalation-isolation claim is proven before the harness exists.
- No metric, threshold, or acceptance criterion is relaxed.
- Indexing still never runs inside a test or request path (`Plan.md` D19).
- `PROMPT.md` §7.2 determinism is untouched.

**Risk introduced:**

Reordering increases the chance of building a large untested retrieval pipeline before any safety code exists. Mitigation: M6a–M6c are gated independently and must pass `make arch` at every step; M4 and M5 are not deferred in scope, only in position.

**Preferential ordering inside indexing if time runs out:**

1. **Extract entities and relationships first** — highest value, highest cost, and it is the foundation everything else derives from.
2. Description summarisation second.
3. Community reports last — derived from the graph, so a partial index still yields a usable graph.

If the window closes with extraction complete but reports missing, that is a recoverable state: the graph persists in Neo4j and reports regenerate later against any provider.

---

## 2026-10-08 — §19.3 answered: stealth models are the LLM backend

**Change:** `PROMPT.md` §19.3 moves from open to answered.

**Cause:** The models available for 2–3 days are the project's OpenAI-compatible endpoint. They are not a separate tool to be used alongside OpenRouter or OpenCode Zen — they *are* the backend.

**Still required before M7:** `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID` as environment values, plus the list of which model ids will be retired so a fallback is chosen before they are. Endpoint and credentials arrive by environment configuration and are never committed.

**Consequence:** backend choice drove the entire indexing cost model. That is now fixed, but temporarily. Since these models are retired, **every index run must record the model id used** (`PROMPT.md` §12), so a later re-index against a different provider is comparable rather than silently different.

---

## 2026-10-08 — §19.4 corpus: to be chosen now

**Status:** open, but no longer blocked on deliberation.

**Cause:** With a 2–3 day window there is no time for extended clinical review. The corpus must be chosen today so indexing can start.

**Consequence to record:** if documents are selected quickly and without a clinician reviewing them, `docs/limitations.md` must state exactly that — how many documents, where each came from, who approved them, and that no clinical review occurred. This is not a detail; an unreviewed corpus now produces a knowledge graph, not merely poor retrieval.

**Rule for now:** prefer a **small, homogeneous, clearly-licensed** corpus (one domain, e.g. emergency care) over a broad unreviewed one. Fewer documents indexed well beats many indexed carelessly, and the index is permanent while the review budget is not.

---

## 2026-10-08 — hermes-agent adopted as reference only

**Change:** `docs/reference/hermes_agent.md` added.

**Cause:** `NousResearch/hermes-agent` surfaced as a candidate ruleset. Reviewed and scoped: **reference only, no dependency, no code.**

**Why not a ruleset:** its rules fit a general-purpose personal assistant. Several conflict directly with hard constraints here — it self-improves skills during use (breaks `PROMPT.md` §7.2 determinism), auto-persists narration into memory (excluded by our `memory.md` rules), spawns subagents and writes executable scripts (against §7.2.4 isolation), and carries identity primitives — DM pairing, allowlists, platform gateways (against the §4.4 non-goal).

**What is taken:** the `AGENTS.md` and memory-file conventions, toolset gating as a model for the permission registry, command approval and container isolation as security design references, OpenRouter as an `§19.3` backend option, and the MCP integration pattern — which is recorded as an open question, not adopted.
