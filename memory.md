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
