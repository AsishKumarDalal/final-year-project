# Decision Log

**Append-only.** Newest at the top of each section. Never rewrite an entry — supersede it.

For amendment procedure see `Plan.md` §6. For settled design decisions made up front, see `Plan.md` §7 (`decision notes`).

---

## 2026-10-09 — Human-readable goals added to `Plan.md` and `Implement.md`

**Change:** two non-normative orientation sections. `Plan.md` gains **"Goals in plain language (for
humans)"** before §0; `Implement.md` gains **"§0 What we are building, in plain language"**. No existing
section was edited, removed, renumbered, or reordered.

**Cause (the finding):** the specification is written for implementers. A reviewer, supervisor, or new
team member cannot answer "what is this actually for, and why is it built in this order?" without reading
a 565-line specification that opens with performance budgets. That is a real audience gap, and it was
raised explicitly — the earlier explanation of these goals was clear to coding agents and useless to
humans. Orientation content was missing, not wrong.

**Why it went in these two files:** `README.md` does not exist yet (it is an M0 deliverable), so there was
nowhere human-facing in the repository. Both files were marked 🔒 in `AGENTS.md`, so this went through the
`Plan.md` §6 amendment procedure rather than a quiet edit.

**What it contains:** the product in one sentence; why indexing is built first (the perishable model
window); the seven stages in plain language with what each one produces; the three-layer data flow; the
non-goals; the scorecard in plain terms; what is already known to be hard; and the honest bottom line.

**What it deliberately does NOT do:**

- No normative content. Both sections state that `PROMPT.md` is the specification and `Plan.md` §2 is the
  binding milestone list, and that §2 wins on any conflict.
- No new requirement, threshold, metric, milestone, or acceptance criterion.
- No claim of clinical validation, no accuracy figure, and no statement that the system diagnoses
  anything. The §4 non-goals and the §18 limitations are reproduced in plain language rather than softened.
- No renumbering, so existing cross-references (§4.2, §5, §6, §7) remain valid.

**Validation:** none run, and none is possible — `make validate` does not exist until M0 builds the
Makefile. Nothing in this change is executable, and no code, test, threshold, or data file was touched, so
there is nothing for a gate to break. Recorded here rather than claimed as a passing check.

**Supersedes for humans:** `README.md` (M0) becomes the canonical public entry point and will carry the same
plain-language framing, without overstating anything.

---

## 2026-10-09 — System 1 (Laya) hosted locally via `laya-serve`; the hosted API is rejected on evidence

**Change:** §19.1 is answered in practice. Laya runs as a **local HTTP service** on
`http://127.0.0.1:8000`, served by the official `laya-serve` (`pip install "laya[serve]"`), exposing
`POST /v1/systemone`. It is no longer called as a third-party API.

**Cause:** the hosted System-1 endpoint was measured and rejected; the user then directed local hosting
so the checkpoint is downloaded once and loaded once.

**Measured evidence (not assumption):**

| | hosted `d1:free` | local `laya` |
|---|---|---|
| latency | 384–526 ms (median 432) | 2183–2627 ms (median 2301) |
| identical calls byte-identical | **no** (0.99478 vs 0.99509) | **yes**, 6/6 runs |
| rate limiting | HTTP 429 on the 3rd rapid call | none observed |
| §7.1 budget ≤150 ms | exceeded | exceeded, ~15× |

Determinism is the decisive column: `PROMPT.md` §7.2.1 and §16.3 require byte-identical repeat runs,
which the remote endpoint does not provide and the local one does.

**Dead end — do not retry.** Ollama's `laya` package is an **MLX build and Apple-Silicon only**:
`ollama pull laya` fails with *"this model requires MLX support"*. Also `ollama.com/download/ollama-linux-amd64.tgz`
is a 404; the archive is now `.tar.zst` (1.44 GB, needs `tar --zstd`). Artifacts removed, 3.5 GB reclaimed.

**New dependencies — explicitly approved by the user on 2026-10-09:** `torch 2.14.1+cpu` (CPU-only
index; this host has no GPU, per §7.4 "no GPU required") and `laya[serve]`. Both live in
`~/venvs/laya-server`, outside the repository and outside the harness test process, preserving D2's
rationale (the 421M checkpoint never enters a test process).

**What is NOT changed.** §7.4 "Laya runs on a server and is accessed over HTTP" still holds — we run that
server ourselves. Tests still read recorded fixtures only (D3). `make validate` never touches this
endpoint. Live checks stay a separate manual target (§4.4).

**Server configuration and why:** `LAYA_MODELS=english LAYA_MAX_LOADED=1` keeps exactly one 421M
checkpoint resident (2.0 GB RSS). The default `MAX_LOADED=2` holds english + multilingual ≈ 3 GB and
would OOM on a 7.8 GB host with no swap. `LAYA_HOST=127.0.0.1` overrides the 0.0.0.0 default.

**Measurements to carry forward (findings, not defects):**

1. **Latency: median 2301 ms against a ≤150 ms budget.** Reported plainly at M12. §7.1 is **not**
   relaxed to make this green. Contributing factor is 2 CPU cores with no GPU.
2. **`choice` questions with ≥11 options return uncalibrated confidence** — the `choice:11+` temperature
   in `rl_agent_config.json` is 0.1006, outside the valid [0.5, 5] range, so the runtime clamps it to
   0.5 and warns. `PROMPT.md` §8.3 has exactly 10 body systems, so it lands in the safe `choice:6-10`
   bucket. Adding an 11th option silently changes calibration.
3. **`noul` can follow its option labels instead of the state** (upstream issue #156): the English
   checkpoint returns a confident *wrong "no"* on positive input. All eight red flags in §8.2 are
   `noul`, and §15.2 requires 100% red-flag recall as a release blocker. This must be measured early,
   not discovered at M11. Documented workaround: ask the same question as a two-option `choice` with
   neutral keys.
4. **`act.act_probability` carries no usable signal** (upstream issue #185, reads ~1.0, AUROC 0.30).
   Gate on `confidence` instead.
5. **512-token context, ~320 tokens for state** (English checkpoint). Longer states truncate; the
   response reports `truncated` and `truncated_questions`. Relevant later to `summarize_report`.

**Open question raised for M4 — deliberately not decided here.** §9 escalates when "acuity expected
level ≥ urgent (ordinal index ≥ 2)". Laya returns a *continuous* expected score — 1.79 with
P(urgent) = 0.79 on the test presentation — so floor, round, and argmax each give a different answer
about whether that branch fires. Settled at M4 with a test on both sides of the boundary, not guessed now.

---

## 2026-10-08 — Milestone reorder: index before the safety core

**Change:** `Plan.md` §2 milestone ordering. Original `M0 → M1 → M2 → M3 → M4 → M5 → M6` becomes `M0 → M1 → M6a–M6c → M4 → M5 → M6d → M7 …`.

**Cause:**

Model access is time-limited — **2–3 days**. The plan assumed model access was stable; it is not. `Plan.md` §6 requires recording any milestone that proves an assumption wrong. This is that case, and it is not a rule break: it is the amendment procedure working as designed.

The original ordering was correct on its own terms. It put the safety core first because the rule engine is pure, dependency-free, and provable. That reasoning still holds and nothing about it is retracted.

**Why it changes:**

| What | Consumes models | Perishable |
|---|---|---|
| Chunking, embedding, Leiden, Docker | no | no |
| **Entity/relationship extraction** | **yes** | **yes** |
| **Description summarisation** | **yes** | **yes** |
| **Community reports (map-reduce)** | **yes** | **yes** |

Graph extraction is ~75% of indexing cost (`docs/rag_implementationplan.md` §2). RAG *code* is permanent — Docker, Qdrant, Neo4j, MiniLM and Leiden all outlive the models. What expires is tokens. So the response is not "abandon safety ordering," it is:

> Build enough pipeline to index, index now, cache by content hash — converting a perishable resource into a permanent artifact.

The RAG plan already required this cache control, originally framed as a cost optimisation. It is now the thing standing between a 3-day window and a total loss.

**What is not changed:**

- The safety core still ships **before anything is wired together.** The escalation-isolation claim is proven before the harness exists.
- No metric, threshold, or acceptance criterion is relaxed.
- Indexing still never runs inside a test or request path (`Plan.md` D19).
- `PROMPT.md` §7.2 determinism is untouched.

**Risk introduced:**

Reordering increases the chance of building a large untested retrieval pipeline before any safety code exists. Mitigation: M6a–M6c are gated independently and must pass `make arch` at every step; M4 and M5 are not deferred in scope, only in position.

**Preferential ordering inside indexing if time runs out:**

1. **Extract entities and relationships first** — highest value, highest cost, and it is the foundation everything else derives from.
2. Description summarisation second.
3. Community reports last — derived from the graph, so a partial index still yields a usable graph.

If the window closes with extraction complete but reports missing, that is a recoverable state: the graph persists in Neo4j and reports regenerate later against any provider.

---

## 2026-10-08 — §19.3 answered: stealth models are the LLM backend

**Change:** `PROMPT.md` §19.3 moves from open to answered.

**Cause:** The models available for 2–3 days are the project's OpenAI-compatible endpoint. They are not a separate tool to be used alongside OpenRouter or OpenCode Zen — they *are* the backend.

**Still required before M7:** `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID` as environment values, plus the list of which model ids will be retired so a fallback is chosen before they are. Endpoint and credentials arrive by environment configuration and are never committed.

**Consequence:** backend choice drove the entire indexing cost model. That is now fixed, but temporarily. Since these models are retired, **every index run must record the model id used** (`PROMPT.md` §12), so a later re-index against a different provider is comparable rather than silently different.

---

## 2026-10-08 — §19.4 corpus: to be chosen now

**Status:** open, but no longer blocked on deliberation.

**Cause:** With a 2–3 day window there is no time for extended clinical review. The corpus must be chosen today so indexing can start.

**Consequence to record:** if documents are selected quickly and without a clinician reviewing them, `docs/limitations.md` must state exactly that — how many documents, where each came from, who approved them, and that no clinical review occurred. This is not a detail; an unreviewed corpus now produces a knowledge graph, not merely poor retrieval.

**Rule for now:** prefer a **small, homogeneous, clearly-licensed** corpus (one domain, e.g. emergency care) over a broad unreviewed one. Fewer documents indexed well beats many indexed carelessly, and the index is permanent while the review budget is not.

---

## 2026-10-08 — hermes-agent adopted as reference only

**Change:** `docs/reference/hermes_agent.md` added.

**Cause:** `NousResearch/hermes-agent` surfaced as a candidate ruleset. Reviewed and scoped: **reference only, no dependency, no code.**

**Why not a ruleset:** its rules fit a general-purpose personal assistant. Several conflict directly with hard constraints here — it self-improves skills during use (breaks `PROMPT.md` §7.2 determinism), auto-persists narration into memory (excluded by our `memory.md` rules), spawns subagents and writes executable scripts (against §7.2.4 isolation), and carries identity primitives — DM pairing, allowlists, platform gateways (against the §4.4 non-goal).

**What is taken:** the `AGENTS.md` and memory-file conventions, toolset gating as a model for the permission registry, command approval and container isolation as security design references, OpenRouter as an `§19.3` backend option, and the MCP integration pattern — which is recorded as an open question, not adopted.
