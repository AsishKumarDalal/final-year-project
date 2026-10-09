# Architecture

## Tracks in this repo (three, independent)

| Track | What | Lives in | Spec |
|---|---|---|---|
| A | Medical decision-support harness (product) | `src/harness/` (unbuilt) | `PROMPT.md`, `Plan.md` M0–M15 |
| B | GraphRAG indexer (knowledge-graph builder) | `graphrag/` | `docs/rag_docs/`, `graphrag_plan.md` |
| H | Coding-agent harness (terminal ReAct agent) | `harness/` | `docs/harness_docs/` (reference, copied 2026-10-09) |

Rules: H never imports A, B, or `graphrag`. A never imports H. B never imports H.
H is a standalone terminal tool, not part of `make validate`.

## Track H — layout

```
harness/
├── main.py            # REPL adapter (/new /sessions /resume /title /memory /reflect /exit)
├── agent/
│   ├── loop.py        # run() — the ReAct loop; ONLY caller of dispatch/maybe_compress/chat_with_retries
│   ├── llm.py         # chat() — thin OpenAI-SDK chat-completions wrapper, no policy/retries
│   ├── prompt.py      # SYSTEM_PROMPT
│   ├── context.py     # maybe_compress() — budget 24000 chars, keep 6 recent turns
│   ├── sessions.py    # SQLite persistence + FTS5 search (plural name is canonical)
│   ├── subagents.py   # spawn_child() — depth-guarded, ephemeral, no further delegation
│   ├── resilience.py  # chat_with_retries() — retry + breaker + emergency compress
│   └── memory.py      # MEMORY.md load / reflect-and-save
└── tools/
    ├── registry.py    # REGISTRY, schemas(), dispatch() — the ONLY mediation point
    ├── files.py       # read_file / write_file / run_command
    ├── sandbox.py     # validate_path() — workspace allowlist, forbidden names
    ├── approval.py    # dangerous-command gate, fail-closed callback seam
    ├── todo.py        # todo.md task list
    └── memory_search.py  # session-search tool handler
```

## Track H — data flow

```
User msg → main.py REPL → agent/loop.py run()
  → chat_with_retries() → assistant msg + tool_calls
  → per call: dispatch() → sandbox/approval checks → handler → observation
  → append tool msgs (call order) → sessions.append()
  → maybe_compress() when over budget → return answer
```

## Track H — dependency contracts

| Module | May import |
|---|---|
| `agent/llm.py`, `agent/prompt.py` | stdlib, `openai` (llm only) |
| `tools/*` | stdlib, `agent/sessions.py` (memory_search only) |
| `agent/loop.py` | everything in `harness/` |
| `main.py` | everything in `harness/` |

`dispatch()` is the only path from a tool call to execution. No handler is
called from anywhere else. Approval fails closed (exception / no-TTY = deny).
