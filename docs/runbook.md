# Runbook — base environment setup (Laya + Docker stores + env)

> **Read first, in this order:** `PROMPT.md` §7.4 (platform) → `Plan.md` §2 (which
> milestone you are on) → `docs/architecture.md` (Tracks A/B/H — what lives where)
> → `docs/decisions.md` (why Laya is local, why Ollama-laya is dead, measured
> numbers) → `memory.md` (setup gotchas) → `graphrag/README.md` (env table).
> This file is **how to reproduce the setup**, not why it is shaped this way.

## 0. What "base setup" means

Three independent services plus one file. Nothing here is code and nothing here
runs inside `make validate` (no network in tests, `Plan.md` D19).

| # | Piece | What it is | Lives at / on |
|---|---|---|---|
| 1 | **Laya (System-1 decision model)** | open-weight 421M checkpoint (`convaiinnovations/laya`, `english`, Apache-2.0), served over HTTP. Decides only — never generates text (`output_tokens: 0`). | `POST http://127.0.0.1:8000/v1/systemone`, venv `~/venvs/laya-server` |
| 2 | **Qdrant + Neo4j** | vector store (3 collections) + graph store. Dev-only, Docker. | `graphrag/compose.yaml`, ports 6333 / 7474+7687, volumes `data/` |
| 3 | **Generative LLM** | OpenAI-compatible endpoint for extraction/narration (Laya *cannot* do this). | env `LLM_*` (local Ollama or hosted) |
| 4 | **`.env`** | all credentials + URLs. Never committed (`.gitignore`), never `source`d. | repo root `.env`, real env vars win |

Track map (do not mix them): Track A = medical harness `src/harness/` (unbuilt);
Track B = indexer `graphrag/`; Track H = coding agent `harness/`.

## 1. Python interpreters and venvs

System python is 3.14 and **has SQLite FTS5**. `~/venvs/graphrag` python does
**not** (harness session search must run under system python or the laya venv).
On a fresh box none of these exist — create them:

```bash
python3 -m venv ~/venvs/laya-server      # decision-model server
python3 -m venv ~/venvs/graphrag        # indexer + its tests
python3 -m venv ~/venvs/harness         # Track H coding agent
~/venvs/graphrag/bin/pip install openai # Track H needs the OpenAI SDK
```

## 2. Laya — download once, serve locally

**Do not use Ollama for Laya** (measured dead end, `docs/decisions.md`): Ollama's
`laya` package is an MLX/Apple-Silicon build — `ollama pull laya` fails on Linux
x86_64. Do not retry this route.

What works: the official `laya[serve]` package, which downloads the checkpoint
from HuggingFace on first start and serves the same SystemOne wire format as the
hosted API (`{model, state, questions}` in, `{model, answers, usage}` out), so
`test/probe_laya_local.py` works against it unchanged.

```bash
~/venvs/laya-server/bin/pip install "laya[serve]"   # + CPU torch (host has no GPU)
# torch 2.14.1+cpu — CPU-only wheels exist; approved dependency, docs/decisions.md
```

Start it (one line; values explained below):

```bash
LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_DEVICE=cpu LAYA_MODELS=english \
LAYA_PRELOAD=1 LAYA_MAX_LOADED=1 LAYA_THREADS=2 USE_TF=0 \
~/venvs/laya-server/bin/laya-serve
```

Run it detached (`setsid nohup … & disown`) — restarting it inside the same
command call gets SIGTERM'd with the call. First start downloads ~421M weights
and preloads them (`LAYA_PRELOAD=1`), ~2.0 GB RSS resident.

Why these flags (all measured, `memory.md` 2026-10-09):

- `LAYA_MODELS=english LAYA_MAX_LOADED=1` — one checkpoint resident. Default
  `MAX_LOADED=2` holds english+multilingual ≈ 3 GB and OOMs a 7.8 GB no-swap box.
- `LAYA_HOST=127.0.0.1` — default binds `0.0.0.0`; override on a dev box.
- `LAYA_DEVICE=cpu LAYA_THREADS=2` — 2 cores, no GPU.
- `USE_TF=0` — `laya.load()` hangs on an abseil deadlock when TensorFlow is present.
- Only the `english` model: 421M / 512-token context (~320 tokens usable for
  state; longer states truncate — the response reports `truncated`).

Relevant env (client side, go in `.env`): `LAYA_BASE_URL=http://127.0.0.1:8000`,
`LAYA_MODEL_ID=laya`. Code default is the same (`graphrag/config.py`,
`graphrag/system1.py`); `.env`/real env override it, never hardcoded creds.

Verify:

```bash
curl -s http://127.0.0.1:8000/health        # reports checkpoint revision SHA → trace §12
python3 test/probe_laya_local.py            # 1 request + answer shapes
python3 test/probe_laya_local.py --repeat 5 # determinism (§16.3; local is byte-identical 6/6)
```

Known findings, not defects (carry to the M12 baseline, do not "fix" by tuning):
median latency ~2301 ms vs the §7.1 ≤150 ms budget (~15× over — 2 CPUs, no GPU);
`choice` with ≥11 options returns uncalibrated confidence (keep extraction at 10);
`noul` can follow its labels instead of the state (upstream #156 — threatens the
100% red-flag-recall blocker, measure early); gate on `confidence`, never on
`act.act_probability` (~1.0 always).

## 3. Qdrant + Neo4j in Docker (downloaded, dev-only)

Images: `qdrant/qdrant:latest` + `neo4j:5-community`, declared in
`graphrag/compose.yaml` (compose lives in `graphrag/`, not repo root — Track B
is self-contained). Volumes persist under `data/qdrant` + `data/neo4j`
(gitignored). Ports: Qdrant `6333` (+`6334`), Neo4j `7474` (browser) + `7687`
(bolt). Neo4j auth is `neo4j/${NEO4J_PASSWORD}` — the `:?` in compose means it
**refuses to start without the password in the environment**.

```bash
cd graphrag && docker compose --env-file ../.env up -d   # NEVER `source ../.env`
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'   # both Up
curl -s http://127.0.0.1:6333/collections                # Qdrant answers
```

First `up` **downloads** both images then creates the volumes. Afterwards data
persists in `data/` across restarts — `down` keeps it; clearing the index is
`QdrantStore.delete_collection` x3 + `MATCH (x) DETACH DELETE x`, never
`rm -rf data/*` while containers run (live volumes — `memory.md` recipe).

Env (in `.env`): `QDRANT_URL=http://127.0.0.1:6333`, `NEO4J_URI=bolt://127.0.0.1:7687`,
`NEO4J_USER=neo4j`, `NEO4J_PASSWORD=<secret>`. Code defaults match
(`graphrag/config.py`); `require_stores()` fails loudly without the password.
Qdrant holds 3 collections (`entities`, `text_units`, `community_reports`,
384-dim MiniLM, upsert by stable id); Neo4j writes are MERGE-only so re-runs
never duplicate (`graphrag/stores.py`).
## 4. Generative LLM (whatever does extraction — Laya cannot)

Extraction/narration needs a model that **emits text**. Laya returns
`output_tokens: 0` and never writes a string (`require_llm()` says so
explicitly). Two working options:

- **Local:** Ollama + `qwen2.5:1.5b` (986 MB, ~10-15 s/turn on 2 cores — dev
  only, not batch indexing): `BASE_URL=http://localhost:11434/v1 API_KEY=ollama
  MODEL=qwen2.5:1.5b`. Gotcha: a half-downloaded installer leaves `ollama`
  without `llama-server` (every inference 500s) — re-run the installer to
  completion; weights in `~/.ollama/models/blobs/` survive, never re-pull.
- **Hosted:** OpenCode Zen, `LLM_BASE_URL=https://opencode.ai/zen/v1`,
  `LLM_MODEL_ID=space-bunny-free`, key via `LLM_API_KEY` or `OPENCODE_ZEN_API`
  (only `space-bunny-free` works externally — 8/9 free models 403; it 429s, so
  backoff and retry after minutes). Always send an explicit `User-Agent`
  (default `Python-urllib` gets Cloudflare 403/1010); it is a reasoning model
  (~85% `reasoning_tokens` — budget `max_tokens` for reasoning + JSON or
  responses truncate); default `reasoning_effort="minimal"`, but Ollama-qwen
  rejects thinking params, so `GRAPHRAG_REASONING_EFFORT=none` there.

Precedence (`graphrag/config.py::from_env`): `LLM_*` wins, then
`OPENCODE_ZEN_*`, then defaults. Repo `.env` is loaded by `load_dotenv()` but
**real env vars always win** (which is what CI uses). Verify:
`~/venvs/graphrag/bin/python test/test_llm_model.py` (manual, never in validate).

## 5. `.env` — the one file (never committed, never sourced)

| Variable | Example | Used by |
|---|---|---|
| `LAYA_BASE_URL` / `LAYA_MODEL_ID` | `http://127.0.0.1:8000` / `laya` | System-1 decisions |
| `NEO4J_PASSWORD` (**required**) | `<secret>` | compose refuses to start without it |
| `NEO4J_URI` / `NEO4J_USER` | `bolt://127.0.0.1:7687` / `neo4j` | graph store |
| `QDRANT_URL` | `http://127.0.0.1:6333` | vector store |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_ID` | see sect 4 | extraction/narration |
| `GRAPHRAG_CORPUS_DIR` / `GRAPHRAG_REASONING_EFFORT` | `corpus_test` / `minimal` or `none` | indexer |
| `GRAPHRAG_CHUNK_CHARS` / `GRAPHRAG_CHUNK_OVERLAP` / `GRAPHRAG_MAX_TRIPLES` | `4800` / `400` / `10` | chunking + cost lever |

Rules: `.env` + `.env.*` gitignored (only `.env.example` commits); keys arrive
by environment, never hardcoded (`PROMPT.md` sect 7.4); **never `source .env`**
— it can spew output and hang the shell; pass it via
`docker compose --env-file ../.env` (code reads it via `load_dotenv()`); never
print or log a key (probes print length only).


## 6. Order of operations (fresh box → working base)

```bash
python3 -m venv ~/venvs/laya-server && ~/venvs/laya-server/bin/pip install "laya[serve]"
# + CPU torch; first laya-serve start downloads the 421M checkpoint from HF
setsid nohup env LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_DEVICE=cpu LAYA_MODELS=english \
  LAYA_PRELOAD=1 LAYA_MAX_LOADED=1 LAYA_THREADS=2 USE_TF=0 \
  ~/venvs/laya-server/bin/laya-serve > /tmp/laya.log 2>&1 < /dev/null & disown
curl -s http://127.0.0.1:8000/health
cd graphrag && docker compose --env-file ../.env up -d && cd ..
python3 test/probe_laya_local.py                       # Laya live
~/venvs/graphrag/bin/python test/test_llm_model.py     # generative LLM live
~/venvs/graphrag/bin/python -m unittest discover -s graphrag/tests   # offline, no key
```

RAM budget (7.8 GB box): Neo4j+Qdrant+laya take ~5 GB, ~2.5 GB free — a 1.5B
local LLM fits; do NOT pull 3B+ without stopping something. Wikipedia fetch
429s after ~11 rapid articles — space fetches minutes apart (45 s is not enough).

## 7. When it breaks (measured fixes)

- `laya-serve` 500 "llama-server binary not found" → installer timed out
  mid-download; re-run to completion, restart detached.
- `NEO4J_PASSWORD is not set` from compose → `.env` missing/unread; check the
  path (`../.env` from `graphrag/`), never `source` it — `grep` the key name only.
- After a daemon hiccup `docker start` fails (runc desync) → `rm` + recreate
  via compose; data survives in `data/` unless wiped.
- Stale index after a code change (file wipe changed nothing) → stores are
  upsert/MERGE-only; clear collections + Neo4j explicitly (sect 3).
- `403 error 1010` from the LLM edge → missing `User-Agent`, not a bad key.

## 8. Dev/test fast path — end-to-end WITHOUT Docker

To test the whole harness against the real corpus without Qdrant/Neo4j, use the
**fast local stores** (see `docs/decisions.md`, `dev/README.md`). Only the stores
differ from production — same hosted Qwen, same Laya, same `QueryEngine`.

> **Live tunnel (verified working):** `https://pond-breathing-foto-advocate.trycloudflare.com/v1`,
> model `qwen2.5-32b-instruct`, key `sk-local`. Laya is NOT up on `:8000` in this
> box, so the runner falls back to recorded fixtures for decisions (see below).

```bash
# 1) Build the graph with the REAL pipeline, but skip the Qdrant/Neo4j writes.
#    The hosted Qwen does the extraction (it is a generative model — Laya cannot;
#    it returns output_tokens:0). Use data/corpus_min/ for a fast run.
LLM_BASE_URL=https://pond-breathing-foto-advocate.trycloudflare.com/v1 \
LLM_API_KEY=sk-local LLM_MODEL_ID=qwen2.5-32b-instruct \
python3 -m graphrag.pipeline --corpus-dir data/corpus_min \
    --skip-stores --skip-reports --batch 2 --workers 2 --max-triples 8
#  -> data/graph.json (in-memory graph; extraction is SLOW over the tunnel)

# 2) Load it into fast stores and run the harness end to end.
export KAGGLE_LLM_BASE_URL=https://pond-breathing-foto-advocate.trycloudflare.com/v1
export LAYA_BASE_URL=http://127.0.0.1:8000        # real Laya (laya-serve, sect 2)
python3 dev/run_e2e.py                            # Laya if up, else fixtures
MEDH_LIVE=1 python3 dev/run_e2e.py                # force Laya at :8000 (fails if down)
```

`dev/build_fast_engine.py` loads `data/graph.json` into
`graphrag.stores.MemoryVectorStore` (fast vector) + the in-memory
`graphrag.graph.Graph` (fast graph), wired to real Laya routing + hosted-Qwen
generation, and drives `medharness`. Production uses Qdrant + Neo4j instead
(sect 3) — the graph and engine are identical.

**Measured:** extraction over the tunnel is ~1 chunk / 30 s (Qwen round-trip +
Cloudflare). `data/corpus_min/myocardial_infarction.txt` (47 KB) chunks to ~19.
`dev/run_e2e.py` then runs L1→L2→L3 on a safe question and the escalated path
in ~15–20 s per Qwen call. `dev/` is **not** part of `make validate`.

