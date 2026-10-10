# harness_plan.md — SUPERSEDED (historical record)

> **Deprecated 2026-10-09.** This was the plan to *convert* the old coding-agent
> `harness/` (Track H) into the medical harness. That conversion is **done** — the
> product is `src/medharness/`, and the donor `harness/` now lives in
> `deprecated/harness/`. Kept only so the reuse-vs-replace history stays checkable.
> Current architecture: `docs/architecture.md`. Current build status:
> `current_tasks.md`.

---

## 1. What `harness/` was (keepable mechanics — already reused)

`main.py` (31L) dumb REPL calls `agent/loop.py run()` (42L), which calls `agent/llm.py chat()` (41L, OpenAI chat-completions, env `BASE_URL/API_KEY/MODEL`), then `tools/registry.py dispatch()` (56L, single chokepoint), then `tools/files.py` (38L: read/write/run_command) returning an observation string, loop again. `prompt.py` (23L): coding assistant, THINK→ACT→OBSERVE, `finish(summary,evidence)`. `tests/test_loop.py` (99L, 7 tests, stubbed chat): direct answer, read-then-answer, bad-JSON→observation, unknown-tool DENY, finish, truncation 2000 chars, budget 25 turns exhausted.

Already-good, keep the logic: RULE 1 (assistant msg appended before tool msgs), errors-as-observations never crashes, truncation marker, `Finish` intercepted before registry, `dispatch` as the ONLY door to execution.

Already-built medical half in `graphrag/`: `system1.py` (`System1Client.decide(state,questions)` over `POST /v1/systemone`) + `query.py` (Q0-QG pipeline, `text_unit:`-only citations, `validate_source_ids` hard-fail). These get wrapped, not rewritten.

## 2. What medical must be (`PROMPT.md` sect 5 law)

> **Laya decides. Rules escalate. LLM explains. Tools hold the truth.** If LLM output can change a safety outcome, the design is wrong.

Flow: `text → LAYER1 Laya (decides only, no generation: acuity distribution + 8 red_flag P(true) + extracted fields) → LAYER2 rules/engine.py (deterministic, no model; ANY flag over threshold OR acuity over urgent → ESCALATE + trace + STOP, llm_called=false) → LAYER3 System2 LLM (explains ONLY if not escalated; may call kb_search/get_lab_ref/get_drug_interactions/summarize_report; every claim cited; refuses diagnosis/treatment; crisis text from config) → {decision, escalation?, explanation?, citations[], decision_trace}`.

Roles enforced at the tool boundary (sect 6 matrix patient/nurse/doctor, trusted `X-Actor-Role` header, enforced inside harness). Invariants: `rules/` imports ONLY `contracts`; only `engine.py` constructs `Escalation`; only `registry.py` grants permission; proven twice (static `make arch` + dynamic raising-LLM test); no network in any test.

## 3. Reuse vs replace

| `harness/` piece | Verdict | Becomes in `src/harness/` |
|---|---|---|
| `agent/loop.py` mechanics (ordering, ERROR-obs, truncate, budget) | KEEP logic, MOVE | `generation/loop.py` — System2 loop ONLY, never the entrypoint |
| `tools/registry.py dispatch` chokepoint shape | KEEP shape, SWAP contents | `tools/registry.py` + permission matrix (sect 6) |
| `agent/llm.py` thin client | KEEP thin, MOVE + harden | `generation/llm.py` (retries later, P6 pattern) |
| `agent/prompt.py` coding prompt | REPLACE | medical explainer: cited-only, banned-phrase, refusal codes, D11 crisis verbatim |
| `tools/files.py` read/write/run_command | DELETE from medical path | `tools/kb.py, labs.py, drugs.py, reports.py` returning {facts,source_ids,source_type}; `not_found` never guess |
| `main.py` REPL | DEMOTE to debug adapter | real entry `orchestrator.assess(text,role)` + `service/app.py` FastAPI |
| `tests/test_loop.py` | KEEP as pattern, ADD safety tests | `test_policy/citations/permissions` + `test_escalation_isolation` (raising stub, 0 LLM calls) |
| `graphrag/system1.py + query.py` | REUSE directly | `decision/http_adapter.py` wraps client; `tools/kb.py` wraps `ask()` |

## 4. Target layout (new; do NOT build inside `harness/`)

`src/harness/contracts/` (M1: HarnessRequest/Response, Decision, Escalation, DecisionTrace, pydantic v2) + `questions.py` (SCHEMA_VERSION + content hash). `data/questions/{acuity,red_flags,extraction,guard}.json` (verbatim sect 8). `decision/{client.py: Protocol decide(), http_adapter.py, fixture_adapter.py}`. `rules/{thresholds.py v1, engine.py: (trace,thresholds)->Escalation|None}`. `tools/{base.py, kb.py, labs.py, drugs.py, reports.py, registry.py}`. `data/tables/{lab_ranges,drug_interactions}.json` versioned. `generation/{llm.py, loop.py, policy.py, citations.py, schemas.py}`. `orchestrator.py: assess(text,role)`. `service/{app.py, routes.py}`. `scripts/{check_architecture.py, validate.sh, record_fixtures.py}`. `tests/{unit,integration,contract,security}`. `evals/sets/*.jsonl`. `out/` generated, never hand-edited.

## 5. Build order (one milestone = one commit + report per `Implement.md` sect 8)

- M0 scaffold: `pyproject, Makefile(validate/arch/type/up/rag-cost), src/harness/__init__, check_architecture.py, .env.example(LAYA_*/LLM_*), .gitignore fix(.env,out,venv)` → `make validate` green, zero tests.
- M1 contracts+schemas: sect 8 JSON verbatim, hash-stable, `escalated=True` requires escalation typing → `pytest test_questions test_contracts + make type`.
- M2-M3 decision client [gate sect 19.1]: `record_fixtures.py`, 20+ cases in `data/fixtures/`; http adapter (timeout + typed errors) vs fixture adapter (unknown-id raises) pass same contract test; ALL tests use fixture adapter, no network.
- M4 rules [safety-critical]: 5 sect 9 branches (suicide≥0.50, other≥0.70, acuity≥urgent idx≥2, resuscitation≥0.10, abstain+flag≥0.40); downgrade-forbidden; boundaries 0.699/0.700 and 0.099/0.100; 100% branch cover; `make arch` proves rules←contracts only.
- M5 lookup tools: pure file lookups, `not_found` never guess, sourced facts, versioned tables → cover ≥90%.
- M6 kb_search [reuse Track B]: `kb.py` wraps `graphrag/query.py ask()`; typed `text_unit:` ids with `validate_source_ids` hard-fail; RAG text in data channel not instruction channel (D10); `mode:none` allowed; stores-down → `degraded:true`, decision still returned.
- M7 LLM policy: guard→reason-code refusals; pre-return citation gate; banned-phrase check; suicide crisis from config never generated; adapter has no path to rules/decision per arch.
- M8 orchestrator [safety-critical]: `assess()` L1→L2→[L3]; raising-LLM stub proves 0 calls on every escalation; verbatim instruction text; full trace per sect 12 (model/schema/rules/thresholds/raw/tools/citations/llm_called); LLM-down → degraded, still decides; 5× runs byte-identical decision.
- M9 roles: allow+deny test per sect 6 cell, `permission_denied` logged, tests bypass transport (proves server-side enforcement).
- M10 edge: FastAPI `POST /assess, GET /health /schemas/{n} /traces/{id}`; role from header only; committed openapi.json.
- M11-M12 evals+baseline: sets ≥100/60/40/100 synthetic; blocking metrics exit-nonzero (recall 100, refusal 100, isolation 0 calls, citations 100% resolvable + 0 gen-text, routing 100, injection 100, banned 0, determinism 100); publish `out/baseline_report.md` WITH bad numbers (sect 14.3 — off-shelf Laya expected poor, no medical training).

## 6. Fixed constants (copy verbatim, never tune to pass)

Schemas sect 8: acuity `score[routine,soon,urgent,resuscitation]` + full distribution; 8× `noul` red_flags in ONE request (chest_pain, severe_dyspnea, altered_consciousness, stroke_signs, major_haemorrhage, severe_abdominal_pain, anaphylaxis, suicide_risk); extraction `choice` body_system 10 criteria (an 11th breaks temperature calibration — memory.md 2026-10-09); guard 3× `noul` (diagnosis/treatment/self_harm). Thresholds sect 9 + versioned file (D13). Escalation JSON shape + `llm_called:false` + verbatim instruction. Crisis field from config (D11). Never: diagnosis wording, dosing, tuning thresholds or holdout, weakening tests, hand-editing `out/`.

## 7. Validation, gates, risks

Every milestone: its listed command + always `make arch && make type && make validate`. Honesty (`Implement.md` 8.1): claim only what ran this session; bad numbers stay bad. Gates sect 19: M2/M3 blocked on 19.1 Laya contract (env only, never hardcoded); M6/M7 need `LLM_*` values (19.3 answered, values pending); corpus 19.4 gates indexing only — medical build proceeds on fixtures regardless; 19.2 labels gate Phase B (M13+) — descope honestly if absent. Risks: hosted Laya measured 384–526ms over 150ms budget + 429 + non-identical (memory.md) → fixture adapter + single batched call + backoff; `choice:11+` uncalibrated; `noul` label-following (#156) threatens 100% recall → measure early; shell/file tools from H must NOT leak into A (lethal trifecta); same `laya` model for triage + routing is intended (D17) but keep question sets separate.
