"""Orchestrator (S7, safety-critical). The single entry point.

LAYER 1 Laya decides -> LAYER 2 rules escalate -> LAYER 3 Qwen explains.
On ANY escalation the LLM is NEVER called (llm_called=false, asserted by test).
Byte-identical decisions: escalation is a pure function of the verdict.

Layer 3 retrieval is two-tier: internal kb_search first, then
search_external_docs (full graph walk) when the model judges internal knowledge
insufficient. Both are mediated by the tool registry chokepoint.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from medharness.contracts.models import HarnessRequest, HarnessResponse
from medharness.rules.engine import escalate
from medharness.rules.thresholds import Thresholds


@dataclass
class Orchestrator:
    decision_client: object            # .decide(state) -> LayaVerdict
    thresholds: Thresholds = field(default_factory=Thresholds)
    llm_client: object | None = None   # .complete(messages)->str  (the LLM boundary)
    external_search: object | None = None   # .search(query, mode=None)->SearchResult
    dispatch: object | None = None     # optional override (tool, role, args)->ToolFact

    def _bound_dispatch(self, tool: str, role: str, args: dict):
        """Mediation chokepoint with external_search injected."""
        if self.dispatch is not None:
            return self.dispatch(tool, role, args)
        from medharness.tools import registry
        return registry.dispatch(tool, role, args, external_search=self.external_search)

    def assess(self, request: HarnessRequest) -> HarnessResponse:
        from medharness.generation import policy
        text, role = request.text, request.actor_role

        # --- LAYER 1: decide (no generation) ---
        verdict = self.decision_client.decide(text)

        # --- LAYER 2: rules escalate (deterministic, no LLM) ---
        esc = escalate(verdict, self.thresholds)
        will_use_llm = self.llm_client is not None

        trace = {
            "acuity": verdict.acuity,
            "acuity_distribution": {k: round(v, 4)
                                    for k, v in verdict.acuity_distribution.items()},
            "red_flags": {n: {"p": round(f.probability, 4), "threshold": f.threshold}
                          for n, f in verdict.red_flags.items()},
            "abstained": verdict.abstained,
            "thresholds_version": self.thresholds.version,
            "llm_called": will_use_llm,   # becomes authoritative below
        }

        if esc is not None:
            # Escalated: STOP. The LLM is never reached. llm_called is false.
            trace["llm_called"] = False
            trace["escalation_reasons"] = [
                {"flag": r.flag, "probability": r.probability, "threshold": r.threshold}
                for r in esc.reasons]
            return HarnessResponse(decision="escalate", escalated=True,
                                   escalation=esc, explanation=esc.instruction,
                                   citations=[], decision_trace=trace)

        # --- LAYER 3: explain (only reached when NOT escalated) ---
        explanation, citations = self._explain(text, role)
        screen = policy.screen(explanation, citations)
        if not screen["ok"]:
            explanation, citations = screen["answer"], []
        return HarnessResponse(decision="explain", escalated=False,
                               escalation=None, explanation=explanation,
                               citations=citations, decision_trace=trace)

    def _explain(self, text: str, role: str) -> tuple[str, list[str]]:
        if self.llm_client is None:
            # No LLM: degraded but still decided. Never fabricate an answer.
            return ("Automated explanation is unavailable right now. This is not a "
                    "diagnosis; consult a clinician."), []
        from medharness.generation.qwen_client import run_tool_loop
        res = run_tool_loop(self.llm_client, role, text, self._bound_dispatch)
        return res.answer, res.citations

