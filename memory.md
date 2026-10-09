# memory.md — Agent Memory

Durable learnings for this repo: things a future agent would need again and could not re-derive cheaply.

## Rules

- Read it at the start of every session.
- Append entries as you learn them, in any format you find useful — headings, bullets, or prose. The structure is yours to choose; keep it skimmable so a future agent gets the substance in under a minute.
- An entry is worth writing when it captures any of these:
  - A mistake you made here — what it was, what actually fixed it
  - An environment quirk, version pin, or setup gotcha that cost you time
  - Non-obvious behaviour of Laya, the LLM provider, or a data file
  - A dead end — what you tried, why it failed, so nobody repeats it
  - A correction the user gave you
  - Something you had to dig for that isn't written down anywhere else
- Skip session narration, task progress, and anything git already records. Progress belongs in `Plan.md` §2; rationale in `PROMPT.md` and `Plan.md` §7; commands in `docs/runbook.md`; deferred work in `current_tasks.md`.
- **Never put secrets, keys, or patient data in this file.**

### 2026-10-09 — Live Laya answered differently than the fixtures did (2 adapter bugs)

- **Live laya-serve returns NUMERIC answers; fixtures return STRING/`probabilities`.**
  Verified against the local `laya-serve` (`english`, rev `7b928d82`):
  - `noul` → `{"type":"noul","noul":0.9306,"confidence":0.9306}` — **no
    `probabilities` dict**. The old parser read only
    `probabilities["true"]`/string `"true"`, so **every live red flag parsed
    0.0 → red-flag recall 0% live**. Live polarity confirmed: true text →
    0.9306, false text → 0.1113 (so numeric `noul` tracks P(true) directly).
  - `score` → `probabilities` keyed **numerically** `{"0":0.3645,…}` with a
    separate `legend: {"0":"routine",…}`. Unmapped, the distribution came out
    uniform 0.25 → `P(resuscitation)=0.25` always breached the 0.10 threshold →
    **every live query escalated, including "I have a cold"**.
  - `noul` renders option labels `false:`/`true:` and can answer "no" to a
    positive state (upstream #156) — measure this against the flags, it may be
    the label bug or genuine model misreading.
- **Then the real trap — argmax.** Even with the legend parsed, `parse_verdict`
  took argmax of the distribution as the expected acuity. Laya genuinely scores
  a cold `{routine .12, soon .37, urgent .50, resus .009}` → argmax `urgent` →
  escalate. **PROMPT.md §8.1 line 189 already answers this: escalate on the
  distribution, not the point estimate alone.** Expected level now derives from
  the distribution when top mass < 0.5 (cold → E=1.40 `soon`; chest pain top
  .75 → `urgent`; unresponsive .91 → `resuscitation`). No threshold changed.
- **Never trust an adapter that has only ever run against fixtures.** Everything
  looked green (75 tests) while the live path was structurally blind. A live
  probe is part of proving a parser, not an optional extra.
- **Measured live latency:** single question warm ~0.5 s, but the **full
  12-question set ~12.4 s** vs the §7.1 ≤150 ms budget (~80× over; 1.7 s/question
  residual from the earlier measure, plus this box's CPU). Acuity `confidence`
  on simple text is ~0.20–0.27 → `abstained=True` is normal; abstain must gate on
  the *salient* keys only (already fixed earlier via the memory.md note).
- **On Laya onset order for this box:** `pip install "laya[serve]"`
  (laya-0.4.1) into system python, then
  `setsid nohup env LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_DEVICE=cpu LAYA_MODELS=english LAYA_PRELOAD=1 LAYA_MAX_LOADED=1 LAYA_THREADS=4 USE_TF=0 laya-serve > /tmp/laya.log 2>&1 < /dev/null & disown`,
  `/health` reports `"status":"ok","loaded":["english"],"revisions":{"english":"7b928d82…"}` in ~40 s. Start it **detached** — a restart inside the same command call gets SIGTERM'd.
- **`pkill -f "run_e2e"` / `pkill -f "laya-serve"` kills YOUR OWN shell** — the pattern matches the wrapping `bash -c` process. Use `pgrep -af "[r]un_e2e"` (leading bracket) to look, then kill by PID.
- **MiniLM reloads its weights every fresh process** (~4 s from local cache, not a download) and prints a `Loading weights` bar. `HF_HUB_DISABLE_PROGRESS_BARS=1` silences it (set in `dev/run_e2e.py`).

### 2026-10-09 — Dev retrieval now runs on FAISS with a checkpoint-style runner

- **`dev/faiss_store.py`** — `FaissVectorStore`, same method names as
  `graphrag.stores.MemoryVectorStore` (`ensure_collections/upsert/search/count`)
  plus `save/load`. `faiss.IndexFlatIP` over L2-normalised float32 vectors
  (= cosine); payloads in a dict sidecar; `payload_filter` falls back to
  brute-force. **`faiss` is a dev-only dependency — requirement needs approval.**
- **`dev/run_e2e.py` is now a two-command flow.**
  - `--index --corpus-dir <dir>` → chunk → hosted-Qwen extraction
    (`--batch 1`, batching drops JSON) → graphrag `Graph` **plus a networkx
    `GraphStore`** for the harness → communities → reports → MiniLM → FAISS,
    checkpointed under `dev/data/` (`chunks.json`, `graph.json`,
    `networkx.json`, `communities.json`, `community_reports.json`, `faiss/`,
    `triples.jsonl`, `meta.json`). Resumable; `--skip-reports`; `--limit N`.
  - `--ask "question"` → loads the checkpoint and runs the **full**
    `medharness` system (`Orchestrator.assess`, Laya-or-fixtures, Qwen,
    `GraphRAGExternalSearch`) with `community_reports` wired in so global search
    works. **No harness logic lives in dev/ — only wiring.**
- **Measured:** fresh 1-doc/1-chunk index = **51 s wall** (~40 s the single
  extraction call). 15 chunks ≈ 11–12 min + reports. `data/corpus_test/aspirin.txt`
  is 55 KB → 15 chunks; slice small corpora with
  `text[:int(len*0.35)].rsplit("\n\n",1)[0]` (5 chunks) or `[:3500]` (1 chunk).
- **Extraction prompt quirk on this server:** it's a llama.cpp build with no
  native tool support, so the harness never sends OpenAI `tools=` (the Qwen
  client turns `tools` XML in the system prompt into `<tool_call>` text instead);
  and on the graphrag side batched extraction drops JSON — use `--batch 1`.
- **`dev/build_fast_engine.py` is now legacy** (still loads `data/graph.json`
  into `MemoryVectorStore` + in-memory graph); new runs use the checkpoint flow.
- **The tunnel URL rotates** — it is `https://pond-breathing-foto-advocate.trycloudflare.com/v1` as of this session; override with `KAGGLE_LLM_BASE_URL`. TLS kill by Cloudflare earlier means any restart flushes the URL.

### 2026-10-09 — Local CPU LLM: Ollama + qwen2.5:1.5b (harness now runs offline)

- **Model:** `qwen2.5:1.5b` Q4_K_M, 986 MB, ctx 32k, `capabilities: [completion, tools]`.
  Verified: plain chat `LOCAL_OK` (~14s), real `read_file` tool_call (~10s),
  full `harness/agent/loop.py run()` end-to-end `HARNESS-LOCAL-OK`.
- **Harness env:** `BASE_URL=http://localhost:11434/v1 API_KEY=ollama MODEL=qwen2.5:1.5b`
  (`agent/llm.py` speaks OpenAI chat-completions; Ollama's `/v1` is compatible).
- **Gotcha 1:** first `install.sh | sh` timed out mid-download — `ollama`
  binary existed but `/usr/local/lib/ollama/` had only licenses (no
  `llama-server`), so every inference 500'd with "llama-server binary not
  found". Fix: re-ran installer to completion, `pkill -f "ollama serve"`,
  restarted detached (`setsid nohup ollama serve ... & disown`) — the restart
  inside the same `run_commands` call gets SIGTERM'd with the tool call.
- **Gotcha 2:** weights live in `~/.ollama/models/blobs/` (941 MB) and survive
  reinstalls — never re-pull, only the runner bundle was missing.
- **RAM headroom:** box is 7.8 GB total, ~5 GB used by Neo4j/Qdrant/laya-serve;
  only ~2.5 GB free. 1.5B fits; do NOT pull 3B+ without stopping something.
  Expect ~10-15s/turn on 2 CPU cores — fine for dev, not for batch indexing.

### 2026-10-09 — Dev/test fast stores + the extraction gotcha (hosted Qwen)

- **Dev/test = fast local stores; production = Qdrant + Neo4j.** To test end-to-end
  without Docker, run the REAL pipeline with `--skip-stores --skip-reports` (hosted-Qwen
  extraction + Laya merge → saves `data/graph.json`, skips Qdrant/Neo4j), then
  `dev/build_fast_engine.py` loads that graph into `MemoryVectorStore` (fast vector) +
  the in-memory `graphrag.graph.Graph` (fast graph) → the SAME `QueryEngine`. Same LLM,
  same Laya, only the STORES differ. Dev tooling lives in `dev/`; `src/medharness/` never
  imports it. Recorded in decisions.md + runbook.md.
- **Extraction gotcha (hosted Qwen via llama.cpp):** the pipeline's default `--batch 4`
  + `reasoning_effort` returns completions with **no JSON** (`InvalidOutputError: no JSON
  object in completion`) — all chunks fail. **Fix: `--batch 1` + `GRAPHRAG_REASONING_EFFORT=none`**
  (the param is not sent; llama.cpp emits clean `{"results":[...]}` JSON). Verified a
  single small call returns valid triples JSON in ~10 s.
- **The tunnel is SLOW under extraction load:** ~1 chunk/min (each 2500–4800-char chunk
  through the 32B model over Cloudflare). Don't extract the full 52-chunk corpus live
  expecting speed — use `data/corpus_min/` (one doc) for a fast e2e, full corpus in the
  background. `pkill -f graphrag.pipeline` SIGTERMs your own shell too — check pgrep first.

### 2026-10-09 — Track A harness S0–S8 built (simple stores, dev speed)

- **Layout:** `src/medharness/` — `contracts/` (pydantic, only thing rules imports),
  `decision/` (Laya http + fixture adapters), `rules/` (thresholds + engine),
  `tools/` (lookup + kb + registry role matrix), `generation/` (qwen client-side
  tool loop + policy), `orchestrator.py`, `service/app.py`, `demo.py`. 39 tests in
  `tests/`, no network. `python -m medharness.demo` runs offline on 6 synthetic cases.
- **Delimiter gotcha (bit me 3x):** writing the literal ``, `</tool_response>` tags
  into source/tests is fragile — the toolchain consumes them mid-write. Build them
  from parts: `_TC_O = "<" + "tool_call>"`; tests import `_TC_O/_TC_C` and wrap bodies
  with a helper. Never paste the full tag as a literal.
- **Path gotcha (bit me 2x):** `data/` is repo-root, so from `src/medharness/<pkg>/*.py`
  it is `parents[3]`, not `parents[2]`. Same off-by-one hit client.py and lookup.py.
- **Escalation is the safety spine:** `orchestrator.assess()` runs Laya→rules and
  returns on escalation WITHOUT constructing the Qwen client. `test_orchestrator` swaps
  a `RaisingLLM` that throws if touched; every escalated case asserts `llm_called=false`
  and the stub untouched. Never refactor assess() to build the LLM before the rule check.
- **Next:** swap the hashed-token embedder in `tools/kb.py` for real MiniLM
  (`embed(texts)->vectors` seam), a live `MEDH_LIVE=1` run, then M11 evals + baseline.

### 2026-10-09 — search_external_docs: two-tier retrieval (internal KB → full graph walk)

- **Mechanism (S9):** the LLM tries `kb_search` (internal simple stores) first; when
  that returns too little it calls `search_external_docs(query, mode)`, which runs a
  FULL graph walk and returns docs as `text_unit:` facts; the LLM then answers grounded
  in them. System prompt teaches the fallback; the tool is permission-gated like the rest.
- **Adapters (`tools/external_search.py`):** `GraphWalkExternalSearch` (offline — real
  2-hop networkx walk via `GraphStore.walk_edges`, seeds = name-match ∪ vector, facts =
  edge provenance `head --rel--> tail | chunk text`). `GraphRAGExternalSearch` (live —
  wraps `graphrag.query.QueryEngine.ask()` for real Qdrant+Neo4j local AND community).
- **Citation rule holds:** only `text_unit:` ids may cite facts; `community:` summaries
  are filtered out (D20). Global/community mode degrades to a named refusal offline
  (needs the live index) — never a fake answer.
- **Wiring:** `Orchestrator` holds `llm_client` + `external_search`; `_bound_dispatch`
  injects external_search into the registry chokepoint. `service/app.build_external_search()`
  picks live-vs-offline by `MEDH_LIVE`.

## Notes

### 2026-10-09 — Hosted System-1 API probe (Liquid `d1:free`) — measured, then rejected

Probe script: `test/probe_system1_api.py` (manual, live, never part of pytest). Key lives in `.env` as
`LIQUILD_AI_API` (note the spelling — it is **not** `LIQUID_`). Never print or read the value.

What the probe actually measured (2026-10-09):

- **Not OpenAI-chat compatible.** `POST /v1/chat/completions` → **404**. The real wire format is System One:
  `POST https://api.liquid.ai/decisions/v1/systemone` (also works at `/v1/systemone`), Bearer auth, body
  `{model, state, questions}`, answer = `noul` 0–1 / `choice` + `probabilities` + `confidence` /
  `score` + `legend` + `probabilities`. `output_tokens` is always 0 (decision model, no generation).
- **Latency 384–526 ms** (median 432 ms) against a §7.1 budget of **≤150 ms** — exceeded in every sample.
- **HTTP 429 on the 3rd rapid sequential call.** Free tier is rate limited; batching/backoff is mandatory,
  and a 429 is a plausible real-world failure the harness must degrade through.
- **Not byte-identical across identical calls.** Same payload twice: `noul` 0.99478 vs 0.99509,
  choice `cardiac` 0.9819 vs 0.9598. §7.2.1 demands byte-identical output for a pinned checkpoint, so a
  *remote* System-1 model cannot satisfy it. This is the strongest argument for local hosting — and for
  fixtures (D3) as the only thing tests are allowed to read.

### 2026-10-09 — Laya identity and local hosting (replaces the hosted API)

- **Laya** = open-weight decision model, **421M params, ModernBERT-large + decision head**, Convai
  Innovations, Apache-2.0. HF `convaiinnovations/laya`: root = English 421M / 512-token context,
  `multilingual` = 322M / 1024, `typed-decisions` = 421M / 1024. Question types `choice` / `score` /
  `noul`; one forward pass answers the whole question set — this is what §7.1 "never call once per flag"
  and §8.2 "one question set" rely on.
- **Dead end: Ollama's `laya` model is Apple-only.** `ollama pull laya` fails with *"this model requires
  MLX support, but the MLX runtime is not available"* — the Ollama package is an MLX build, and MLX is
  macOS/Apple-Silicon. Dead on Linux x86_64. Also note `ollama.com/download/ollama-linux-amd64.tgz` is
  a **404** — the archive is now `ollama-linux-amd64.tar.zst` (1.44 GB, needs `tar --zstd`). Don't retry
  this route. Freed the 3.5 GB afterwards.
- **What works: `pip install "laya[serve]"` → `laya-serve`**, an official Jev-compatible HTTP server on
  `POST /v1/systemone` with `{state, questions}` in and `{model, answers, usage}` out — *the same wire
  format the hosted Liquid API uses*, so the probe script works against both. Venv at
  `~/venvs/laya-server` (python 3.14 + `torch 2.14.1+cpu`; CPU-only torch wheels **do** exist for
  cp314). Start command (also in `.env` as `LAYA_SERVER_COMMAND`):
  `LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_DEVICE=cpu LAYA_MODELS=english LAYA_PRELOAD=1 LAYA_MAX_LOADED=1 LAYA_THREADS=2 USE_TF=0 ~/venvs/laya-server/bin/laya-serve`
- **Env vars worth knowing:** `LAYA_MODELS` (comma list to preload), `LAYA_MAX_LOADED` (default 2),
  `LAYA_DEFAULT_MODEL` (default english), `LAYA_PRELOAD`, `LAYA_DEVICE`, `LAYA_HOST` (**default
  0.0.0.0** — override with `LAYA_HOST=127.0.0.1` on a dev box), `LAYA_PORT`, `LAYA_API_KEY`,
  `LAYA_THREADS`, `LAYA_IDLE_UNLOAD_SECONDS`, `LAYA_MAX_CONCURRENT`, `LAYA_JEV_STRICT`.
- **Measured on this box** (2 cores, no GPU, no swap, 2.0 GB RSS resident):
  **latency 2183–2627 ms, median 2301 ms** vs the §7.1 budget of **≤150 ms — exceeded ~15×**. With
  `LAYA_MODELS=english` + `LAYA_MAX_LOADED=1` only one 421M checkpoint is resident (default `MAX_LOADED=2`
  would hold english+multilingual ≈ 3 GB and OOM on a 7.8 GB / no-swap host).
  **Determinism: 6/6 runs byte-identical** (§16.3 satisfied for the live local path — the hosted API was *not*).
  Answer shape verified: `score` → `{score, legend, probabilities, confidence}`, `choice` →
  `{choice, probabilities, confidence}`, `noul` → `{noul}`, plus `usage.input_tokens`, `output_tokens: 0`,
  `state_tokens`, `truncated`, `truncated_questions`. `/health` reports the checkpoint **revision SHA**
  (`7b928d82…`) — that is the identifier §12 wants in every trace.

### 2026-10-09 — OpenCode Zen is the generative backend (Track B extraction)

- Base `https://opencode.ai/zen/v1`, extraction at `POST /v1/chat/completions`,
  model `space-bunny-free`. Catalogue at `GET /v1/models` (43 entries).
- **Always send an explicit `User-Agent`.** Default `Python-urllib` UA gets
  Cloudflare `403 error 1010` — looks like auth failure, is not. `graphrag/llm.py`
  hardcodes the UA so this cannot regress.
- The model is a **reasoning model**: measured 85% of completion tokens as
  `reasoning_tokens` (1011/1193 on the first live batch). Budget `max_tokens`
  for reasoning + JSON or responses truncate mid-object (seen at 500).
- **12.4 s for a 2-chunk batch** on short text. Throughput math for the big run
  must use this, not a generic "2.5 s/call" guess.
- Env spelling: the model var is `OPENCODE_MODEl` (lowercase L). Both spellings
  resolve, `LLM_*` wins. `require_llm()` lists exactly what is missing.
- Not entitled on this key: `nemotron-3.5-lightning-free` (403),
  `glm-5.3-flash` (402). Do not assume sibling models work — probe first.

1. **`noul` can follow its option labels instead of the state** (upstream issue #156): it renders its two
   options as `false:`/`true:`, and the English checkpoint will return a confident **wrong "no"** for
   positive input. All eight of our red flags are `noul` (§8.2) and red-flag recall must be **100%**
   (§15.2 release blocker) — so this must be measured early, not at M11. Documented workaround if it
   bites: ask the same question as a two-option `choice` with neutral keys.
2. **`choice:11+` temperature is invalid** in `rl_agent_config.json` (0.1006, outside [0.5, 5]); the runtime
   clamps it to 0.5 and warns *"Treat confidence from the affected entries as uncalibrated."* So **any
   `choice` question with ≥11 options returns uncalibrated confidence.** §8.3 extraction has exactly 10
   criteria → safe `choice:6-10` bucket. Do not add an 11th body system without noticing this.
3. **`act.act_probability` is useless** (reads ~1.0; AUROC 0.30 — issue #185). Gate on `confidence`.
4. **Base checkpoints are near chance zero-shot on typed-decisions** (0.362 vs 0.318 random) and ship
   over-confident (ECE 0.466 before temperature fitting). This is the mechanism behind PROMPT.md §14.3 /
   §18 "off-the-shelf Laya has no medical training" — expect a bad baseline; Phase B is the remediation.
5. `laya.load()` can hang if TensorFlow is installed (abseil deadlock) — run with `USE_TF=0`.
6. Decision recorded in `docs/decisions.md` 2026-10-09 (hosting choice + new-dependency approval).
### 2026-10-09 — Sequential reports was the indexer bottleneck, not extraction
- 5-doc corpus (~59 chunks) finished extract/graph/merge/communities in ~1 min;
  run then sat 17+ min in `[7/8]` because `generate_reports` called `narrate_fn`
  sequentially for 196 communities (104 L0 + 92 L1 — over-fragmented).
- Fix: level-by-level ThreadPoolExecutor in `graphrag/pipeline.py`, same
  `--workers` flag as extraction; `CostLedger.record` needed a lock for thread
  safety. No suite covers `generate_reports` — verified with stub smoke
  (multi-thread + order preserved) and full 152-test unittest run.
- Note: no `pytest` in `~/venvs/graphrag`; use `python -m unittest discover -s graphrag/tests`.

### 2026-10-09 — Fresh-run recipe (stores are MERGE/upsert-only)
- File wipe alone is NOT a fresh run: Qdrant upserts by stable point id and
  Neo4j writes are MERGE-only, so stale points/nodes survive. Clear with
  `QdrantStore.delete_collection` x3 + `MATCH (x) DETACH DELETE x`. Never
  `rm -rf data/qdrant data/neo4j` while containers run (live volumes).
- `graphrag/wikipedia.py` hits Wikipedia 429 after ~11 rapid fetches; sleeps
  of 45s did not help. Fetch in small batches with minutes between them.

### 2026-10-09 — Track H P1 notes
- harness_docs phase1 has no verbatim SYSTEM_PROMPT or tool schemas — wrote
  both from the described behaviours; note it if the reference ever pins text.
- `harness/` is run with cwd=`harness/` so intra-package imports must be
  absolute (`from tools.registry import ...`), not relative.
- Live Zen check: `BASE_URL=https://opencode.ai/zen/v1`,
  `API_KEY=$OPENCODE_ZEN_API`, `MODEL=space-bunny-free` works with openai SDK.

### 2026-10-09 — Zen free-tier model probe (all 9 free models)
- 8/9 free models return **403 "free tier can only be used from within OpenCode"** —
  platform restriction, not a bad key. Only `space-bunny-free` works externally.
- `space-bunny-free` intermittently **429 rate-limited**; earlier extraction worked,
  later probes throttled. Retry after minutes, not seconds.
- Catalogue: `GET https://opencode.ai/zen/v1/models` (42 models total, 9 free).

### 2026-10-09 — Track A build: simple stores for dev speed (user order)

- **Vector = in-memory numpy cosine** (`src/medharness/stores/vector.py`),
  **graph = networkx in-process** (`stores/graph.py`) — no Qdrant/Neo4j/Docker
  for dev speed. Drop-in adapters with the same upsert/search/MERGE-walk
  interface stay the goal; PROMPT.md sect 7.4 still says Qdrant+Neo4j.
- **Package named `medharness`** — `src/harness` collided with Track H's
  top-level `harness/` dir (CWD shadowing: repo-root `harness/__init__.py`
  won over the installed package). Verified: `import harness` from repo root
  resolved to Track H.
- **Embeddings:** MiniLM not cached on this box (first `SentenceTransformer`
  load timed out cold); S1 verified with a 32-dim hashed-token embedder
  (chest-pain query ranked `chest_pain::0` 0.638). Real MiniLM plugs into the
  same `embed(texts)->vectors` seam later.
- Seed: 4 synthetic docs / 14 entities / 4 MAY_SIGNAL edges, chunk ids
  `Doc::N` as stable provenance.
- **S2 decision client:** `decision/client.py` (parse Laya answers → typed
  `LayaVerdict`), `http_adapter.py` (live `POST /v1/systemone`), `fixture_adapter.py`
  (20 synthetic cases; tests use this, no network). `full_question_set()` sends
  acuity + 8 flags + extraction + guard in ONE batched request.
- **Abstain gotcha (cost me 2 debug rounds):** `abstained` = min confidence over
  SALIENT keys only (acuity + flags with p≥0.30). Sweeping all 8 flags lets a
  quiet sentinel flag's defaulted confidence (0.0, or the `_noul` 0.25 at p≈0.5)
  force a false abstain. Never gate safety on a flag nobody raised.
- **Fixture sentinel:** `_noul(p)` sets confidence 0.25 when |p−0.5|≤0.2 — so a
  salient borderline flag (e.g. dyspnea p=0.55) must be recorded with an
  explicit high confidence, else parse_verdict abstains. Real Laya gives a real
  confidence; the synthetic helper was the culprit, not the parser.

### 2026-10-09 — Hosted Kaggle Qwen replaces Zen/Ollama for generation; tool schema goes client-side

- **Model:** `qwen2.5-32b-instruct` Q4_K_M GGUF on Kaggle T4 x2 via llama-cpp-python
  server + Cloudflare tunnel (host script in `docs/hosting_qwen_kaggle.md`).
  Client: `OpenAI(base_url="https://pond-breathing-foto-advocate.trycloudflare.com/v1", api_key="sk-local")`.
  Tunnel URLs rotate — pass the new one via `KAGGLE_LLM_BASE_URL`, model stays.
- **Tool-schema change (the important part):** the server does NOT support the
  OpenAI `tools=` parameter, so schemas moved INTO the system prompt as Qwen
  `<tools>` XML; the model writes `<tool_call>{"name":…,"arguments":…}</tool_call>`
  as plain text; `parse_tool_calls()` regex-extracts it (strips ``` fences,
  coerces string `arguments`, reports unclosed blocks); results return as
  `<tool_response>` user turns. Code: `test/local_kaggle_llm.py`.
  Verified live: raw reply contained a real `<tool_call>` for `get_weather`,
  parser extracted it, loop returned the grounded answer.
- **Do not** send `tools=` to this endpoint — it is silently ignored. Any future
  medical tool (`kb_search`, etc.) follows the same client-side pattern.
