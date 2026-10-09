# RAG Implementation Plan — GraphRAG for the Medical Harness

**Status:** Draft · replaces `Plan.md` M6 · supersedes `Plan.md` D6, D7, D8
**Scope:** the `kb_search` tool only. The Laya decision layer, rule engine, and LLM policy are unchanged.

---

## 1. Decision summary

| Concern | Choice | Notes |
|---|---|---|
| Retrieval architecture | **GraphRAG**, replicating `microsoft/graphrag` | Global + Local + Basic search modes |
| Vector store | **Qdrant**, Docker | 3 collections, mirroring GraphRAG's embedding targets |
| Graph store | **Neo4j**, Docker | Entities, relationships, communities, provenance |
| Query-mode router | **Laya** `choice` question | Deterministic, ~33 ms, no LLM |
| Embeddings | **`all-MiniLM-L6-v2`**, downloaded locally, 384-dim | No embedding API calls |
| LLM (indexing + query) | **OpenAI-compatible client**; OpenCode Zen or OpenRouter | One interface, swappable backend |

### ⚠️ Read this before committing to the dependency

**`microsoft/graphrag` is in maintenance mode.** Its own README: *"largely in maintenance mode, and won't be accepting new PRs or implementing new features."* Bug fixes and CVE patches only.

**Therefore: replicate the architecture, do not take the dependency.** The method is published and stable (arXiv 2404.16130, the 2024 Microsoft Research paper); the implementation is a research demo, explicitly *"not an officially supported Microsoft offering."*

Implementing the pipeline ourselves is roughly the same work as wiring up their package, and it avoids inheriting a dependency that will not receive fixes. It also lets us use Qdrant and Neo4j, which GraphRAG does not support natively.

---

## 2. Indexing pipeline

GraphRAG's default pipeline, adapted to our stores. Every stage writes to Neo4j or Qdrant and is **idempotent** — re-running must not duplicate nodes.

```
data/corpus/*.pdf|txt
        │
        ▼
  [1] LoadDocuments ──────────► Neo4j :Document
        │
        ▼
  [2] ChunkDocuments ─────────► Neo4j :TextUnit  (+ Document)-[:CONTAINS]->(TextUnit)
        │  1200 tokens, 100 overlap
        ▼
  [3] ExtractGraph ───────────► Neo4j :Entity {title, type, description}
        │  LLM per text unit       :Relationship {source, target, description, weight}
        │  → merge by (title,type)   Neo4j :Entity)-[:MENTIONED_IN]->(:TextUnit)
        │  → weight += 1 per dupe
        ▼
  [4] SummarizeDescriptions ──► update :Entity.description
        │  one LLM call per node/edge
        ▼
  [5] EmbedEntities ──────────► Qdrant collection `entities`
        │
        ▼
  [6] EmbedTextUnits ─────────► Qdrant collection `text_units`
        │
        ▼
  [7] DetectCommunities ──────► Neo4j :Community {level, title, summary}
        │  hierarchical Leiden         (:Entity)-[:MEMBER_OF]->(:Community)
        │                              (:Community)-[:CHILD_OF]->(:Community)
        ▼
  [8] GenerateReports ────────► Neo4j :Community.summary + :CommunityReport
        │  bottom-up map-reduce, per level
        ▼
  [9] EmbedReports ───────────► Qdrant collection `community_reports`
```

### Stage notes

**[1]–[2] Load and chunk.** Track `Document.id → TextUnit.id` for provenance. This is what makes a citation resolvable, which `PROMPT.md` §7.3.4 requires as a hard failure.

**[3] Extract graph.** LLM prompt per text unit returns entities and relationships. Entity types are **domain-specific**, not GraphRAG's defaults:

```
symptom, condition, test, drug, procedure, body_system, guideline, population
```

Merge on `(title, type)`. Co-occurring duplicates increment relationship weight — GraphRAG uses degree-weighted edges for prioritisation.

**[4] Summarise descriptions.** Multiple text units mention the same entity; fold their descriptions into one. Without this, nodes carry duplicated text and rank badly.

**[5]–[6] Embed.** `all-MiniLM-L6-v2` at **384 dimensions**. One Qdrant collection per target, matching GraphRAG's three embedding targets. Store `doc_id` / `text_unit_id` / `entity_key` in Qdrant payload so results map back to Neo4j.

**[7] Detect communities.** **Hierarchical Leiden**, recursing to a size threshold. Libraries: `graphdask` (GraphRAG's own) or `python-igraph` + `leidenalg`. Store each level so query time can choose the granularity.

**[8] Generate community reports.** Bottom-up. Leaf communities: prioritise edges by combined source+target degree, fill the context window until the token limit. Higher communities: if element summaries overflow, substitute shorter sub-community summaries iteratively. Both use LLM map-reduce.

**[9] Embed reports.** Only now, after reports are final — they are a query-time artefact, so embedding earlier wastes work.

### Cost control — mandatory

GraphRAG's own docs: **graph extraction is ~75% of indexing cost.** Their warning: *"GraphRAG indexing can be an expensive operation… start small."*

Controls:
- Index a **small corpus first** (10 documents). Measure actual spend before scaling.
- Claim extraction stays **disabled** (GraphRAG's default).
- **Cache LLM responses by content hash.** Re-indexing must not re-pay for unchanged units.
- Log tokens and cost per stage; `make rag-cost` prints it.

If cost proves prohibitive, **FastGraphRAG** is the documented fallback: NLP noun-phrase extraction instead of LLM, smaller 50–100 token chunks, community reports still LLM-generated. Much cheaper, noisier graph. Decide only after measuring, not before.

---

## 3. Query-time routing

### 3.1 Laya decides the search mode

A single `choice` question. Fast, deterministic, no LLM in the path:

```json
{
  "search_mode": {
    "type": "choice",
    "instructions": "Which retrieval strategy best answers this question?",
    "criteria": {
      "local":  "questions about one specific condition, drug, test, or symptom — 'what treats X', 'what is the dose of Y', 'is Z a symptom of asthma'",
      "global": "questions about patterns or themes across many conditions — 'what causes most readmissions', 'summarise the main risks', 'compare management across these conditions'",
      "basic":  "a direct lookup of a specific fact — 'what is the normal range for potassium'",
      "none":   "not a medical information question at all"
    }
  }
}
```

**Why Laya and not an LLM classifier:** it is a routing decision, so it belongs in System 1 (`PROMPT.md` §5). It is ~33 ms and deterministic, so the same query always picks the same mode — which the reproducibility requirement in `PROMPT.md` §7.2 requires. An LLM router would make retrieval mode vary between identical requests.

`"none"` matters: `kb_search` must decline rather than return weak results when the query is out of scope. `PROMPT.md` §11.2 already requires refusing ungroundable requests.

**Fine-tuning is expected.** Off-the-shelf Laya has no medical training (`PROMPT.md` §14.3), so routing accuracy in Phase A will be poor and must be measured, not assumed. The routing eval set is the Phase B dataset for this component.

**Always run Basic search as well.** Global and Local both miss direct lookups. Merge Local/Basic results by entity and by vector score respectively; never merge Global with the others, because global output is a synthesised answer, not passages.

### 3.2 Local search

Best for specific entities — "what are the contraindications of metformin."

1. Embed the query → Qdrant `entities` → top-k seed entities
2. From Neo4j, expand each seed:
   - its `MENTIONED_IN` text units
   - its neighbouring entities and relationship descriptions
   - its community reports
3. Rank, filter to one context window
4. Single LLM call — Local does **not** need map-reduce

### 3.3 Global search

Best for corpus-level questions — "what are the main cardiovascular risks described here?"

1. Select community reports at one hierarchy level (`PROMPT.md` determinism: pin the level in the trace)
2. **Map:** each report independently produces key points with a 0–100 helpfulness score, in parallel; score-0 answers are dropped
3. **Reduce:** sort by score, fill a fresh context window, produce the final answer

**Cost:** N LLM calls for map + 1 for reduce, per query. This is the most expensive path in the system and must be visible in `make rag-cost`. Higher hierarchy levels give thorough answers and higher cost — the tradeoff is explicit in GraphRAG's docs and must be a recorded setting, not a default nobody knows about.

### 3.4 Basic search

Plain vector top-k over `text_units`. One LLM call. Cheap fallback.

### 3.5 Response contract

Every mode returns the `PROMPT.md` §10 shape:

```json
{
  "facts": ["..."],
  "source_ids": ["text_unit:kb_0042", "entity:condition/metformin"],
  "source_type": "graphrag",
  "search_mode": "local",
  "search_mode_confidence": 0.91
}
```

`source_ids` must resolve against `data/corpus/` — unresolvable ids are a hard failure (`PROMPT.md` §7.3.4). `search_mode` and its confidence go into the decision trace, so routing is auditable.

---

## 4. Docker services

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333", "6334:6334"]
    volumes: ["./data/qdrant:/qdrant/storage"]

  neo4j:
    image: neo4j:5-community
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: "neo4j/${NEO4J_PASSWORD}"
    volumes: ["./data/neo4j:/data"]
```

Both are **development-only**. Persistent volumes live under `data/`, which is gitignored. `make up` / `make down` / `make rag-reset` wrap this.

**Credentials come from the environment**, never committed. `NEO4J_PASSWORD`, `QDRANT_URL`, `NEO4J_URI`, `NEO4J_USER` go in `.env`, which M0 adds to `.gitignore`.

Cost: two services, ~1.5 GB RAM combined. That is a real operational cost against `PROMPT.md` §7.4's offline requirement — **a degraded mode with both stores down must still return the decision and escalation**, since neither store is on the safety path.

---

## 5. LLM provider

One OpenAI-compatible client interface, swappable backend:

| Option | Endpoint shape |
|---|---|
| OpenAI | `https://api.openai.com/v1` |
| OpenCode Zen | OpenAI-compatible gateway |
| OpenRouter | `https://openrouter.ai/api/v1` |

Configuration by environment only:

```
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL_ID=
```

Consequences worth stating plainly:

- **Indexing cost is real** and depends entirely on which backend is chosen. Provider choice is therefore a *cost* decision, not just a quality one. Still open — `PROMPT.md` §19.3.
- Model id **must** be recorded in every trace (`PROMPT.md` §12). Otherwise a result cannot be reproduced if the provider aliases or rotates a model.
- The critical escalation path still never calls the LLM (`PROMPT.md` §7.2.4). Indexing and query generation are both downstream of that boundary.

---

## 6. Changes to the existing plan

### `Plan.md` — decisions superseded

| Was | Now | Why |
|---|---|---|
| **D6** numpy cosine search, no vector DB | **Qdrant + Neo4j in Docker** | GraphRAG needs a graph store; plain cosine cannot express community hierarchy |
| **D7** precomputed embeddings in `data/index/` | **Qdrant collections** | Embeddings live with the vector index |
| **D8** hash embedder for tests | **stub embedder client, same interface** | Real MiniLM in production and integration; deterministic stub only where a network-free test needs it |

D6's original rationale — *"brute force is exact, deterministic, and dependency-free"* — is superseded by a capability requirement, not a preference. Log the reversal in `docs/decisions.md`.

**A new constraint:** GraphRAG's indexing pipeline is **offline and LLM-dependent**. It must not run inside a request path, and it must never run during `make validate`. Indexing is a separate command (`make rag-index`), never part of a test.

### `Plan.md` — M6 rewritten

M6 becomes **M6a** (embedding client + Qdrant), **M6b** (Neo4j graph model + Leiden), **M6c** (community reports), **M6d** (three query modes + Laya router). Validations become `make rag-index-smoke`, `make rag-query-smoke`, `make rag-cost`, plus the existing citation and injection tests.

### `PROMPT.md` — §19.3 and §19.4

- **§19.3** narrowed: the *interface* is settled (OpenAI-compatible). The *backend* remains open.
- **§19.4** partially answered: storage and architecture settled; **which documents, and who approves them as trusted, is still open.** GraphRAG raises the stakes — an unreviewed corpus now produces a knowledge graph, not just retrieved passages.

### `PROMPT.md` — §7.1 latency budget

The §7.1 budget assumed a single retrieval call. Global search is a map-reduce over community reports and will breach it. Amend: Basic ≤ 500 ms, Local ≤ 2 s, Global ≤ 15 s — and Global must stream partial results rather than hold the request.

---

## 7. Risks

| Risk | Severity | Handling |
|---|---|---|
| **Indexing cost** — extraction is ~75% of index cost | **High** | Small corpus first, response caching by content hash, `make rag-cost` after every index |
| **`graphrag` is unmaintained** | **High** | Replicate the method; take no dependency. Cite the paper, not the repo |
| **Routing accuracy off-the-shelf** — Laya has no medical training | **High** | Measured in Phase A; `"none"` option lets it decline; fine-tuned in Phase B |
| **Two Docker services** | Medium | Dev-only; both outside the safety path; degraded mode required |
| **Leiden determinism** — library choice may not be reproducible | Medium | Pin the library and version; verify community ids are stable across runs |
| **Entity merge quality** — same entity, different names | Medium | Canonical title map in `data/corpus/`; log merges for review |
| **Hallucinated graph relations** | **High** | Every edge carries the text unit it came from; citations resolve to source text, never to a generated description |
| **Global search answer ≠ source** | Medium | A synthesised global answer is labelled as generated interpretation, never as `source_facts` (`PROMPT.md` §7.3.6) |

**The hallucinated-graph-relations risk is the one to hold onto.** A generated entity description is model output, so it is *not* a source fact. Citations must resolve to text units in `data/corpus/`. If a citation can only be satisfied by pointing at a generated description, the citation is invalid — that is the rule that keeps GraphRAG from quietly becoming a hallucination engine.

---

## 8. Open questions

1. **Which documents, and who approves them?** Still open (`PROMPT.md` §19.4). GraphRAG makes this heavier — bad input now produces a graph, not just poor retrieval.
2. **Which LLM backend**, and what index budget? Drives the whole cost model (§5).
3. **Standard or FastGraphRAG?** Standard is better quality; FastGraphRAG is far cheaper. Decide after measuring Standard on a small corpus.
4. **Community hierarchy level** for global search. Lower = more thorough and more expensive. Must be a recorded setting.
5. **Entity type list** — the nine proposed types need domain review.