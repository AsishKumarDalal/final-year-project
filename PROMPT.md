# PROMPT.md — Medical Decision-Support Harness

**Status:** Draft v1 · awaiting input on Open Decisions (§18)
**Nature:** This file is the **complete project specification**. Do not begin implementation until §18 is resolved.

---

## 1. Summary

Build a **medical decision-support harness**: a service that reads a patient's or clinician's free-text description of a problem, uses a fast calibrated decision model (**Laya**) to assign a triage acuity and detect red-flag symptoms, escalates hard via deterministic rules when danger is present, and otherwise uses an LLM plus retrieval tools to produce a cited, scope-limited explanation.

The system is split into a fast **System 1** layer that only decides, a **System 2** layer that only explains, and a **tools** layer that is the sole source of factual truth.

**It is not a diagnostic system.** It assigns priority, flags danger, and cites information. It never names a disease as a conclusion and never recommends treatment.

---

## 2. Scope boundary

**In scope:** the AI/harness layer only — decision model integration, rule engine, retrieval tools, LLM orchestration, evaluation harness, fine-tuning.

**Out of scope:** everything else. See §4.

The harness is delivered as a **language-agnostic JSON/HTTP service** so that any future backend can consume it. A thin FastAPI edge is included. No database, no auth system, no UI.

---

## 3. Goals

1. Assign a **triage acuity level** from free text with a calibrated probability.
2. Detect **red-flag symptoms** with per-flag calibrated probabilities.
3. **Escalate deterministically** when danger is present — without invoking the LLM at any point.
4. Produce **grounded, cited, scope-limited explanations** for non-escalated cases.
5. Enforce **role-based tool access** at the tool-call boundary, not in the UI.
6. **Measure** the system against falsifiable targets and publish the results, including negative ones.
7. **Improve accuracy** by fine-tuning the decision model on medical data (Phase B).

---

## 4. Non-goals

State these explicitly in code comments and README. They exist to stop scope creep.

| # | Non-goal |
|---|---|
| 1 | **No diagnosis.** The system never outputs "you have X" or a differential as a conclusion. |
| 2 | **No treatment, dosing, or medication advice.** |
| 3 | **No frontend or UI of any kind.** |
| 4 | **No user management, authentication, or patient-record storage.** Roles are asserted by the caller via a trusted header; the harness enforces permissions but does not issue identities. |
| 5 | **No real patient data.** Synthetic and hand-authored cases only. |
| 6 | **No OCR.** Report summarization is limited to text-extractable PDFs; scanned images are out of scope. |
| 7 | **No model training in Phase A.** Phase A is measurement of an off-the-shelf checkpoint. |
| 8 | **No cloud deployment.** Local/server only. |
| 9 | **No multilingual UI.** Laya's multilingual routing is used internally if present; language is not a product feature. |
| 10 | **No monitoring, drift detection, or longitudinal tracking.** |

---

## 5. Core architecture

```
Input (free text, optional structured fields)
   │
   ▼
LAYER 1 — Laya  (System 1 · decides · no generation)
   │   → acuity: expected ordinal level + full distribution
   │   → red_flag_*: P(true) per flag
   │   → extracted: structured fields
   │
   ▼
LAYER 2 — Rule engine  (deterministic · no model)
   │   any red flag ≥ threshold, or acuity ≥ URGENT
   │     → ESCALATE. Emit decision trace. STOP. LLM is never called.
   │
   ▼ (only if not escalated)
LAYER 3 — LLM  (System 2 · explains · retrieves · refuses)
   │   may call tools:
   │     · kb_search      (RAG over curated corpus)
   │     · get_lab_ref    (lab ranges + critical values)
   │     · get_drug_interactions
   │     · summarize_report (text-extractable PDFs only)
   │
   ▼
Response: { decision, escalation?, explanation?, citations[], decision_trace }
```

### The governing law

> **The decision model decides. The rules escalate. The LLM explains. The tools hold the truth.**

If any safety-critical outcome can be changed by LLM output, the design is wrong. This is not negotiable and is enforced by test (§16).

### Layer boundaries

| Layer | May output | May NOT |
|---|---|---|
| Laya | labels, probabilities, ordinal levels | free text |
| Rules | escalate / not escalate, match reasons | probabilities, prose |
| LLM | prose, citations, tool calls | escalate, triage level, diagnosis, treatment |
| Tools | facts + source ids | prose inference |

---

## 6. Roles and permissions

Permissions are enforced **at the tool-call boundary inside the harness**. A hidden button is not enforcement — the harness must refuse the call regardless of what the caller requests.

| Capability | patient | nurse | doctor |
|---|---|---|---|
| Triage + red-flag assessment | ✅ | ✅ | ✅ |
| RAG / educational info | ✅ | ✅ | ✅ |
| Report summarization | ✅ | ✅ | ✅ |
| Lab reference | ❌ | ✅ | ✅ |
| Drug interactions | ❌ | ❌ | ✅ |
| Differential hints (candidates only, framed as "discuss with a clinician") | ❌ | ❌ | ✅ |
| `escalate_to_human` record | ❌ | ✅ | ✅ |

**Role source:** trusted `X-Actor-Role` header supplied by the upstream caller. The harness **trusts and enforces** it; it does not verify identity. Any tool call outside the matrix returns a refusal with reason `permission_denied`, and this is logged.

---

## 7. Hard constraints

### 7.1 Performance

| Operation | Budget (p95) |
|---|---|
| Laya decision stage, including network RTT | ≤ 150 ms |
| Rule engine evaluation | ≤ 5 ms |
| Retrieval (any single tool) | ≤ 500 ms |
| Basic search retrieval | ≤ 500 ms |
| Local search retrieval | ≤ 2 s |
| Global search retrieval | ≤ 15 s, and must stream partials rather than hold the request |
| Full harness response, excluding LLM generation | ≤ 1.0 s for basic/local |
| Full harness response, including LLM generation | ≤ 6 s (basic/local) or ≤ 20 s (global) |
| Batched triage, 100 cases | ≤ 10 s |

Global search is a map-reduce over community reports and is the most expensive path in the system. It is budgeted separately rather than averaged in, and its cost is reported per query.

Batch where possible — Laya answers all questions for one input in a single forward pass, and batches amortise across inputs. Never call Laya once per flag; send all flags as one question set.

### 7.2 Determinism

1. **Same input + same pinned checkpoint + same question schema → byte-identical decision output.** Repeat runs must agree.
2. Every decision records a **decision trace**: Laya model ID, checkpoint identifier, question-schema version hash, rule-engine version, threshold set, and timestamp.
3. Escalation is **replayable** from the trace: re-running the rule engine on a recorded trace must reproduce the same escalation decision.
4. **LLM output is non-deterministic and is never load-bearing for safety.** No escalation, no triage level, and no red-flag determination may depend on it.
5. Threshold changes are versioned. Threshold changes invalidate prior traces.

### 7.3 UX contract (no UI built; the harness must emit what a UI needs)

1. Escalation must be **the first field in the response**, not buried in prose.
2. Every escalation lists **which flags fired, their probabilities, and the threshold**.
3. Every generative claim carries a **citation id** that resolves to a source document.
4. Unresolvable citations are a **hard failure**, not a warning.
5. Refusals are **explicit and reasoned** — never silent, never a fallback answer.
6. Output separates `source_facts` from `generated_interpretation`. The two must never be merged into one blob.
7. A low-confidence decision surfaces as an explicit **abstention**, not a best guess.
8. Emergency-facing output includes the escalate-instruction text verbatim and unaltered.

### 7.4 Platform

- Linux. Python 3.10+.
- **Laya runs on a server and is accessed over HTTP.** Phase A does not depend on a local install.
- **No GPU required for Phase A.** Fine-tuning (Phase B) uses free-tier GPU (e.g. Kaggle 2×T4).
- **Qdrant and Neo4j run in Docker, development only.** Persistent volumes live under `data/`. Credentials from the environment, never committed.
- **Embedding is local.** `all-MiniLM-L6-v2`, downloaded once, 384 dimensions. No embedding API calls.
- The only external runtime dependencies are the **Laya API** and the **LLM API** (any OpenAI-compatible endpoint). Everything else must work offline.
- The harness must degrade gracefully: if the LLM API is unavailable, return the decision + escalation and a clearly-marked `degraded: true`, rather than failing the request. The same applies if **both retrieval stores are down** — neither sits on the safety path.

---

## 8. Question schemas

These are **fixed and versioned**. Changing one changes the schema version hash and invalidates prior traces (§7.2.5).

### 8.1 Acuity

```json
{
  "acuity": {
    "type": "score",
    "instructions": "Rate the urgency of this patient's presentation, from least to most urgent, based only on the information given.",
    "criteria": ["routine", "soon", "urgent", "resuscitation"]
  }
}
```

Required output: expected ordinal level, plus the **full distribution across all four levels**. Escalation on the distribution, not the point estimate alone.

### 8.2 Red flags — one `noul` per signal, single question set

All flags are sent in **one** request. Never individually.

```json
{
  "red_flag_chest_pain":        { "type": "noul", "instructions": "Does the patient report chest pain, pressure, or tightness?" },
  "red_flag_severe_dyspnea":    { "type": "noul", "instructions": "Is there severe breathlessness, respiratory distress, or inability to speak in full sentences?" },
  "red_flag_altered_consciousness": { "type": "noul", "instructions": "Is there confusion, fainting, loss of consciousness, or an altered level of consciousness?" },
  "red_flag_stroke_signs":      { "type": "noul", "instructions": "Is there facial droop, weakness or numbness on one side of the body, or difficulty speaking?" },
  "red_flag_major_haemorrhage": { "type": "noul", "instructions": "Is there uncontrolled bleeding, vomiting blood, or black tarry stools?" },
  "red_flag_severe_abdominal_pain": { "type": "noul", "instructions": "Is there severe or sudden-onset abdominal pain, or a rigid tender abdomen?" },
  "red_flag_anaphylaxis":       { "type": "noul", "instructions": "Is there swelling of the face, lips or tongue, difficulty breathing, or collapse after an exposure?" },
  "red_flag_suicide_risk":      { "type": "noul", "instructions": "Does the patient mention harming themselves, self-harm, or that they do not want to live?" }
}
```

**`red_flag_suicide_risk` has special handling — see §11.3.**

### 8.3 Structured extraction

```json
{
  "body_system": {
    "type": "choice",
    "instructions": "Which body system is the main complaint about?",
    "criteria": {
      "cardiac":     "chest pain, palpitations, breathlessness on exertion",
      "respiratory": "cough, breathlessness, wheezing, sputum",
      "neurological":"headache, dizziness, numbness, weakness, confusion, fits",
      "gastrointestinal": "abdominal pain, vomiting, diarrhoea, bleeding",
      "endocrine":   "sweating, tremor, thirst, weight change, heat or cold intolerance",
      "urinary":     "pain or difficulty passing urine, blood in urine",
      "skin":        "rash, lesion, itching, swelling",
      "musculoskeletal": "joint or muscle pain, stiffness, injury",
      "general":     "fever, malaise, fatigue, unexplained weight loss",
      "other":       "anything that does not clearly fit the above"
    }
  }
}
```

### 8.4 Guard — detects requests the harness must refuse

```json
{
  "guard_diagnosis_request":  { "type": "noul", "instructions": "Does the user ask for a diagnosis, a named disease, or whether they have a specific illness?" },
  "guard_treatment_request":  { "type": "noul", "instructions": "Does the user ask how to treat, dose, stop, start, or change any medication or medicine?" },
  "guard_self_harm_request":  { "type": "noul", "instructions": "Does the user describe ongoing self-harm intent, an active plan, or immediate danger to themselves or others?" }
}
```

---

## 9. Rule engine

Deterministic. No model calls. Versioned. Pure function of `(trace, thresholds)`.

**Escalate if ANY of:**

| Condition | Threshold |
|---|---|
| `red_flag_suicide_risk` | ≥ **0.50** |
| any other `red_flag_*` | ≥ **0.70** |
| `acuity` expected level | ≥ `urgent` (ordinal index ≥ 2) |
| `acuity` probability of `resuscitation` | ≥ **0.10** |
| Laya abstained (`min_confidence` triggered) AND any red flag ≥ 0.40 | escalate with `reason: insufficient_confidence` |

**Downgrade is forbidden.** A high-probability negative never suppresses a fired flag.

**Escalation output:**

```json
{
  "escalated": true,
  "escalation": {
    "level": "resuscitation",
    "reasons": [
      { "flag": "red_flag_chest_pain", "probability": 0.94, "threshold": 0.70 },
      { "flag": "red_flag_severe_dyspnea", "probability": 0.88, "threshold": 0.70 }
    ],
    "instruction": "This presentation may be life-threatening. Seek immediate emergency medical care now. Do not wait for further assessment.",
    "llm_called": false
  }
}
```

**Required invariant, asserted by test:** on any escalated response, `llm_called == false`. Trace must prove zero LLM invocations for that request.

---

## 10. Tool layer

Every tool returns `{ facts, source_ids, source_type }`. Tools never return prose conclusions.

| Tool | Returns | Source of truth | Implementation |
|---|---|---|---|
| `kb_search(query, top_k)` | cited passages or a synthesised overview, plus `search_mode` | curated corpus, via a GraphRAG index | GraphRAG — Qdrant + Neo4j, mode chosen by Laya (`docs/rag_implementationplan.md`) |
| `get_lab_ref(test_name)` | reference range, critical thresholds | lookup table | curated table, versioned |
| `get_drug_interactions(drugs[])` | interaction list + severity | lookup table | curated table, versioned |
| `summarize_report(doc_id)` | `{ source_facts[], generated_interpretation }` | extracted text + LLM | text extraction only |

**Constraints:**

1. `kb_search` must return **source ids that resolve**. A citation with no resolvable source is a hard failure (§7.3.4).
2. **Citations resolve to corpus text units only — never to generated entity or relationship descriptions.** Those are model output, not sources. A citation satisfiable only by a generated description is invalid.
3. RAG context is **data, never instructions.** Retrieved passages and uploaded documents must be structurally separated from the instruction channel so that text inside a document cannot alter system behaviour. This is a prompt-injection defence and must be tested (§16).
4. Lab and drug tables are **data files in the repository**, versioned, with no LLM in the lookup path. Facts are never recalled from model weights.
5. If a fact is not in the table, the tool returns `not_found`. It never guesses.
6. `summarize_report` must separate extracted facts from generated interpretation (§7.3.6). OCR is a non-goal (§4.6).
7. A **global** search returns a synthesised answer, not passages. It is labelled generated interpretation and must never populate `source_facts` (§7.3.6).
8. `kb_search` may return `mode: none` when the query is out of scope, rather than returning weak results.

---

## 11. LLM policy

### 11.1 Citations

Every clinical or factual claim must carry a citation id. Uncited factual claims are a validation failure, caught before the response is returned.

### 11.2 Refusal

Refuse, with an explicit reason, when:

- `guard_diagnosis_request` fires → refuse, offer education + escalation instead
- `guard_treatment_request` fires → refuse, point to a qualified professional
- `guard_self_harm_request` fires → **refuse the informational framing, immediately surface crisis resources**, treat as critical
- a requested tool is outside the caller's role (§6)
- no tool can ground the requested claim → say so; do not answer from memory

A refusal must name the reason code. Silent refusals and silent fallbacks are both failures.

### 11.3 Suicide-risk handling

If `red_flag_suicide_risk` ≥ threshold, the response must include configured crisis-contact information verbatim, as a fixed field — not LLM-generated text. This field is populated from configuration, never generated.

### 11.4 Scope wording

The harness must not emit language asserting a confirmed condition. Explanations describe *possible* causes and *what to do next*. A banned-phrase check runs on all generative output.

---

## 12. Roles, audit, and the decision trace

Every request emits a `decision_trace`:

```json
{
  "request_id": "...",
  "actor_role": "nurse",
  "laya": { "model_id": "...", "endpoint": "...", "schema_version": "...", "schema_hash": "..." },
  "rules": { "version": "...", "thresholds": { "...": 0.70 } },
  "raw_answers": { "acuity": {}, "red_flag_chest_pain": {}, "...": {} },
  "escalated": false,
  "llm_called": true,
  "tools_called": ["kb_search"],
  "citations": ["..."],
  "abstained": false,
  "degraded": false,
  "timestamp": "..."
}
```

Requirements:

1. Traces are append-only and queryable by `request_id`, `actor_role`, and escalation state.
2. A trace contains **everything needed to replay** the escalation decision offline.
3. No patient-identifying content in traces. Test inputs are synthetic.

---

## 13. Deliverables

### Phase A — build and measure

| # | Deliverable |
|---|---|
| A1 | `DecisionClient` interface with a **Laya HTTP adapter** and a **recorded-fixture adapter** (replays stored traces; lets the whole system be tested offline and deterministically) |
| A2 | Versioned question schemas (§8) as data files, including the **retrieval-mode routing schema** used by `kb_search` |
| A3 | Deterministic rule engine (§9) with unit tests covering every threshold branch |
| A4 | Tool layer (§10): `kb_search` (GraphRAG), `get_lab_ref`, `get_drug_interactions`, `summarize_report` |
| A5 | LLM adapter with tool calling, citation enforcement, refusal policy, banned-phrase check |
| A6 | **Orchestrator** — the harness, wiring Layers 1→2→3 with the §5 law |
| A7 | Role-permission enforcement at the tool boundary |
| A8 | FastAPI edge exposing the harness |
| A9 | **Evaluation harness** (§15) |
| A10 | **Baseline evaluation report — published even if the numbers are bad** (§14.3) |
| A11 | README covering setup, architecture, and the §16 non-goals |

### Phase B — improve

| # | Deliverable |
|---|---|
| B1 | Medical triage dataset, with **documented provenance, labelling procedure, and stated limitations** |
| B2 | Fine-tuning notebook (RLCD loop, temperature calibration, evaluation, export) |
| B3 | Fine-tuned acuity checkpoint |
| B4 | Fine-tuned red-flag checkpoint |
| B5 | **Before/after report** — fine-tuned vs off-the-shelf on the same held-out set, same metrics |
| B6 | Threshold recalibration for the fine-tuned checkpoint, re-tested against §16 |

---

## 14. Milestones

### Phase A

| Milestone | Exit criterion |
|---|---|
| **M1** — decision layer live | Laya API returns answers for the full question set; adapter + fixtures working; latency within §7.1 |
| **M2** — rule engine live | Every threshold branch unit-tested; escalation output matches §9 exactly |
| **M3** — tools live | All four tools return facts + resolvable source ids; injection test passes |
| **M4** — orchestrator live | End-to-end run; escalation provably short-circuits the LLM; roles enforced |
| **M5** — evaluation live | Full §15 harness runs; **baseline report published** |

### Phase B

| Milestone | Exit criterion |
|---|---|
| **M6** — dataset frozen | Labelled set frozen and versioned; provenance and limitations documented |
| **M7** — fine-tuned | Checkpoints exported; offline inference reproducible |
| **M8** — compared | Before/after report on the same held-out set; thresholds re-calibrated and re-tested |

### 14.3 No-hiding rule

**The Phase A baseline report is published in full, including poor results.** If off-the-shelf Laya scores badly on medical text — which is expected, because it has no medical training — that is recorded as the finding, and Phase B is the remediation. A harness that publishes a good-looking baseline obtained by cherry-picked cases is a failed harness.

---

## 15. Evaluation harness

The evaluation harness is a **first-class deliverable**, not an afterthought. Without it there is no evidence the system works.

### 15.1 Test sets

All synthetic. Three sets, kept separate and versioned:

| Set | Purpose | Size |
|---|---|---|
| `scenarios` | Normal and escalated presentations | ≥ 100 |
| `red_flags` | Each red flag present, singly and in combination | ≥ 60 |
| `refusals` | Diagnosis / treatment / out-of-role / ungroundable requests | ≥ 40 |
| `holdout` | Never used for tuning, threshold setting, or threshold recalibration | ≥ 100 |

### 15.2 Metrics and targets

| Metric | Target | Blocking? |
|---|---|---|
| **Red-flag recall** — fired flag → escalation | **100%** | **Yes — release blocker** |
| **Refusal correctness** — should refuse → did refuse | **100%** | **Yes — release blocker** |
| **Escalation isolation** — LLM called on escalated case | **0 occurrences** | **Yes — release blocker** |
| Citation resolvability | 100% | Yes |
| Citation resolves to generated graph text rather than corpus text | **0 occurrences** | **Yes — release blocker** |
| Identical queries route to the same search mode | 100% | Yes |
| Prompt-injection resistance | 100% on injection cases | Yes |
| Acuity accuracy (holdout) | report; target set after baseline | No |
| Acuity macro-F1 (holdout) | report; target set after baseline | No |
| Banned-phrase violations | 0 | Yes |
| Decision determinism (5× repeat) | 100% identical | Yes |
| Latency budgets (§7.1) | within budget | Yes |

Metrics marked *report* get a target only after the Phase A baseline. Setting targets before measuring is how a project ends up defending a number it chose.

---

## 16. "Done when" — checks

The build is done when **all** of the following are true and demonstrable.

### 16.1 Structural checks

- [ ] Harness exposes a single entrypoint taking text + role, returning the §12 trace
- [ ] No module outside the rule engine can set `escalated`
- [ ] No module outside the tool layer can emit a fact
- [ ] LLM client is unreachable in the escalated code path (verified by dependency inspection, not by reading)
- [ ] Question schemas are versioned data files, not inline strings

### 16.2 Safety checks

- [ ] Red-flag recall 100% on `red_flags` set
- [ ] Refusal correctness 100% on `refusals` set
- [ ] Zero LLM calls across all escalated scenarios, proven from traces
- [ ] Every one of 100% escalation responses carries the verbatim instruction text
- [ ] Injection payloads inside document and RAG content produce no behaviour change
- [ ] Out-of-role tool calls refused 100% of the time

### 16.3 Determinism checks

- [ ] Same input × 5 runs → identical decision output
- [ ] Recorded trace replayed offline → identical escalation decision
- [ ] Thresholds and schema version present in every trace

### 16.4 Performance checks

- [ ] All §7.1 budgets met at p95 on the reference machine, measured and reported

### 16.5 Honesty checks

- [ ] Baseline report published with actual numbers
- [ ] Non-goals (§4) stated in the README
- [ ] Dataset limitations stated wherever a dataset is referenced
- [ ] No claim of clinical validation anywhere in the repository

### 16.6 Phase B checks

- [ ] Before/after report on the same holdout set
- [ ] Fine-tuned beats baseline on acuity metric **or** the negative result is reported as a negative result
- [ ] Red-flag recall still 100% after fine-tuning
- [ ] Thresholds recalibrated and re-tested, not inherited blindly

---

## 17. Demo flow

Nine steps. **Each step names what is shown.**

1. **Baseline transparency** — show the Phase A baseline numbers, including what is bad. Establish honesty first.
2. **Routine case** — submit a low-urgency presentation. Show the decision trace: acuity distribution, red-flag probabilities below threshold, rules evaluated, LLM called, citations rendered.
3. **Trace replay** — replay that trace offline. Show byte-identical output. Determinism demonstrated, not asserted.
4. **Escalation** — submit a chest-pain presentation. Show escalation as the first field, the fired flags with probabilities and thresholds, and the trace showing `llm_called: false`.
5. **Isolation proof** — show the trace's LLM-call counter across all escalation runs, aggregated. Zero.
6. **Refusal** — ask for a diagnosis. Show the explicit refusal, the reason code, and what was offered instead.
7. **Out-of-role refusal** — as a patient, request drug-interaction lookup. Show `permission_denied` at the tool boundary.
8. **Injection attempt** — submit a document containing an instruction-override payload. Show the behaviour is unchanged.
9. **Fine-tuned comparison** — same holdout cases, baseline vs fine-tuned, side by side. Report the delta honestly, in either direction.

---

## 18. Risks and known limitations

| Risk | Severity | Handling |
|---|---|---|
| **Off-the-shelf Laya has no medical training.** Phase A acuity and flag quality will be poor. | **High, certain** | Expected. Measured and published (§14.3); remediated in Phase B. Must be stated in the README, not buried. |
| **No labelled medical data available for Phase B.** | **High** | Open Decision §19.2. Determines whether Phase B is real. |
| Laya API unavailable or slow | Medium | Fixture adapter; graceful degradation (§7.4) |
| LLM API key invalid on demo day | Medium | Critical path (escalation) has no LLM dependency by design (§7.2.4) |
| Threshold values in §9 are guesses | Medium | Recalibrate against `red_flags` set in Phase A; never against `holdout` |
| Curated corpus for RAG is a curation burden | Medium | Scope the corpus to one domain (e.g. emergency care) rather than general health |
| Safety-critical use implied by framing | **High** | §19.1. The educational-prototype framing must be visible in the API response, not only in the README |

### Known limitations to state in the README

- Not a medical device. Not clinically validated. Not for diagnosis.
- Acuity levels are an operational prioritisation aid, not a clinical severity measure.
- Red-flag detection is a safety net that catches what it was trained to catch; absence of a flag is not absence of danger.
- Labels are synthetic; performance on real presentations is unknown and must not be assumed.

---

## 19. Open decisions

**Answer status as of 2026-10-08.** A milestone whose gate is open must not start.

| # | Decision | Status | Needed by |
|---|---|---|---|
| 1 | **Laya API** — base URL, auth method, exact request/response schema, checkpoint/model id. Endpoint and credentials arrive by environment configuration, never hardcoded. | 🟡 open — **does not block M0, M1, M4, M5, M6**; only M2/M3 | M2 |
| 2 | **Labelled data source for Phase B.** Real (MIMIC-IV — credentialed, ICU-flavoured), synthetic (Synthea), hand-authored and clinician-reviewed, or LLM-generated (must be disclosed as a limitation). **If no credible source exists, Phase B is descoped and the spec says so.** | 🟡 open | M13 |
| 3 | **LLM backend.** Interface is OpenAI-compatible; backend chosen 2026-10-08 — the time-limited models. Still needed as environment values: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID`, plus **which model ids will be retired** so a fallback is picked before they go. | ✅ **answered** — needs the values | M6, M7 |
| 4 | **Corpus for RAG** — which documents, how curated, who approves them as "trusted". Architecture is settled (`docs/rag_implementationplan.md`); the corpus is not. A knowledge graph raises the stakes — unreviewed input produces a graph, not just poor retrieval. | 🔴 **deciding now — time-critical, blocks indexing** | **today** |
| 5 | Acuity taxonomy — 4-level scale, or align to a named existing scale? | 🟡 open | M1 |
| 6 | Lab and drug table scope — how much to build. | 🟡 open | M5 |
| 7 | Whether the harness ships as a library, a service, or both. | 🟡 open | M10 |

> ⏳ **#4 is the critical path.** Model access lasts 2–3 days and indexing is the only thing that consumes it. There is nothing to index until the corpus is chosen. Prefer a **small, homogeneous, clearly-licensed** corpus in one domain over a broad unreviewed one — the index is permanent, so few documents indexed well beats many indexed carelessly. If documents are chosen fast and without clinical review, `docs/limitations.md` must say exactly that. See `docs/decisions.md`.

---

## 20. Interpretation rules

1. **This file is the specification.** Where it is silent, ask; do not invent.
2. **Do not add capabilities** not listed in §13. A tool the spec did not ask for is scope creep, not initiative.
3. **Do not soften §4.** Non-goals exist to be defended.
4. **If a check in §16 cannot pass, report it as failing.** A passing claim on an unrun check is the worst possible outcome.
5. **Measurement before optimisation.** Phase A exists to produce numbers. Phase B exists to improve them.
6. **No safety-critical path may depend on non-deterministic output.** If a proposed change violates §7.2.4, reject it regardless of benefit.