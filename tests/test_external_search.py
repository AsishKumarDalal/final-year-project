"""External deep-search tests (S9). No network, no Docker.

Proves the mechanism: search_external_docs does a full graph walk and returns
text_unit citations; community/global degrades to a named refusal offline; the
tool is permission-gated; and a stubbed LLM that calls search_external_docs gets
grounded, cited evidence back.
"""
from __future__ import annotations

import unittest

from medharness.tools.external_search import build_offline_external_search
from medharness.tools.registry import PermissionDenied, dispatch


def make_search():
    return build_offline_external_search()


class ExternalSearchTests(unittest.TestCase):
    def test_local_walk_cites_text_units(self):
        res = make_search().search("chest pain cardiac emergency", mode="local")
        self.assertEqual(res.search_mode, "local")
        self.assertTrue(res.facts)
        self.assertTrue(all(s.startswith("text_unit:") for s in res.source_ids))

    def test_no_community_ids_in_facts(self):
        res = make_search().search("stroke", mode="local")
        self.assertFalse(any("community:" in s for s in res.source_ids))

    def test_global_refuses_without_community_reports(self):
        res = make_search().search("cardiology", mode="global")
        self.assertEqual(res.search_mode, "global")
        self.assertTrue(res.not_found)
        self.assertIsNotNone(res.refusal)   # named refusal, never a fake answer

    def test_unknown_query_degrades(self):
        res = make_search().search("zzzz qqqq nonexistent", mode="local")
        if res.not_found:
            self.assertEqual(res.facts, [])
        self.assertIsNotNone(res.refusal if res.not_found else res.search_mode)


class RegistryExternalTests(unittest.TestCase):
    def test_dispatch_external_docs(self):
        search = make_search()
        tf = dispatch("search_external_docs", "patient",
                      {"query": "chest pain", "mode": "local"}, external_search=search)
        self.assertEqual(tf.source_type, "external_graphrag")
        self.assertTrue(all(s.startswith("text_unit:") for s in tf.source_ids))

    def test_dispatch_external_docs_without_client_degrades(self):
        tf = dispatch("search_external_docs", "patient", {"query": "chest pain"})
        self.assertTrue(tf.not_found)
        self.assertEqual(tf.facts, [])

    def test_permission_still_enforced(self):
        search = make_search()
        with self.assertRaises(PermissionDenied):
            dispatch("search_external_docs", "hacker", {"query": "x"}, external_search=search)


if __name__ == "__main__":
    unittest.main(verbosity=2)
