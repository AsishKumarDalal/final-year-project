"""Orchestrator tests (S7, safety-critical). No network.

The load-bearing test: test_escalation_never_calls_llm swaps in a Qwen stub that
RAISES if touched, then asserts every escalated case returns with llm_called
false and the stub untouched. This is the dynamic half of sect 7.2.4 isolation.
"""
from __future__ import annotations

import unittest

from medharness.contracts.models import HarnessRequest
from medharness.decision.fixture_adapter import FixtureDecisionClient
from medharness.orchestrator import Orchestrator


class RaisingClient:
    """The LLM boundary. Any touch on the escalated path is a bug -> explode."""

    touched = False

    def complete(self, messages):
        RaisingClient.touched = True
        raise AssertionError("LLM was called on an escalated request - isolation broken")


class StubClient:
    def __init__(self, answer="General information about headaches; consult a clinician."):
        self.answer = answer

    def complete(self, messages):
        return self.answer


class EscalationIsolationTests(unittest.TestCase):
    def setUp(self):
        RaisingClient.touched = False

    def test_escalation_never_calls_llm(self):
        c = FixtureDecisionClient()
        cases = ["Severe chest pain with pressure spreading to my left arm since this morning.",
                 "My face is drooping on one side and I cannot speak clearly.",
                 "I do not want to live anymore.",
                 "Unresponsive, barely breathing, weak pulse.",
                 "Vomiting blood and black stools since last night."]
        orch = Orchestrator(decision_client=c, llm_client=RaisingClient(),
                            dispatch=lambda *a: (_ for _ in ()).throw(AssertionError("no tools")))
        for text in cases:
            resp = orch.assess(HarnessRequest(text=text, actor_role="patient"))
            self.assertTrue(resp.escalated, f"should escalate: {text[:30]}")
            self.assertFalse(resp.decision_trace["llm_called"])
            self.assertFalse(resp.escalation.llm_called)
        self.assertFalse(RaisingClient.touched, "raising LLM was touched on escalation")

    def test_safe_case_may_call_llm(self):
        c = FixtureDecisionClient()
        orch = Orchestrator(decision_client=c, llm_client=StubClient(),
                            dispatch=lambda *a: None)
        resp = orch.assess(HarnessRequest(
            text="I have had a mild headache for two days, no other symptoms."))
        self.assertFalse(resp.escalated)
        self.assertTrue(resp.decision_trace["llm_called"])
        self.assertEqual(resp.decision, "explain")

    def test_degraded_without_llm(self):
        c = FixtureDecisionClient()
        orch = Orchestrator(decision_client=c)  # no llm_client
        resp = orch.assess(HarnessRequest(
            text="I have had a mild headache for two days, no other symptoms."))
        self.assertFalse(resp.escalated)
        self.assertIn("unavailable", resp.explanation)
        self.assertEqual(resp.citations, [])
        self.assertFalse(resp.decision_trace["llm_called"])

    def test_deterministic_decisions(self):
        c = FixtureDecisionClient()
        orch = Orchestrator(decision_client=c, llm_client=RaisingClient(),
                            dispatch=lambda *a: None)
        text = "Severe chest pain with pressure spreading to my left arm since this morning."
        runs = [orch.assess(HarnessRequest(text=text)).decision_trace
                for _ in range(5)]
        self.assertEqual(len({repr(r) for r in runs}), 1, "decisions not byte-identical")

    def test_response_shape(self):
        c = FixtureDecisionClient()
        orch = Orchestrator(decision_client=c, llm_client=RaisingClient(),
                            dispatch=lambda *a: None)
        resp = orch.assess(HarnessRequest(text="I do not want to live anymore."))
        self.assertEqual(resp.decision, "escalate")
        self.assertTrue(resp.escalation.instruction)
        self.assertIn("emergency", resp.escalation.instruction.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
