# AGENTS.md — repo facts, traps, gates, invariants

Read at session start, with `memory.md`. This file states facts; `PROMPT.md` is
the spec and wins on any conflict (`PROMPT.md` > `Plan.md` > `Implement.md` > `docs/`).

## What this repo actually is

A **Python medical decision-support harness** — spec in `PROMPT.md`, milestones in
`Plan.md`, **product code in `src/medharness/`** (built, 52 tests green). Two tracks:

| Track | What | Lives in | Spec / design |
|---|---|---|---|
| A | Medical harness (**the product**: triage, escalation, cited explanation) | `src/medharness/` | `PROMPT.md`, `Plan.md` M0–M12 |
| B | GraphRAG indexer (builds the index A reads) | `graphrag/` | `graphrag_plan.md`, `docs/rag_docs/` |

A never imports B's code; B never imports A. The one interface is data: B writes
the Qdrant+Neo4j index offline; A's `search_external_docs` reads it at query time.

> A generic coding-agent harness (`harness/`, "Track H") was **deprecated
> 2026-10-09** and moved to `deprecated/harness/`. Nothing imports it — do not
> resurrect it as product code.

## Start here

1. Read `memory.md`, then `PROMPT.md` §4 (non-goals), then the milestone in
   `Plan.md` §2, then `current_tasks.md` (red dots need a human).
2. Setup: `docs/runbook.md` (Laya + Docker stores + `.env`). Architecture:
   `docs/architecture.md`.
3. `PRODUCT.md` is the user-facing pitch; the engineering build log is
   `docs/decisions.md`.

## When to read what (everything else is on-demand)

- `PROMPT.md` — once in full, then when a rule is questioned. Amended only via `Plan.md` §6.
- `Plan.md` — before every milestone. Same amendment rule.
- `Implement.md` — the milestone loop + never-do list.
- `docs/decisions.md` — **the single decision log** (append-only, newest first).
- `docs/rag_docs/` — GraphRAG design; before touching Track B.
- `docs/reference/`, `docs/harness_docs/` — third-party notes; reference only.
- `out/*.md` — generated evidence; **never hand-edited**. `refer/*.docx` — superseded.

## Traps

1. **Repo name/history lie** (`asishdalal/redisccoding`, ex-C++ MiniRedis). No C++,
   no Redis, no scikit-learn classifier — the Laya+rules+LLM design replaced that.
2. **`.env` is gitignored but real.** Never commit, never `source` (hangs the shell —
   use `--env-file`), never print a key.
3. **No network in tests, ever.** Live probes (`test/probe_*`, `test/test_*_model.py`)
   are manual-only. Never claim a validation ran — report what actually ran.
4. **Ollama `laya` is dead on Linux** (MLX/Apple-only). Laya = `pip install "laya[serve]"`.

## Gates (`PROMPT.md` §19 — check before assuming blocked)

| § | Decision | Status |
|---|---|---|
| 19.1 | Laya contract | ✅ answered in practice — local `laya-serve` on `:8000` |
| 19.2 | Phase B label source | 🟡 open — gates M13+; descope honestly if absent |
| 19.3 | LLM backend | ✅ answered — hosted Kaggle Qwen; needs `KAGGLE_LLM_*` env |
| 19.4 | RAG corpus | 🔴 deciding — blocks indexing only, not the safety core |

Never substitute a mock and proceed past a gate. If the milestone needs an open
decision, stop and ask.

## Load-bearing invariants (enforced by `make arch` = `scripts/check_architecture.py`)

- **`rules/` imports only `contracts`** — structurally keeps the LLM off the
  escalated path. Only `rules/engine.py` builds `Escalation`; only
  `tools/registry.py` (`dispatch`) grants permission. Proven twice: import checker
  + raising-LLM-stub test (`tests/test_orchestrator.py`).
- **Laya decides. Rules escalate. LLM explains. Tools hold the truth.** On
  escalation `llm_called=false` and the Qwen client is never constructed.
- GraphRAG, not vector search: replicate the method, never add `microsoft/graphrag`
  (D16). Citations resolve to corpus **text units only**, never generated
  descriptions (D20, release-blocking). Indexing is offline, never in tests
  (`make validate`) (D19).
- Never: tune `holdout` or thresholds to pass; weaken/skip/delete a test; hand-edit
  `out/`; claim un-evidenced accuracy; add a dependency without approval; use real
  patient data. A failing blocking metric is a **finding** — publish it (§14.3).
- Scope: build only the milestone's Build list; the rest goes in `current_tasks.md`.
  Docs land in the same commit as their code. `README.md`/`PRODUCT.md` never overstate.

## Notes

- `memory.md`: read at session start; write learnings any time. No secrets, no
  patient data, no session narration.
- Phase A ends with a published baseline **including bad numbers** (off-shelf Laya
  has no medical training — poor accuracy is expected).
- Git: `main`; remote is someone else's repo — confirm before pushing. One milestone
  = one change set, `M{n}: {what} — {validation result}`.
- Test command: `python3 -m unittest discover -s tests` (no network, no key needed).
