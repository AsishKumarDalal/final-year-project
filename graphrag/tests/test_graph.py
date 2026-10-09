"""Graph aggregation, provenance, pruning, type backfill, persistence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graphrag.graph import Graph


def build_tesla_graph() -> Graph:
    graph = Graph()
    graph.add_triple(
        "Elon Musk", "CEO_OF", "Tesla, Inc.", source_chunk="tesla::1"
    )
    graph.add_triple(
        "Elon Musk", "CEO_OF", "Tesla Inc", source_chunk="tesla::4"
    )
    return graph


class TestAggregation(unittest.TestCase):
    def test_same_edge_merges_into_one(self):
        graph = build_tesla_graph()
        self.assertEqual(len(graph.edges), 1)
        edge = next(iter(graph.edges.values()))
        self.assertEqual(edge.mentions, 2)
        self.assertEqual(edge.source_chunks, {"tesla::1", "tesla::4"})

    def test_company_name_variants_fold_into_one_node(self):
        """Merge keys are the normalised, space-separated form (graph.json uses
        ``"elon musk"``), while the display name keeps the full surface."""
        graph = build_tesla_graph()
        self.assertIn("elon musk", graph.nodes)
        self.assertIn("tesla", graph.nodes)
        self.assertEqual(len(graph.nodes), 2)

    def test_longest_surface_wins_the_display_slot(self):
        """The display must not flap between 'Tesla, Inc.' and 'Tesla'."""
        graph = build_tesla_graph()
        node = graph.nodes["tesla"]
        self.assertEqual(node.display, "Tesla, Inc.")
        self.assertIn("Tesla Inc", node.aliases)

    def test_mentions_are_free_confidence(self):
        graph = build_tesla_graph()
        self.assertEqual(graph.nodes["elon musk"].mentions, 2)
        self.assertEqual(graph.nodes["tesla"].mentions, 2)


class TestRejections(unittest.TestCase):
    def test_rejection_reason_is_recorded_not_silent(self):
        graph = Graph()
        result = graph.add_triple("Tesla", "COMPETES_WITH", "other automakers")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "vague_entity")
        self.assertEqual(len(graph.edges), 0)
        self.assertEqual(graph.rejected["vague_entity"], 1)

    def test_self_loop_rejected(self):
        graph = Graph()
        self.assertEqual(graph.add_triple("Tesla", "X", "Tesla").reason, "self_loop")

    def test_compound_name_is_expanded_not_rejected(self):
        """The extraction is compressed, not wrong — so split rather than drop."""
        graph = Graph()
        result = graph.add_triple(
            "Tesla and SpaceX", "COMPETES_WITH", "Rivian",
            source_chunk="c::1",
        )
        self.assertIn(result.status, ("added", "merged"))
        self.assertIn("tesla", graph.nodes)
        self.assertIn("spacex", graph.nodes)
        self.assertIn("rivian", graph.nodes)


class TestPruning(unittest.TestCase):
    def test_weight_one_edges_are_dropped(self):
        graph = Graph()
        graph.add_triple("A1", "R", "B1", source_chunk="c::1")
        graph.add_triple("A1", "R", "B1", source_chunk="c::2")
        graph.add_triple("C1", "R", "D1", source_chunk="c::3")
        dropped = graph.prune_min_mentions(2)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(graph.edges), 1)

    def test_pruning_keeps_real_relationships(self):
        graph = build_tesla_graph()
        self.assertEqual(graph.prune_min_mentions(2), 0)
        self.assertEqual(len(graph.edges), 1)


class TestTypeBackfill(unittest.TestCase):
    def test_repeated_edge_weighs_enough_to_retype(self):
        """solution.md failure 2: a one-off inverted edge typed a node PERSON
        and blocked a legitimate merge. One-sighting edges must not vote, and a
        heavily-repeated edge must count for more than a single mention."""
        graph = Graph()
        graph.add_triple("Aspirin", "TREATS", "Myocardial infarction", source_chunk="c::1")
        graph.add_triple("Aspirin", "TREATS", "Myocardial infarction", source_chunk="c::2")
        graph.backfill_types(min_mentions=2)
        self.assertEqual(graph.nodes["aspirin"].type, "drug")

    def test_single_sighting_edge_does_not_vote(self):
        graph = Graph()
        graph.add_triple("Amoxicillin", "TREATS", "Infection", source_chunk="c::1")
        graph.backfill_types(min_mentions=2)
        self.assertEqual(graph.nodes["amoxicillin"].type, "condition")

    def test_explicit_extraction_type_outranks_the_backfill(self):
        graph = Graph()
        graph.register_entities([{"name": "Aspirin", "type": "drug"}])
        graph.add_triple("Aspirin", "TREATS", "Infection", source_chunk="c::1")
        graph.add_triple("Aspirin", "TREATS", "Infection", source_chunk="c::2")
        graph.backfill_types(min_mentions=2)
        self.assertEqual(graph.nodes["aspirin"].type, "drug")


class TestProvenance(unittest.TestCase):
    def test_every_edge_resolves_to_a_chunk(self):
        graph = build_tesla_graph()
        self.assertEqual(graph.resolvable_text_units(), {"tesla::1", "tesla::4"})

    def test_no_dangling_edges(self):
        graph = build_tesla_graph()
        self.assertEqual(graph.dangling_edges(), [])

    def test_provenance_is_never_lost_on_merge(self):
        graph = Graph()
        for i in range(5):
            graph.add_triple("Chest pain", "SYMPTOM_OF", "MI", source_chunk=f"c::{i}")
        edge = next(iter(graph.edges.values()))
        self.assertEqual(len(edge.source_chunks), 5)


class TestPersistence(unittest.TestCase):
    def test_round_trip(self):
        graph = build_tesla_graph()
        graph.add_triple("Troponin", "TESTS_FOR", "MI", source_chunk="tesla::7")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graph.json"
            graph.save(path)
            reloaded = Graph.load(path)
        self.assertEqual(len(reloaded.nodes), len(graph.nodes))
        self.assertEqual(len(reloaded.edges), len(graph.edges))
        self.assertEqual(
            reloaded.resolvable_text_units(), graph.resolvable_text_units()
        )
        self.assertEqual(reloaded.rejected, graph.rejected)

    def test_saved_file_is_valid_json_with_stats(self):
        graph = build_tesla_graph()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graph.json"
            graph.save(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("stats", payload)
        self.assertEqual(payload["stats"]["nodes"], 2)


class TestStats(unittest.TestCase):
    def test_counts_are_reported(self):
        graph = build_tesla_graph()
        stats = graph.stats()
        self.assertEqual(stats["nodes"], 2)
        self.assertEqual(stats["edges"], 1)
        self.assertEqual(stats["edges_with_provenance"], 1)


if __name__ == "__main__":
    unittest.main()