# Implement.md — Execution Runbook

**This file tells you how to operate. It does not define the work.**

| File | Owns | Change it when |
|---|---|---|
| `PROMPT.md` | **What** — goals, architecture law, constraints, deliverables, done-when | Only via the amendment procedure (`Plan.md` §6) |
| `Plan.md` | **When and how verified** — milestones, commands, architecture contracts, decision notes | Only via the amendment procedure (`Plan.md` §6) |
| `Implement.md` | **How you operate** — this file | Never, to make a check pass |
| `docs/` | **What happened** — decisions, runbook, limitations | Continuously, in the same commit as the code |

**Precedence on conflict:** `PROMPT.md` > `Plan.md` > `Implement.md` > `docs/`.
If two disagree, the higher one wins and the lower one gets fixed — never the reverse.

---

## 1. Before you start anything

1. Read `PROMPT.md` in full. Then `Plan.md` in full. Then this file.
2. Check `PROMPT.md` §19. Four decisions gate all work:
   - §19.1 Laya API contract
   - §19.2 Phase B label source
   - §19.3 LLM provider and key
   - §19.4 RAG corpus
3. Identify the first milestone not yet complete in `Plan.md` §2.
4. If a gating decision (§19.1–19.4) is unanswered for the milestone you are starting, **stop and ask.** Do not substitute a mock and continue.
5. Verify the toolchain: `make install` then `make validate`. A red baseline before any work means an environment problem, fix that first.

---

## 2. The milestone loop

Repeat this. Do not deviate.

```
 ┌─ 1. READ     Plan.md milestone. List every Build item.
 ├─ 2. BUILD    Implement exactly those items. Nothing else.
 ├─ 3. VALIDATE Run the milestone's validation command.
 ├─ 4. FIX      Failing? Fix the cause. Re-run narrow, then full.
 ├─ 5. DOC      Update docs/ for what you built. Same commit as code.
 ├─ 6. REPORT   Emit the completion report (§8).
 └─ 7. COMMIT   Only when validation is green and docs are updated.
```

### 2.1 Rules for step 2 — building

- Implement the Build list **completely**. A half-built milestone is worse than an unbuilt one, because the next milestone assumes it exists.
- Follow `Plan.md` §4 for file placement. If a file is not covered by the layout, you are inventing scope — see §4.
- Follow `Plan.md` §4.2 dependency contracts. `make arch` will catch violations, but do not wait for it.
- Use the decision notes in `Plan.md` §7. They are settled.

### 2.2 Rules for step 3 — validating

Run **exactly** the command listed in the milestone. If a milestone lists no command, that is a defect in `Plan.md` — report it rather than inventing one.

### 2.3 Rules for step 4 — fixing

Full procedure in `Plan.md` §5. In short:

> Fix the **code**. Never the test, never the threshold, never the target.

If the fix requires changing something outside the milestone's Build list, that is a scope violation — report it and ask.

---

## 3. Keeping diffs scoped

**Scope discipline is the single most important behaviour in this project.** An agent that adds unrequested capability produces something that looks finished and is wrong — which is the exact failure this whole specification exists to prevent.

### 3.1 Hard scope rules

| # | Rule |
|---|---|
| S1 | Build **only** the milestone's Build list. |
| S2 | **No new dependencies.** Ever, without explicit approval — record the request in `docs/decisions.md` and stop. |
| S3 | No new tools, endpoints, data files, config keys, or abstractions beyond the milestone list. |
| S4 | Do not refactor working code outside the milestone. |
| S5 | Do not fix unrelated bugs you notice. Log them (§3.3). |
| S6 | Do not add tests for code outside the milestone. |
| S7 | Do not rename or reformat code outside the milestone. |
| S8 | If the milestone is ambiguous, **stop and ask.** Do not pick a reading and proceed. |
| S9 | If you believe a substantially better approach exists, implement the **specified** approach. Record the alternative in `docs/decisions.md` for human review. |
| S10 | One milestone = one coherent change set. Do not start the next. |

### 3.2 Named temptations

These are the specific failure modes. When you feel one, log it instead of acting.

| Temptation | Correct action |
|---|---|
| "While I'm in this file, let me also tidy…" | §4. Revert it. |
| "This module could use a config layer." | §4. Log in `docs/decisions.md`. |
| "Let me add error handling for cases out of scope." | §4. Log it. |
| "This abstraction would be cleaner if…" | §4. Log it. |
| "There's a bug in another module." | §3.3. Log it, don't touch it. |
| "Let me add a CLI while I'm here." | §4. Not requested. |
| "The spec is silent, so I'll add something sensible." | §4. S8 — stop and ask. |
| "This test is failing for an unrelated reason." | §5.2 in `Plan.md`. Fix the cause or report it. Never skip. |

**The rule of thumb:** if it is not on the Build list, it is a note, not a change.

### 3.3 Deferred work log

Maintain `docs/TODO.md`. One line per item:

```
- [ ] M-M17  cache lookups  — found while doing M4  — not fixed, out of scope
```

Never fix these inline. Never delete them silently. They carry forward.

---

## 4. Validation

### 4.1 After every milestone

Run the milestone's command. Then run `make validate` before committing. A milestone whose own command passes but whose full gate fails is **not complete**.

### 4.2 Fix failures immediately

Do not stack work behind a red gate. Do not start the next milestone. Do not batch up three broken things and fix them together — you will misattribute the cause.

### 4.3 Never bypass

The forbidden repairs are enumerated in `Plan.md` §5.2. They are reproduced in §9 of this file because they are the ones most likely to occur under time pressure.

### 4.4 A failing blocking metric is a result, not a defect

If `make eval` exits nonzero because red-flag recall is 92% instead of 100%:

- That is the gate **working correctly.**
- Record it in `out/baseline_report.md`.
- **Do not** lower the target in `PROMPT.md` §15.2.
- **Do not** change thresholds until they have been tuned on the `red_flags` set, and never against `holdout` (`Plan.md` D15).
- Fix it in Phase B, where the before/after report shows the movement.

The only legitimate reason to change a target is that the target was **wrong** — and that goes through the amendment procedure (`Plan.md` §6), not through a quiet edit.

---

## 5. Documentation — continuous, not deferred

### 5.1 The rule

> **A milestone is not complete until its documentation is updated in the same commit as the code.**

Deferred documentation is the same failure as a skipped test, wearing a different hat: the work looks finished while the knowledge behind it does not exist.

### 5.2 The doc set

| File | Created | Updated | Contains |
|---|---|---|---|
| `README.md` | M0 | Every milestone that changes setup, usage, or scope | What this is, setup, how to run, **non-goals** |
| `docs/architecture.md` | M0 | When a layer, contract, or data flow changes | The `Plan.md` §4 contracts, the three-layer diagram |
| `docs/decisions.md` | M0 | **Every** judgement call | Decision, rationale, alternatives rejected, date |
| `docs/runbook.md` | M0 | When a command or requirement changes | How to install, test, evaluate, run the service |
| `docs/tools.md` | M5 | M5, M6, M7 | Each tool: signature, returns, data source, provenance |
| `docs/evaluation.md` | M11 | M11, M15 | Metric definitions, how to run, targets, why each exists |
| `docs/limitations.md` | M12 | M12, M15 | What the system cannot do |
| `docs/TODO.md` | M0 | Continuously | Deferred work (§3.3) |

### 5.3 Generated reports are not documentation

`out/*.md` files are **generated artifacts**. Never hand-edit them. They are evidence, not prose.

The distinction matters: an agent that hand-edits a report to look better has falsified its own evidence.

### 5.4 Doc content rules

1. **`README.md` may never overstate.** No claim of clinical validation, no "diagnoses," no accuracy figure that does not appear in `out/`.
2. **Every limitation gets written down.** If something is unvalidated, broken, or unknown, it goes in `docs/limitations.md`. Silent omission is a defect.
3. **Every judgement call is recorded** in `docs/decisions.md` — not just the ones in `Plan.md` §7. That section covers decisions made up front; this log covers decisions made during the build.
4. **No marketing tone.** Write to a reviewer who will check your work.
5. **Document the data.** Any table, corpus, or dataset gets provenance: where it came from, who made it, what it does not cover.

---

## 6. When blocked

Stop and report. Do not improvise a way past a blocker.

| Situation | Action |
|---|---|
| Gating decision unanswered (`PROMPT.md` §19) | Stop. Ask. Name exactly what is needed. |
| Laya API unreachable | Use fixtures for development; report the outage. Do not swap in a mock and report success. |
| LLM key invalid or absent | Report it. The safety path is designed to work without one (`PROMPT.md` §7.2.4) — demonstrate that, then ask for the key. |
| Milestone spec is ambiguous | Stop. Ask. Do not pick a reading. |
| Milestone cannot be completed as written | Report the finding, propose an amendment (`Plan.md` §6), wait. |
| You found a critical bug in completed work | Report it immediately. Fixing it mid-milestone is a scope violation; safety defects are the exception — see below. |

**Safety-defect exception:** if you find a defect that would let the system fail to escalate a red flag, stop everything, report it, and fix it as an emergency — regardless of which milestone is open. Record it in `docs/decisions.md` the same day.

---

## 7. Commit protocol

- One commit per milestone where the milestone is coherent; more if it is large. Never one commit spanning two milestones.
- Format: `M{n}: {what} — {validation result}`
  - `M4: deterministic rule engine — 100% branch coverage, make arch green`
- Never commit `.env`, keys, checkpoints, `out/` reports generated mid-debug, or `__pycache__`.
- `out/` reports **are** committed, but only at M12, M15, and when a finding needs recording.
- A commit whose message implies a passing check must have that check run **in that commit.** Never write a claim you have not verified.

---

## 8. Milestone completion report

Emit this after every milestone. It is short and it is mandatory.

```
## M{n} — {name}

Built
  - {item} → {path}
  - {item} → {path}

Validation
  {command}            → PASS | FAIL ({actual result})
  make validate        → PASS | FAIL ({actual result})

Acceptance criteria (Plan.md §2)
  [x] {criterion}
  [ ] {criterion — not met, why}

Numbers
  {actual measured values, or "not measured yet"}

Not done
  - {anything intentionally left, and why}

Blocked / needs you
  - {question, or "none"}
```

### 8.1 Reporting honesty rules

1. **Never claim a check passed without having run it in this session.** "Should pass" is not a result.
2. **Never round a failing metric into a passing one.** 92% is not "high 90s, close enough."
3. **Bad numbers are reported as bad numbers.** `PROMPT.md` §14.3 is a written rule precisely because the tempting move is to leave them out.
4. **If something is unfinished, say unfinished.** Do not report a milestone complete because the code runs.
5. If you skipped a criterion, list it in **Not done** — do not omit it silently.

---

## 9. Never do

Absolute prohibitions. No milestone, deadline, or request overrides these.

1. **Never** weaken, skip, or delete a test or assertion to make a gate pass.
2. **Never** tune a safety threshold to hit a target number.
3. **Never** evaluate or tune against `holdout` (`Plan.md` D15).
4. **Never** edit an eval case so the system passes it.
5. **Never** hand-edit a generated report in `out/`.
6. **Never** claim clinical validation, diagnostic capability, or accuracy not evidenced in `out/`.
7. **Never** add a dependency without explicit approval.
8. **Never** put real patient data anywhere.
9. **Never** let the LLM influence escalation, triage level, or red-flag determination.
10. **Never** skip the escalation-isolation test because "the code obviously does the right thing."
11. **Never** commit a secret.
12. **Never** modify `PROMPT.md` or `Plan.md` to make a check pass. Amendment is a documented procedure (`Plan.md` §6), not an edit.
13. **Never** report a milestone complete with unmet acceptance criteria.
14. **Never** expand scope because something seemed better.

---

## 10. Pre-commit self-check

Before every commit:

```
[ ] Only the milestone's Build items are in the diff
[ ] No new dependency
[ ] No file outside Plan.md §4.1 layout
[ ] Milestone validation command passed — in this session
[ ] make validate passed — in this session
[ ] Documentation updated for what changed
[ ] docs/decisions.md has an entry if I made a judgement call
[ ] No secrets, no real patient data, no hand-edited out/ report
[ ] Any found-but-unfixed issue is in docs/TODO.md
[ ] Completion report emitted, with bad news included
[ ] Commit message reflects what I actually verified
```

If any box is unchecked, do not commit.

---

## 11. First session

If you are starting fresh with no prior work:

```bash
make install
make validate          # expect green with zero tests
```

Then open `Plan.md` §2 and start **M0**. Read `PROMPT.md` §4 (non-goals) before you write any code — knowing what you must **not** build prevents more wasted work than any other single document.