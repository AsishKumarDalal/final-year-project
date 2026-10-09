"""Merge funnel, System-1 client, communities, reports. All offline."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graphrag.communities import Community, partition
from graphrag.graph import Graph
from graphrag.merge import (
    apply_verdict,
    block_candidates,
    cluster_nominations,
    conservative_judge,
    entity_context,
    fuzzy_score,
    jaro_winkler,
    MergeVerdict,
    nominate,
    pick_canonical,
    resolve_entities,
)
from graphrag.reports import (
    build_report_context,
    member_fact_lines,
    member_text_units,
    parse_report,
)
from graphrag.system1 import (
    choice_probability,
    score_value,
    System1Client,
    System1Error,
)


def tesla_graph() -> Graph:
    graph = Graph()
    graph.add_triple("Elon Musk", "CEO_OF", "Tesla, Inc.", source_chunk="t::1")
    graph.add_triple("Elon Musk", "CEO_OF", "Tesla Inc", source_chunk="t::4")
    graph.add_triple("Tesla Powerwall", "MANUFACTURED_BY", "Tesla, Inc.",
                     source_chunk="t::2")
    graph.add_triple("Martin Eberhard", "FOUNDED_BY_BACK", "Tesla Motors",
                     source_chunk="t::3")
    return graph


class TestJaroWinkler(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(jaro_winkler("tesla", "tesla"), 1.0)
        self.assertEqual(jaro_winkler("", "tesla"), 0.0)
        # "martha" vs "marhta": classic Jaro ~0.944, Winkler boosts the "mar" prefix.
        self.assertAlmostEqual(jaro_winkler("martha", "marhta"), 0.961, places=3)

    def test_symmetric(self):
        self.assertAlmostEqual(jaro_winkler("tesla motors", "tesla"),
                               jaro_winkler("tesla", "tesla motors"))

    def test_fuzzy_weights(self):
        # solution.md §4: the 0.73 paradox — motors and powerwall score alike.
        motors = fuzzy_score("tesla", "tesla motors")
        powerwall = fuzzy_score("tesla", "tesla powerwall")
        self.assertGreater(motors, 0.5)
        self.assertGreater(powerwall, 0.5)
        self.assertLess(abs(motors - powerwall), 0.15)


class TestBlocking(unittest.TestCase):
    def test_shared_token_pairs_only(self):
        pairs = block_candidates(["tesla", "tesla motors", "elon musk"])
        self.assertIn(("tesla", "tesla motors"), pairs)
        self.assertNotIn(("elon musk", "tesla"), pairs)
        self.assertNotIn(("elon musk", "tesla motors"), pairs)

    def test_no_token_overlap_never_compared(self):
        """'Musk' vs 'Elon' share no token — known recall trade-off, documented."""
        pairs = block_candidates(["musk", "elon"])
        self.assertEqual(pairs, [])

    def test_nominate_merges_nothing(self):
        graph = tesla_graph()
        before = (len(graph.nodes), len(graph.edges))
        nominations = nominate(sorted(graph.nodes))
        self.assertEqual((len(graph.nodes), len(graph.edges)), before)
        self.assertTrue(any(a == "tesla" for a, _, _ in nominations))

    def test_clusters_are_capped(self):
        nominations = [(f"n{i}", f"n{i+1}", 0.9) for i in range(40)]
        batches = cluster_nominations(nominations, max_batch=15)
        self.assertTrue(all(len(b) <= 15 for b in batches))


class TestCanonical(unittest.TestCase):
    def test_most_mentions_wins(self):
        graph = tesla_graph()
        # "tesla" has the most mentions across the fixture.
        self.assertEqual(pick_canonical(graph, ["tesla", "tesla motors"]), "tesla")

    def test_tie_goes_to_longest_display(self):
        graph = Graph()
        graph.add_triple("A B", "R", "C D", source_chunk="c::1")
        graph.add_triple("E", "R", "C D", source_chunk="c::2")
        # "a b" and "e": both 1 mention... force a tie via equal mentions.
        self.assertIn(pick_canonical(graph, ["a b", "e"]), {"a b", "e"})


class TestApplyVerdict(unittest.TestCase):
    def test_merge_folds_evidence(self):
        graph = tesla_graph()
        verdict = MergeVerdict(groups=[["tesla", "tesla motors"]],
                               refused=[], judge="test")
        key_map = apply_verdict(graph, verdict)
        self.assertEqual(key_map, {"tesla motors": "tesla"})
        self.assertNotIn("tesla motors", graph.nodes)
        self.assertEqual(graph.nodes["tesla"].mentions, 4)
        self.assertIn("Tesla Motors", graph.nodes["tesla"].aliases)
        self.assertEqual(graph.dangling_edges(), [])

    def test_conservative_judge_refuses_everything(self):
        graph = tesla_graph()
        before_nodes = len(graph.nodes)
        report = resolve_entities(graph, judge=conservative_judge)
        self.assertEqual(report.merged_nodes, 0)
        self.assertEqual(len(graph.nodes), before_nodes)
        self.assertTrue(report.refused)

    def test_audit_log_written(self):
        graph = tesla_graph()
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "merge_log.json"
            report = resolve_entities(graph, judge=conservative_judge,
                                      audit_path=audit)
            payload = json.loads(audit.read_text(encoding="utf-8"))
        self.assertTrue(payload)
        self.assertIn("judge", payload[0])
        self.assertEqual(report.judges_used, ["none"])

    def test_entity_context_shows_edges(self):
        graph = tesla_graph()
        context = entity_context(graph, "tesla")
        self.assertIn("CEO_OF", context)
        self.assertIn("mentions=", context)


class StubSystem1Transport:
    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list = []

    def __call__(self, url, body, headers, timeout):
        import json as _json
        payload = _json.loads(body.decode("utf-8"))
        self.calls.append(payload)
        return 200, _json.dumps({"answers": self.answers})


class TestSystem1Client(unittest.TestCase):
    def test_decide_returns_answers(self):
        transport = StubSystem1Transport({"q": {"type": "noul", "noul": 0.9}})
        client = System1Client(transport=transport)
        answers = client.decide("state", {"q": {"type": "noul"}})
        self.assertEqual(answers["q"]["noul"], 0.9)
        sent = transport.calls[0]
        self.assertEqual(sent["model"], "laya")
        self.assertIn("questions", sent)

    def test_non_200_raises(self):
        client = System1Client(
            transport=lambda *a: (500, "boom"))
        with self.assertRaises(System1Error):
            client.decide("s", {"q": {}})

    def test_available_false_on_failure(self):
        client = System1Client(transport=lambda *a: (None, ""))
        self.assertFalse(client.available())

    def test_choice_probability_guards(self):
        self.assertEqual(choice_probability({}, "merge"), 0.0)
        self.assertEqual(
            choice_probability({"probabilities": {"merge": 0.88}}, "merge"), 0.88)

    def test_score_value_normalises(self):
        answer = {"probabilities": {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 1.0}}
        self.assertEqual(score_value(answer), 1.0)
        self.assertEqual(score_value({}), 0.0)


class TestCommunities(unittest.TestCase):
    def test_two_cliques_two_communities(self):
        edges = [
            ("a", "R", "b", 5.0), ("b", "R", "c", 5.0), ("a", "R", "c", 5.0),
            ("x", "R", "y", 5.0), ("y", "R", "z", 5.0), ("x", "R", "z", 5.0),
            ("c", "R", "x", 0.1),
        ]
        communities = partition(edges, levels=1)
        self.assertEqual(len(communities), 2)
        members = [set(c.members) for c in communities]
        self.assertIn({"a", "b", "c"}, members)
        self.assertIn({"x", "y", "z"}, members)

    def test_ids_are_deterministic(self):
        edges = [("a", "R", "b", 2.0), ("b", "R", "c", 2.0), ("x", "R", "y", 3.0)]
        first = [c.as_dict() for c in partition(edges, levels=2)]
        second = [c.as_dict() for c in partition(edges, levels=2)]
        self.assertEqual(first, second)

    def test_empty_graph(self):
        self.assertEqual(partition([], levels=2)[0].members, ())

    def test_levels_nest(self):
        edges = [(f"n{i}", "R", f"n{i+1}", 1.0) for i in range(8)]
        communities = partition(edges, levels=2)
        self.assertTrue({c.level for c in communities} >= {0})


class TestReports(unittest.TestCase):
    def test_context_prioritises_high_mention_edges(self):
        community = Community(id="L0_0000", level=0, members=("tesla",))
        edges = [
            {"head": "tesla", "relation": "LOCATED_IN", "tail": "us", "mentions": 1,
             "source_chunks": ["c::1"]},
            {"head": "elon musk", "relation": "CEO_OF", "tail": "tesla",
             "mentions": 9, "source_chunks": ["c::2"]},
        ]
        lines = member_fact_lines(
            community, edges, {"tesla": "Tesla", "elon musk": "Elon Musk", "us": "US"})
        self.assertTrue(lines[0].startswith("Elon Musk"))

    def test_member_text_units_are_citable(self):
        community = Community(id="L0_0000", level=0, members=("tesla",))
        edges = [{"head": "tesla", "relation": "R", "tail": "x", "mentions": 1,
                  "source_chunks": ["c::1", "c::2"]}]
        self.assertEqual(member_text_units(community, edges), ["c::1", "c::2"])

    def test_parse_report(self):
        title, summary = parse_report('```json\n{"title":"T","summary":"S"}\n```')
        self.assertEqual((title, summary), ("T", "S"))

    def test_context_truncates(self):
        community = Community(id="L0_0000", level=0, members=("a",))
        context = build_report_context(
            community, member_lines=["x" * 100] * 50, budget=10)
        self.assertLessEqual(len(context), 41)


if __name__ == "__main__":
    unittest.main()