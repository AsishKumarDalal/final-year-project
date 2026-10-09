"""Decision Receipt tests (S10). No network. The receipt is the differentiator:
calibrated board, deterministic-safety guarantee, reproducible fingerprint,
resolvable citations — none of which a general chatbot can produce.
"""
from __future__ import annotations

import unittest

from medharness.contracts.models import HarnessRequest
from medharness.decision.fixture_adapter import FixtureDecisionClient
from medharness.orchestrator import Orchestrator
from medharness.receipt import build_receipt, fingerprint, render_text


class RaisingClient:
    def complete(self, messages):
        raise AssertionError("LLM touched on escalation")


def assess(text, llm=None):
    orch = Orchestrator(decision_client=FixtureDecisionClient(), llm_client=llm,
                        dispatch=lambda *a: None)
    return orch.assess(HarnessRequest(text=text))


class ReceiptTests(unittest.TestCase):
    def test_escalated_receipt_has_no_ai_in_decision(self):
        resp = assess("Severe chest pain with pressure spreading to my left arm since this morning.",
                      llm=RaisingClient())
        r = build_receipt(resp)
        self.assertTrue(r["escalated"])
        self.assertFalse(r["ai_involved_in_decision"])   # the guarantee
        self.assertTrue(any(f["fired"] for f in r["red_flag_board"]))

    def test_calibrated_board_present(self):
        resp = assess("Severe chest pain with pressure spreading to my left arm since this morning.")
        board = build_receipt(resp)["red_flag_board"]
        chest = next(f for f in board if f["flag"] == "red_flag_chest_pain")
        self.assertGreater(chest["probability"], 0.7)   # a real calibrated number
        self.assertEqual(chest["threshold"], 0.7)

    def test_fingerprint_reproducible(self):
        text = "Severe chest pain with pressure spreading to my left arm since this morning."
        a = assess(text)
        b = assess(text)
        self.assertEqual(fingerprint(a), fingerprint(b))

    def test_fingerprint_differs_by_decision(self):
        esc = assess("I do not want to live anymore.")
        safe = assess("I have had a mild headache for two days, no other symptoms.")
        self.assertNotEqual(fingerprint(esc), fingerprint(safe))

    def test_safe_case_ai_involved(self):
        resp = assess("I have had a mild headache for two days, no other symptoms.",
                      llm=type("C", (), {"complete": lambda s, m: "info"})())
        self.assertTrue(build_receipt(resp)["ai_involved_in_decision"])

    def test_render_text_has_key_lines(self):
        resp = assess("Severe chest pain with pressure spreading to my left arm since this morning.")
        txt = render_text(resp)
        self.assertIn("Decision Receipt", txt)
        self.assertIn("NO — safety decision was deterministic", txt)
        self.assertIn("Fingerprint", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
