"""Jailbreak-proof escalation tests (S12). No network. The moat: no adversarial
phrasing can talk the system out of escalating, because the LLM never runs on
the escalated path.
"""
from __future__ import annotations

import unittest

from medharness.security_demo import (
    INJECTION_PHRASINGS,
    prove_escalation_is_jailbreak_proof,
    render_jailbreak_report,
)


class JailbreakProofTests(unittest.TestCase):
    def test_every_phrasing_still_escalates(self):
        results, all_held = prove_escalation_is_jailbreak_proof()
        self.assertEqual(len(results), len(INJECTION_PHRASINGS))
        self.assertTrue(all_held, "an adversarial phrasing breached escalation")
        for r in results:
            self.assertTrue(r["escalated"])
            self.assertFalse(r["llm_called"])

    def test_llm_never_touched(self):
        # prove_... resets ExplodingLLM.touched internally; all_held already asserts it
        _, all_held = prove_escalation_is_jailbreak_proof()
        self.assertTrue(all_held)

    def test_report_renders(self):
        txt = render_jailbreak_report()
        self.assertIn("PROOF", txt)
        self.assertIn("ALL HELD", txt)
        self.assertIn(str(len(INJECTION_PHRASINGS)), txt)

    def test_has_multiple_distinct_phrasings(self):
        self.assertGreaterEqual(len(INJECTION_PHRASINGS), 8)
        self.assertEqual(len(set(INJECTION_PHRASINGS)), len(INJECTION_PHRASINGS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
