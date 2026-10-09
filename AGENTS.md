# AGENTS.md

> ## ⏳ Index first — model access lasts 2–3 days from 2026-10-08
>
> GraphRAG **indexing** consumes model tokens; everything else does not. Order is:
>
> ```
> M0 M1 → M6a M6b M6c (BUILD + INDEX NOW) → M4 M5 (safety core) → M6d → M7 …
> ```
>
> **This is a recorded amendment, not a rule break** — `docs/decisions.md`, 2026-10-08. The original order was sound; the assumption behind it (stable model access) was wrong. Read `docs/decisions.md` before changing it again.
>
> Inside indexing, prioritise **extraction → description summarisation → community reports**. Extraction is ~75% of index cost and everything else derives from it. If the window closes early, stop after extraction — that is the recoverable end state.
>
> The safety core still ships **before anything is wired together**, so escalation isolation is proven before the harness exists.

## What this repo actually is

A **Python medical decision-support harness** spec. Not a Redis project, despite the name.

The three markdown files at root are the entire deliverable so far. **There is no source code, no manifest, and no test suite yet.** Everything they describe is unbuilt.

## File map — what every file is, where it lives, when to read it, when to write it

**Read** = open it. **Write** = you are expected to edit it.
🔒 = do not edit directly. ✏️ = you edit it.

### Root — governing specs

| File | What it is | Read | Write | Exists? |
|---|---|---|---|---|
| `PROMPT.md` | **The specification** — goals, non-goals, architecture, constraints, schemas, metrics, gates (§19) | Once in full, then whenever a rule is questioned | 🔒 amendment via `Plan.md` §6 only | ✅ |
| `Plan.md` | **Milestones + validation** — M0–M15, commands, architecture contracts, decisions D1–D20 | Before every milestone | 🔒 amendment via `Plan.md` §6 only | ✅ |
| `Implement.md` | **The runbook** — milestone loop, scoped diffs, stop-and-fix, never-do list | Session start | Rarely — only if the procedure itself is wrong | ✅ |
| `AGENTS.md` | **This file** — repo facts, traps, gates, invariants | Session start | Only when a repo fact changes (new file, new trap, gate moved) | ✅ |
| `memory.md` | **Agent's free-form learnings** — mistakes, quirks, dead ends, corrections | Session start | ✏️ **any time** — the one file exempt from scope rules | ✅ blank |
| `README.md` | Public entry point — what this is, setup, how to run, non-goals | Before writing any docs | ✏️ at M0, then kept current | ❌ **M0** |

### `docs/` — living project documentation

Update in the **same commit** as the code it describes.

| File | What it is | Read | Write | Exists? |
|---|---|---|---|---|
| `docs/decisions.md` | **Append-only amendment log** — what changed, why, the cause | Before proposing any reorder or amendment | ✏️ append only — never rewrite an entry | ✅ |
| `docs/rag_implementationplan.md` | GraphRAG design — index pipeline, query modes, Docker, costs | Before touching M6 | ✏️ with M6 | ✅ |
| `docs/reference/` | Third-party notes (Hermes). **Reference only — never a dependency** | When considering adopting something | ✏️ rarely, to record a finding | ✅ |
| `docs/architecture.md` | Three layers, `Plan.md` §4 dependency contracts, data flow | When changing structure | ✏️ same commit as code | ❌ M0 |
| `docs/runbook.md` | How to install, test, evaluate, run the service | When a command changes | ✏️ same commit as code | ❌ M0 |
| `docs/tools.md` | Each tool's signature, returns, data provenance | When adding a tool | ✏️ same commit as code | ❌ M5 |
| `docs/evaluation.md` | Metric definitions, how to run them, what each target is for | Before running metrics | ✏️ same commit as code | ❌ M11 |
| `docs/limitations.md` | What the system cannot do, stated plainly | Before claiming anything | ✏️ any time a limitation is found | ❌ M0 |
| `docs/TODO.md` | Deferred work — one line each, with the milestone | When scope discipline kicks in | ✏️ any time something is out of scope | ❌ M0 |

### Not documentation — do not treat as docs

| Path | What it is | Read | Write |
|---|---|---|---|
| `out/*.md` | **Generated evidence** — reports, baselines | When reporting a result | 🔒 **never hand-edited** — editing falsifies the result |
| `refer/*.docx` | Superseded original project doc — see Trap 2 | Background only | 🔒 never |
| `.env` | API keys for Laya and the LLM | — | 🔒 **never committed** |

### Code — nothing here exists yet

| Path | What it is | Exists? |
|---|---|---|
| `src/harness/{contracts,rules,tools}/` | The three layers. `rules/` imports **only** `contracts` | ❌ M0–M8 |
| `tests/` | Unit + integration. **No network, ever.** | ❌ M0 |
| `scripts/check_architecture.py` | The import checker behind `make arch` | ❌ M0 |
| `Makefile` | `validate`, `arch`, `up`, `rag-cost` | ❌ M0 — **first deliverable** |
| `pyproject.toml` | Manifest — ruff, mypy, pytest config | ❌ M0 |
| `.gitignore` | ⚠️ stock C++ template, protects nothing — see Trap 4 | ✅ **wrong, M0 must fix** |
| `evals/sets/` | Quoted eval cases (no paraphrase) | ❌ M11 |
| `data/{qdrant,neo4j}/` | The index — durable once built | ❌ M6 |

## Verified repo state

- **In `HEAD`:** `.gitignore`, `PROMPT.md`, `Plan.md`, `refer/*.docx`
- **Uncommitted:** `AGENTS.md`, `Implement.md`, `memory.md`, `docs/`
- **No CI exists.** `make validate` is local-only — never report it as passing on CI.
- Branch `main`; `origin/dev` still holds the old C++ files. Confirm before pushing.

## Traps — check these before writing anything

**1. The repo name and git history are misleading.**
Origin is `asishdalal/redisccoding`; history is a C++ MiniRedis project. `miniredis.cpp` and `README.md` were **deliberately deleted** (committed as deletions in `912f9a8`). Do not resurrect them, do not reach for a C++ toolchain, do not add Redis-related code. `README.md` is an M0 deliverable — it does not exist yet, so there is no project documentation to read.

**2. `refer/*.docx` is superseded. It is not the spec.**
It is the original "AI-Based Disease Prediction" project document that `PROMPT.md` deliberately departed from — the classifier-training sections were dropped in favour of a Laya decision layer + rules + LLM harness. Reading it and starting to train a scikit-learn classifier is the most likely wrong turn in this repo. Use it only for background.

**3. No commands work yet.**
`make`, `pytest`, `ruff`, `mypy` — none are installed or configured. The `Makefile` is M0's first deliverable. **Never claim a validation ran, or that a result "should" pass.** Report what actually ran.

**4. `.gitignore` is the stock C++ template — it protects nothing here.**
Verified absent: `__pycache__`, `.venv`, `.env`, `out/`, `.pytest_cache`, `*.pkl`. The project handles API keys for Laya and an LLM, so **`.env` is currently committable by accident.** M0 adds these; until then, check `git status` before staging anything.

## Spec precedence

`PROMPT.md` > `Plan.md` > `Implement.md` > `docs/`

On conflict: the higher file wins and the lower one gets corrected — never the reverse. `PROMPT.md` and `Plan.md` change only through the amendment procedure in `Plan.md` §6 (written finding → smallest change → logged decision → re-run `make validate`). They are never edited to make a check pass.

## Start here

1. Read `PROMPT.md` in full, then `Plan.md` in full, then `Implement.md`.
2. Read `PROMPT.md` §4 **non-goals** before writing code. Knowing what not to build prevents more wasted work than any other single section.
3. Find the first incomplete milestone in `Plan.md` §2.

## Gate status

`PROMPT.md` §19 — **these move.** Check current status there before assuming a milestone is blocked.

| § | Decision | Status | Blocks |
|---|---|---|---|
| 19.1 | Laya API contract | 🟡 open — does **not** block M0/M1/M4/M5/M6 | M2, M3 |
| 19.2 | Phase B label source | 🟡 open | M13+ |
| 19.3 | LLM backend | ✅ answered 2026-10-08 — time-limited models; still needs `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_ID` env values | M6, M7 |
| 19.4 | RAG corpus | 🔴 **deciding now — critical path for indexing** | M6 |

Do not substitute a mock and proceed past a gate. If a milestone needs an open decision, stop and ask.

## Architecture invariants that look like refactor targets

These are load-bearing. Each is enforced by `make arch` (`scripts/check_architecture.py`) once it exists.

- **`src/harness/rules/` may import only `contracts`.** This is what *structurally* guarantees the LLM is never reached on the escalated path. Do not "clean up" a rule module by importing a config helper or a tool.
- **Only `rules/engine.py` may construct an `Escalation`.** Only `tools/registry.py` may grant tool permission.
- **The escalation path is proven twice** — statically by the import checker, dynamically by a test that substitutes a raising LLM stub. Don't drop either.
- **Laya is remote HTTP, not an in-process dependency.** No local Laya install or checkpoint is expected. Tests run on recorded fixtures (`Plan.md` D2, D3).
- **No network in any automated test.** Live checks are a separate manual target.

## Maintain `memory.md`

**Read it at the start of every session. Write to it whenever you learn something worth keeping.** It is the one file you are expected to edit even when it is not in the milestone's Build list — scope rules (`Implement.md` §3) do not apply to it.

Write an entry when you hit any of these:

- A mistake you made here — what it was, what actually fixed it
- An environment quirk, version pin, or setup gotcha that cost you time
- Non-obvious behaviour of Laya, the LLM provider, or a data file
- A dead end — what you tried, why it failed, so nobody repeats it
- A correction the user gave you
- Something you had to dig for that isn't written anywhere else

**Use whatever format you like** — headings, bullets, prose, tables. The structure is yours; don't preserve a template and don't reorganise someone else's entries. Keep each entry short so a future agent gets the substance in under a minute.

Don't write session narration, task progress, or anything git already records — that belongs in `Plan.md` §2, `docs/runbook.md`, and commit history. Never put secrets, keys, or patient data in it.

## Maintain `docs/`

Project documentation lives in `docs/`, not in the root and not in docstrings. Create it at M0 and keep it current as you build.

**Update docs in the same commit as the code they describe.** A milestone is not complete until its documentation lands with it — deferred docs are the same failure as a skipped test wearing a different hat.

Two rules that carry real consequences:

- **`README.md` may never overstate.** No claim of clinical validation, no "diagnoses", no accuracy figure that does not appear in `out/`. Every limitation goes in `docs/limitations.md`; silent omission is a defect.
- **`out/*.md` are not docs.** They are generated evidence — never hand-edited. Documentation is the `docs/` set above.

What each file owns is in the **File map** above.

## Retrieval is GraphRAG, not vector search

`docs/rag_implementationplan.md` is the design. Read it before touching M6.

- **Qdrant + Neo4j in Docker**, dev-only. `make up` / `make down`.
- **`microsoft/graphrag` is in maintenance mode and accepts no new features.** We replicate the method, we do not take the dependency. Adding the `graphrag` package is a defect (`Plan.md` D16).
- **Laya routes local / global / basic search.** A routing decision belongs in System 1 — not an LLM classifier, which would make identical queries pick different modes and break reproducibility (`Plan.md` D17).
- **Citations resolve to corpus text units, never to generated entity or relationship descriptions.** Entity descriptions are model output. A citation satisfiable only by generated text is invalid — this is what keeps GraphRAG from becoming a hallucination engine (`Plan.md` D20). Release-blocking metric.
- **Indexing is offline and LLM-dependent.** It must never run inside a test or a request path, and never as part of `make validate` (`Plan.md` D19). Extraction is ~75% of index cost, so always measure with `make rag-cost`.
- `Plan.md` D6/D7/D8 are **superseded**, not deleted. Don't "restore" them.

## Rules that will look like obstacles

These are deliberate. Treat a request to remove one as a defect report, not a task.

- **Never tune against `holdout`.** Any tuning invalidates the evaluation.
- **Never hand-edit `out/*.md`.** Generated evidence. Editing it falsifies the result.
- **Never weaken a test, skip it, or delete it** to make a gate green.
- **Never tune a safety threshold to hit a target number.** Thresholds in `rules/thresholds.py` are versioned safety parameters (`Plan.md` D13).
- **A failing blocking metric is a finding, not a defect.** Record it in the baseline report. Don't lower the target in `PROMPT.md` §15.2.
- **Scope discipline:** build only the current milestone's Build list. Found something else? It goes in `docs/TODO.md`, not the diff. Named temptations are in `Implement.md` §3.2.
- **Docs update in the same commit as the code.** A milestone is not done until its docs land with it.

## Planning

Phase A (M0–M12) ends with a published baseline. **Stage F may not begin before that report exists** — otherwise you tune against a target you invented.

`PROMPT.md` §14.3 is the no-hiding rule: the baseline is published **including bad numbers**. Off-the-shelf Laya has no medical training, so poor accuracy is the expected result, and reporting it is the requirement.

## Git

Branch and push conventions are not settled — the remote is someone else's repo. Confirm before pushing anywhere. Commit granularity (`Plan.md` milestone = one change set, format `M{n}: {what} — {validation result}`) is settled; everything else is not.