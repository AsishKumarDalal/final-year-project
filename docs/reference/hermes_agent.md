# Hermes Agent — Reference Notes

**Status:** reference only. **No dependency, no code borrowed, no rules adopted.**

Source: [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) — 252k stars, MIT, Nous Research.

---

## 1. What it is

A **general-purpose personal AI assistant**. Terminal TUI, a messaging gateway for Telegram/Discord/Slack/WhatsApp/Signal, a skills system with self-improvement, agent-curated memory, FTS5 session search, a cron scheduler, subagent spawning, 40+ tools, MCP integration, and swappable model providers (Nous Portal, OpenRouter, OpenAI, custom endpoints).

It is a *product*. It is not a library, not a framework, and not a medical system.

---

## 2. What we take

| From Hermes | Applied here as | Where |
|---|---|---|
| `AGENTS.md` as project context read by the agent | Our `AGENTS.md` | root |
| Agent memory file persisting across sessions | Our `memory.md` | root |
| `hermes tools` — enable/disable toolsets | Role-permission registry enforced at the tool-call boundary | `PROMPT.md` §6, `tools/registry.py` |
| Command approval + container isolation | Security design reference for permission enforcement | `PROMPT.md` §6 |
| OpenRouter as a provider | Confirmation that an OpenAI-compatible backend is viable for §19.3 | `PROMPT.md` §19.3 |
| `evals/` + batch trajectory generation | Corroborates the evaluation harness as a first-class deliverable | `Plan.md` §2 M11, `PROMPT.md` §15 |
| MCP integration | **Open question — not adopted** | §4 below |

**Why these are safe to take:** none of them touch determinism, the safety path, or the scope boundary.

---

## 3. What we reject, and why

Its rules fit a general assistant. Several conflict directly with hard constraints here.

| Hermes behaviour | Conflicts with | Why it matters here |
|---|---|---|
| **Self-improving skills created autonomously during use** | `PROMPT.md` §7.2.1 | Behaviour that changes mid-evaluation invalidates every reported metric. A system that rewrites itself cannot be measured. |
| **Auto-nudges to persist knowledge into memory** | `Implement.md` §5, `memory.md` rules | Our memory file records durable facts, not narration. Auto-persisted chatter is how a memory file stops being read. |
| **Subagents; writes Python scripts calling tools via RPC** | `PROMPT.md` §7.2.4 | The safety path must be deterministic and isolated. A component that writes and executes arbitrary code is orthogonal to that and dangerous here. |
| **Identity primitives — DM pairing, allowlists, platform gateways** | `PROMPT.md` §4.4 non-goal | We do not issue identity. Role arrives as a trusted header; the harness enforces permissions but never authenticates. |
| **40+ general tools** | `Implement.md` §3 scope discipline | A tool costs a fixed checklist: schema, permission row, provenance, eval case, docs entry. Not "someone wrote a function." |
| **7 terminal backends, cloud/serverless persistence, cron** | `PROMPT.md` §4 non-goal — no cloud deployment | Out of scope entirely. |

**The general shape of the conflict:** Hermes optimises for capability growth over time. `PROMPT.md` §7.2 optimises for reproducibility at a point in time. You cannot have both, and this project's claim rests on measurement.

---

## 4. Open question — MCP

Both Hermes and Laya ship MCP support (`laya[mcp]` is an optional extra of the Laya package).

**Possibility:** expose our tools over MCP instead of a bespoke tool interface, and the harness becomes composable with any MCP client — while Laya slots in natively as well.

**Why it is only an open question:**

- It would change `src/harness/tools/` from a Python interface into a protocol boundary, which reshapes `Plan.md` §4.2's dependency contracts.
- It adds a dependency in a project whose architecture gate is deliberately zero-dependency (`Plan.md` D9).
- Nobody has evaluated whether MCP's transport helps or hurts the §7.2.1 determinism claim.

**Decision:** deferred. Raise it as an amendment through `Plan.md` §6 with a written finding if it is wanted. Do not adopt it opportunistically while building a milestone.

---

## 5. What is explicitly not taken

- No dependency on the package.
- No code, prompts, skill definitions, or configuration format copied.
- No rules imported wholesale — where a Hermes rule conflicts with a hard constraint here, the hard constraint wins.
- No star count treated as evidence of quality. 252k stars indicates popularity, not fitness for a safety-critical medical harness. Verify anything adopted by measurement (`PROMPT.md` §15).
