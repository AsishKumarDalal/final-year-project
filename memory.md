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
- Skip session narration, task progress, and anything git already records. Progress belongs in `Plan.md` §2; rationale in `PROMPT.md` and `Plan.md` §7; commands in `docs/runbook.md`; deferred work in `docs/TODO.md`.
- **Never put secrets, keys, or patient data in this file.**

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

### Traps in Laya itself (from its own model card — these bite our red-flag design)

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