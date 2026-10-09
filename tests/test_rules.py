"""Rule engine tests (S3). No network. Boundary values, all 5 sect 9 branches,
downgrade-forbidden, llm_called invariant. Runnable with stdlib unittest.
"""
from __future__ import annotations

import unittest

from medharness.contracts.models import (
    ACUITY_ORDER,
    LayaVerdict,
    RedFlag,
)
from medharness.decision.fixture_adapter import FixtureDecisionClient
from medharness.rules.engine import escalate
from medharness.rules.thresholds import Thresholds


def verdict(acuity, dist, flags, abstained=False):
    rf = {n: RedFlag(probability=p, threshold=0.70 if n != "red_flag_suicide_risk" else 0.50)
          for n, p in flags.items()}
    return LayaVerdict(acuity=acuity, acuity_distribution=dist, red_flags=rf,
                       abstained=abstained)


def dist(resus=0.02, urgent=0.08, soon=0.10, routine=0.80):
    return {"resuscitation": resus, "urgent": urgent, "soon": soon, "routine": routine}


class EscalateTests(unittest.TestCase):
    def test_suicide_branch(self):
        v = verdict("urgent", dist(), {"red_flag_suicide_risk": 0.50})
        esc = escalate(v)
        self.assertIsNotNone(esc)
        self.assertTrue(esc.llm_called is False)
        self.assertTrue(any(r.flag == "red_flag_suicide_risk" for r in esc.reasons))

    def test_other_flag_branch(self):
        v = verdict("routine", dist(), {"red_flag_chest_pain": 0.70})
        esc = escalate(v)
        self.assertIsNotNone(esc)
        self.assertTrue(any(r.flag == "red_flag_chest_pain" for r in esc.reasons))

    def test_acuity_level_branch(self):
        v = verdict("urgent", dist(), {})
        esc = escalate(v)
        self.assertIsNotNone(esc)
        self.assertTrue(any(r.flag == "acuity_level" for r in esc.reasons))

    def test_resuscitation_prob_branch(self):
        v = verdict("routine", dist(resus=0.10), {})
        esc = escalate(v)
        self.assertIsNotNone(esc)
        self.assertTrue(any(r.flag == "resuscitation_probability" for r in esc.reasons))
        self.assertEqual(esc.level, "resuscitation")

    def test_abstain_flag_branch(self):
        v = verdict("soon", dist(), {"red_flag_chest_pain": 0.40}, abstained=True)
        esc = escalate(v)
        self.assertIsNotNone(esc)
        self.assertTrue(any(r.flag == "insufficient_confidence:red_flag_chest_pain"
                            for r in esc.reasons))

    def test_abstain_low_flag_no_escalate(self):
        v = verdict("soon", dist(), {"red_flag_chest_pain": 0.39}, abstained=True)
        self.assertIsNone(escalate(v))

    def test_no_escalation_safe_case(self):
        v = verdict("routine", dist(), {n: 0.02 for n in
                  ("red_flag_chest_pain", "red_flag_suicide_risk")})
        self.assertIsNone(escalate(v))

    def test_boundary_other_flag_699_vs_700(self):
        below = verdict("routine", dist(), {"red_flag_chest_pain": 0.6999})
        at = verdict("routine", dist(), {"red_flag_chest_pain": 0.7000})
        self.assertIsNone(escalate(below))
        self.assertIsNotNone(escalate(at))

    def test_boundary_resus_099_vs_100(self):
        below = verdict("routine", dist(resus=0.0999), {})
        at = verdict("routine", dist(resus=0.1000), {})
        self.assertIsNone(escalate(below))
        self.assertIsNotNone(escalate(at))

    def test_boundary_suicide_499_vs_500(self):
        below = verdict("routine", dist(), {"red_flag_suicide_risk": 0.4999})
        at = verdict("routine", dist(), {"red_flag_suicide_risk": 0.5000})
        self.assertIsNone(escalate(below))
        self.assertIsNotNone(escalate(at))

    def test_downgrade_forbidden(self):
        # High-probability negative on OTHER flags must NOT suppress a fired flag.
        v = verdict("routine", dist(), {"red_flag_chest_pain": 0.90,
                                        "red_flag_suicide_risk": 0.01})
        esc = escalate(v)
        self.assertIsNotNone(esc)
        fired = [r.flag for r in esc.reasons]
        self.assertIn("red_flag_chest_pain", fired)

    def test_fixture_escalations(self):
        c = FixtureDecisionClient()
        for cid in ("chest_pain_severe", "stroke_signs", "suicide_risk",
                    "resuscitation_prob", "bleeding_severe", "anaphylaxis"):
            esc = escalate(c.decide_case(cid))
            self.assertIsNotNone(esc, f"{cid} must escalate")
            self.assertFalse(esc.llm_called)

    def test_fixture_safe_no_escalation(self):
        c = FixtureDecisionClient()
        for cid in ("mild_headache", "cold_mild", "back_pain", "fatigue"):
            self.assertIsNone(escalate(c.decide_case(cid)), f"{cid} must not escalate")


if __name__ == "__main__":
    unittest.main(verbosity=2)
