# deprecated/

Code that is **not part of the product** and kept only for reference. Nothing
here is imported by `src/medharness/`, run by `make validate`, or shipped.

## `harness/` — Track H, the coding-agent ReAct loop (DEPRECATED 2026-10-09)

A **generic coding assistant** (terminal ReAct agent: `read_file`, `write_file`,
`run_command`, sub-agents, sessions). It was the *inspiration and mechanics donor*
for the medical harness — its loop shape, tool-registry chokepoint, and
error-as-observation pattern were reused.

It is **not the medical product** and is not maintained. The real product is
`src/medharness/` (Track A). This was superseded when the build focus moved to the
medical decision-support harness.

**Do not build on this. Do not import it.** Kept only so the reuse-vs-replace
history in `docs/harness_plan.md` §3 stays checkable.
