# Architecture

## Tracks in this repo (two, independent)

| Track | What | Lives in | Spec |
|---|---|---|---|
| A | Medical decision-support harness (**the product**) | `src/medharness/` | `PROMPT.md`, `Plan.md` M0–M12 |
| B | GraphRAG indexer (knowledge-graph builder) | `graphrag/` | `docs/rag_docs/`, `docs/graphrag_plan.md` |

Rules: A never imports B's code; B never imports A. The **one interface** is data:
B writes the Qdrant+Neo4j index offline; A's `search_external_docs` reads it
(`GraphRAGExternalSearch` wraps `graphrag.query.QueryEngine` at query time only —
never at import time, never in a test).

> A generic coding-agent harness formerly lived in `harness/` (Track H). It was
> deprecated 2026-10-09 and moved to `deprecated/harness/`. Nothing imports it.

## Track A — the product (`src/medharness/`)

```
src/medharness/
├── contracts/        # pydantic boundary models; the ONLY module rules/ imports
├── decision/         # Laya client: http_adapter (live) + fixture_adapter (tests)
├── rules/            # deterministic escalation: thresholds.py + engine.py (no LLM)
├── stores/           # simple dev stores: vector.py (numpy) + graph.py (networkx)
├── tools/            # lookup, kb (internal), external_search (graph walk), registry
├── generation/       # Qwen client-side tool loop + policy gates
├── orchestrator.py   # assess(): Laya → rules → [Qwen]; returns before LLM on escalate
├── receipt.py        # verifiable Decision Receipt (calibrated, reproducible)
├── demo.py           # offline demo entry point
└── service/app.py    # FastAPI edge (POST /assess, GET /health)
```

## Track A — the safety spine (data flow)

```
text → orchestrator.assess()
  → decision_client.decide()      # LAYER 1 Laya: acuity + red flags (numbers)
  → rules.engine.escalate()       # LAYER 2: deterministic; danger ⇒ STOP
       └─ escalated? return Escalation, llm_called=false  ← Qwen never built
  → generation.run_tool_loop()    # LAYER 3 (non-escalated only): explains + cites
       ├─ kb_search (internal, fast vector)
       └─ search_external_docs (full graph walk, when internal insufficient)
  → policy.screen()               # banned-phrase + citation gate before return
  → HarnessResponse + Decision Receipt
```

## Track A — dependency contracts (enforced by `scripts/check_architecture.py`)

| Module | May import |
|---|---|
| `rules/*` | **`contracts` + stdlib only** (the LLM-isolation guarantee) |
| `contracts/*` | stdlib, `pydantic` |
| `tools/*` | `contracts`; `stores`, `decision`, `generation` lazily |
| `decision/*`, `generation/*`, `orchestrator.py` | `contracts` + their layer |
| `orchestrator.py` | everything (the single wiring point) |

Only `rules/engine.py` constructs `Escalation`. Only `tools/registry.py`
(`dispatch`) is the path from a tool call to execution, and it enforces the role
matrix. Proven twice: statically by the import checker, dynamically by the
raising-LLM isolation test (`tests/test_orchestrator.py`).

