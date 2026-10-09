"""Clinician Handoff tests (S11). No network. The handoff is the differentiator:
typed decision + calibrated board + reproducible fingerprint + cited sources —
none of which a general chatbot can produce.
"""
from __future__ import annotations

import unittest

from medharness.contracts.models import HarnessRequest
from medharness.decision.fixture_adapter import FixtureDecisionClient
from medharness.handoff import DISCLAIMER, build_handoff, render_handoff, to_intake_json
from medharness.orchestrator import Orchestrator


class RaisingClient:
    def complete(self, messages):
        raise AssertionError("LLM touched on escalation")


def assess(text, llm=None):
    orch = Orchestrator(decision_client=FixtureDecisionClient(), llm_client=llm,
                        dispatch=lambda *a: None)
    return orch.assess(HarnessRequest(text=text))


class HandoffTests(unittest.TestCase):
    def test_escalated_handoff(self):
        resp = assess("Severe chest pain with pressure spreading to my left arm since this morning.",
                      llm=RaisingClient())
        h = build_handoff(resp, reported_text="Severe chest pain…")
        self.assertEqual(h["decision"], "escalate")
        self.assertEqual(h["triage_level"], "ESCALATE")
        self.assertFalse(h["ai_in_decision"])
        self.assertTrue(any(f["fired"] for f in h["red_flags_fired"]))
        self.assertTrue(h["fingerprint"])
        self.assertEqual(h["reported_text"], "Severe chest pain…")

    def test_calibrated_numbers_present(self):
        resp = assess("Severe chest pain with pressure spreading to my left arm since this morning.")
        board = build_handoff(resp)["red_flag_board"]
        chest = next(f for f in board if f["flag"] == "red_flag_chest_pain")
        self.assertGreater(chest["probability"], 0.7)   # a real calibrated number
        self.assertTrue(chest["fired"])

    def test_safe_handoff_ai_explanation(self):
        resp = assess("I have had a mild headache for two days, no other symptoms.",
                      llm=type("C", (), {"complete": lambda s, m: "info"})())
        h = build_handoff(resp)
        self.assertEqual(h["decision"], "explain")
        self.assertTrue(h["ai_in_decision"])
        self.assertEqual(h["red_flags_fired"], [])

    def test_disclaimer_always_present(self):
        for t in ("Severe chest pain with pressure spreading to my left arm since this morning.",
                  "I have had a mild headache for two days, no other symptoms."):
            h = build_handoff(assess(t))
            self.assertIn("not a diagnosis", h["disclaimer"].lower())
            self.assertIn("not clinically validated", h["disclaimer"].lower())

    def test_render_has_key_sections(self):
        resp = assess("Severe chest pain with pressure spreading to my left arm since this morning.")
        txt = render_handoff(resp, reported_text="chest pain")
        self.assertIn("CLINICIAN HANDOFF", txt)
        self.assertIn("RED-FLAG ASSESSMENT", txt)
        self.assertIn("Fingerprint", txt)
        self.assertIn("NO (deterministic safety gate)", txt)
        self.assertIn(DISCLAIMER, txt)

    def test_fingerprint_matches_receipt(self):
        from medharness.receipt import fingerprint
        resp = assess("I do not want to live anymore.")
        self.assertEqual(build_handoff(resp)["fingerprint"], fingerprint(resp))


class IntakeJsonTests(unittest.TestCase):
    def test_intake_is_json_serialisable(self):
        import json
        resp = assess("Severe chest pain with pressure spreading to my left arm since this morning.")
        payload = to_intake_json(resp, reported_text="chest pain")
        json.dumps(payload)  # must not raise
        self.assertEqual(payload["schema"], "medicheck.intake/v1")

    def test_intake_codes_red_flags(self):
        resp = assess("Severe chest pain with pressure spreading to my left arm since this morning.")
        rf = to_intake_json(resp)["red_flags"]
        self.assertTrue(rf["red_flag_chest_pain"]["present"])
        self.assertGreater(rf["red_flag_chest_pain"]["probability"], 0.7)
        self.assertIn("threshold", rf["red_flag_chest_pain"])

    def test_intake_safety_block(self):
        resp = assess("Severe chest pain with pressure spreading to my left arm since this morning.",
                      llm=RaisingClient())
        safety = to_intake_json(resp)["safety"]
        self.assertFalse(safety["ai_in_decision"])
        self.assertTrue(safety["fingerprint"])
        self.assertEqual(safety["thresholds_version"], "v1")

    def test_intake_has_disclaimer(self):
        resp = assess("I have had a mild headache for two days, no other symptoms.")
        self.assertIn("not a diagnosis", to_intake_json(resp)["disclaimer"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
