# Decision Log

**Append-only.** Newest at the top of each section. Never rewrite an entry — supersede it.

For amendment procedure see `Plan.md` §6. For settled design decisions made up front, see `Plan.md` §7 (`decision notes`).

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
