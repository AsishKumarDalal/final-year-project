"""Tool tests (S4). No network. Role matrix, not_found never guess, citations.
"""
from __future__ import annotations

import unittest

from medharness.tools.registry import PermissionDenied, dispatch
from medharness.tools.lookup import get_drug_interactions, get_lab_ref


class LabTests(unittest.TestCase):
    def test_found(self):
        r = get_lab_ref("troponin")
        self.assertTrue(r.facts)
        self.assertEqual(r.source_type, "lab_table")
        self.assertTrue(r.source_ids)
        self.assertFalse(r.not_found)

    def test_not_found(self):
        r = get_lab_ref("unobtainium")
        self.assertTrue(r.not_found)
        self.assertEqual(r.facts, [])
        self.assertEqual(r.source_ids, [])

    def test_drug_interaction_found(self):
        r = get_drug_interactions(["aspirin", "warfarin"])
        self.assertTrue(r.facts)
        self.assertTrue(any("major" in f for f in r.facts))

    def test_drug_interaction_not_found(self):
        r = get_drug_interactions(["aspirin", "paracetamol"])
        self.assertTrue(r.not_found)


class PermissionTests(unittest.TestCase):
    def test_patient_may_call_labs(self):
        r = dispatch("get_lab_ref", "patient", {"test_name": "glucose_fasting"})
        self.assertTrue(r.facts)

    def test_unknown_tool_denied(self):
        with self.assertRaises(PermissionDenied):
            dispatch("run_command", "doctor", {"command": "rm -rf /"})

    def test_unknown_role_denied(self):
        with self.assertRaises(PermissionDenied):
            dispatch("get_lab_ref", "hacker", {"test_name": "troponin"})

    def test_dispatch_chokepoint_returns_toolfact(self):
        r = dispatch("get_drug_interactions", "nurse", {"drugs": ["ibuprofen", "warfarin"]})
        self.assertEqual(r.source_type, "drug_table")


class KbTests(unittest.TestCase):
    def test_kb_search_cites_text_units(self):
        r = dispatch("kb_search", "patient", {"query": "chest pain"})
        self.assertTrue(r.facts)
        self.assertTrue(all(s.startswith("text_unit:") for s in r.source_ids))

    def test_kb_search_no_community_ids(self):
        r = dispatch("kb_search", "patient", {"query": "stroke"})
        self.assertFalse(any("community:" in s for s in r.source_ids))


if __name__ == "__main__":
    unittest.main(verbosity=2)
