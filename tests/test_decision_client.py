"""Decision-client parsing (S2). Covers the live laya-serve wire shape.

Regression: live laya-serve returns noul answers as
{"type": "noul", "noul": 0.93, "confidence": 0.93} with NO "probabilities"
dict, while fixtures use string noul + probabilities. The parser used to
return 0.0 for every live flag — the safety core was blind on the live path.
Verified 2026-10-09: true text -> 0.9306, false text -> 0.1113.
"""
import unittest

from medharness.decision.client import (
    noul_probability,
    parse_verdict,
    score_distribution,
)


class TestNoulProbability(unittest.TestCase):
    def test_live_numeric_shape(self):
        ans = {"type": "noul", "noul": 0.9306, "confidence": 0.9306}
        self.assertAlmostEqual(noul_probability(ans), 0.9306)

    def test_live_numeric_false(self):
        ans = {"type": "noul", "noul": 0.1113, "confidence": 0.8887}
        self.assertAlmostEqual(noul_probability(ans), 0.1113)

    def test_fixture_string_shape(self):
        ans = {"type": "noul", "noul": "true",
               "probabilities": {"true": 0.94, "false": 0.06},
               "confidence": 0.90}
        self.assertAlmostEqual(noul_probability(ans), 0.94)

    def test_malformed_is_zero(self):
        self.assertEqual(noul_probability({}), 0.0)
        self.assertEqual(noul_probability({"type": "noul"}), 0.0)

    def test_live_flag_fires_rule(self):
        answers = {
            "acuity": {"type": "score", "score": "urgent",
                       "probabilities": {"routine": 0.05, "soon": 0.1,
                                         "urgent": 0.8, "resuscitation": 0.05},
                       "confidence": 0.8},
            "red_flag_chest_pain": {"type": "noul", "noul": 0.9306,
                                   "confidence": 0.9306},
        }
        verdict = parse_verdict(answers)
        self.assertGreaterEqual(
            verdict.red_flags["red_flag_chest_pain"].probability, 0.70)


class TestScoreLegend(unittest.TestCase):
    LIVE_ACUITY = {"type": "score", "score": 0.8779,
                   "legend": {"0": "routine", "1": "soon",
                              "2": "urgent", "3": "resuscitation"},
                   "probabilities": {"0": 0.3645, "1": 0.399,
                                     "2": 0.2308, "3": 0.0058},
                   "confidence": 0.2046}

    def test_numeric_keys_resolve_via_legend(self):
        dist = score_distribution(
            self.LIVE_ACUITY, ["routine", "soon", "urgent", "resuscitation"])
        self.assertAlmostEqual(dist["resuscitation"], 0.0058, places=3)
        self.assertAlmostEqual(sum(dist.values()), 1.0, places=3)

    def test_cold_does_not_breach_resuscitation(self):
        verdict = parse_verdict({"acuity": self.LIVE_ACUITY})
        self.assertLess(
            verdict.acuity_distribution["resuscitation"], 0.10)

    def test_expected_level_uses_distribution_not_argmax(self):
        # §8.1: escalation rides the distribution. A cold lands on urgent by
        # argmax (top mass 0.50) but E[level] = 1.4 -> "soon", not escalate.
        verdict = parse_verdict({"acuity": self.LIVE_ACUITY})
        self.assertIn(verdict.acuity, ("routine", "soon"))
        self.assertNotEqual(verdict.acuity, "urgent")

    def test_real_emergency_still_escalates(self):
        answers = {"acuity": {"type": "score", "score": 2.0,
                              "legend": {"0": "routine", "1": "soon",
                                         "2": "urgent", "3": "resuscitation"},
                              "probabilities": {"0": 0.01, "1": 0.05,
                                                "2": 0.74, "3": 0.20},
                              "confidence": 0.8}}
        verdict = parse_verdict(answers)
        self.assertIn(verdict.acuity, ("urgent", "resuscitation"))
        self.assertGreaterEqual(
            verdict.acuity_distribution["resuscitation"], 0.10)


if __name__ == "__main__":
    unittest.main()
