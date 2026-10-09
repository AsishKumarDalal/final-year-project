"""Query layer: routing, seeds, walk, rank, refuse, global, citation checks.

Everything runs against fakes: MemoryVectorStore, StubEmbedder, and a scripted
System-1. No network, no key, no model.
"""

from __future__ import annotations

import unittest

from graphrag.embeddings import StubEmbedder
from graphrag.query import (
    QueryEngine,
    RankedEvidence,
    REFUSAL_OUT_OF_SCOPE,
    REFUSAL_UNGROUNDABLE,
    normalise_query,
    validate_source_ids,
)
from graphrag.stores import MemoryVectorStore
from graphrag.system1 import System1Client


class ScriptedSystem1:
    """System1Client whose transport answers from a script."""

    def __init__(self, answers: dict):
        self._answers = answers
        self.calls: list = []

    def __call__(self, url, body, headers, timeout):
        import json as _json
        self.calls.append(_json.loads(body.decode("utf-8")))
        return 200, _json.dumps({"answers": self._answers})


def make_engine(**overrides):
    store = MemoryVectorStore()
    store.ensure_collections()
    embedder = StubEmbedder()
    engine = QueryEngine(vector_store=store, embedder=embedder, **overrides)
    engine.entity_index = {
        "myocardial infarction": {
            "display": "Myocardial infarction", "type": "condition",
            "mentions": 12, "aliases": ["MI", "heart attack"]},
        "chest pain": {
            "display": "Chest pain", "type": "symptom",
            "mentions": 9, "aliases": []},
        "aspirin": {
            "display": "Aspirin", "type": "drug",
            "mentions": 7, "aliases": []},
    }
    engine.chunk_texts = {
        "mi::0": "Acute myocardial infarction presenting with severe chest pain.",
        "mi::1": "Aspirin is administered for suspected myocardial infarction.",
        "mi::2": "Troponin testing confirms myocardial infarction.",
    }
    engine.adjacency = {
        "myocardial infarction": [
            ("PRESENTS_WITH", "chest pain", 5, ["mi::0"]),
            ("TESTS_FOR_REV", "troponin", 2, ["mi::2"]),
        ],
        "chest pain": [("SYMPTOM_OF_REV", "myocardial infarction", 5, ["mi::0"])],
        "aspirin": [("TREATS", "myocardial infarction", 3, ["mi::1"])],
    }
    return engine, store


def seed_vectors(engine, store):
    texts = ["Myocardial infarction — condition — 12 mentions aliases: MI, heart attack",
             "Chest pain — symptom — 9 mentions",
             "Aspirin — drug — 7 mentions"]
    vectors = engine.embedder.embed(texts)
    store.upsert("entities", ["e1", "e2", "e3"], vectors,
                 [{"entity_key": "myocardial infarction"},
                  {"entity_key": "chest pain"},
                  {"entity_key": "aspirin"}])
    chunk_texts = [engine.chunk_texts[k] for k in ("mi::0", "mi::1", "mi::2")]
    store.upsert("text_units", ["u0", "u1", "u2"],
                 engine.embedder.embed(chunk_texts),
                 [{"text_unit_id": "mi::0"}, {"text_unit_id": "mi::1"},
                  {"text_unit_id": "mi::2"}])


class TestQueryNormalise(unittest.TestCase):
    def test_stopwords_dropped(self):
        tokens = normalise_query("What is the treatment for chest pain?")
        self.assertIn("chest", tokens)
        self.assertNotIn("what", tokens)


class TestRouting(unittest.TestCase):
    def test_no_system1_defaults_to_local(self):
        engine, _ = make_engine(system1=None)
        self.assertEqual(engine.route("anything"), ("local", 0.0))

    def test_system1_mode_and_confidence(self):
        client = System1Client(
            transport=ScriptedSystem1(
                {"search_mode": {"type": "choice",
                                 "probabilities": {"local": 0.8, "global": 0.1,
                                                   "basic": 0.05, "none": 0.05}}}))
        engine, _ = make_engine(system1=client)
        self.assertEqual(engine.route("chest pain?"), ("local", 0.8))

    def test_router_failure_defaults_to_local(self):
        client = System1Client(transport=lambda *a: (None, ""))
        engine, _ = make_engine(system1=client)
        self.assertEqual(engine.route("x"), ("local", 0.0))

    def test_none_mode_refuses(self):
        client = System1Client(
            transport=ScriptedSystem1(
                {"search_mode": {"type": "choice",
                                 "probabilities": {"none": 0.95, "local": 0.05}}}))
        engine, _ = make_engine(system1=client)
        result = engine.ask("tell me a joke")
        self.assertIn(REFUSAL_OUT_OF_SCOPE, result.refusal or "")
        self.assertEqual(result.facts, [])


class TestSeeds(unittest.TestCase):
    def test_name_door_finds_exact_and_alias(self):
        engine, _ = make_engine()
        seeds = engine.door_names("What is myocardial infarction?")
        self.assertIn("myocardial infarction", seeds)

    def test_vector_door_wiring(self):
        """Door 2 mechanics with the hash stub: identical text matches at 1.0
        (payload maps back to the entity key); unrelated text scores ~0 and is
        gated out. Paraphrase matching is a property of the real MiniLM,
        verified live — not something a deterministic stub can exhibit."""
        engine, store = make_engine()
        seed_vectors(engine, store)
        probe = engine.embedder.embed(
            ["Myocardial infarction — condition — 12 mentions aliases: MI, heart attack"])[0]
        seeds = engine.door_vectors(probe)
        self.assertIn("myocardial infarction", seeds)
        far = engine.probe("xylophone repair manual page nine")
        self.assertEqual(engine.door_vectors(far), [])

    def test_union(self):
        engine, store = make_engine()
        seed_vectors(engine, store)
        seeds = engine.seeds("aspirin for MI", engine.probe("aspirin for MI"))
        self.assertIn("aspirin", seeds)
        self.assertIn("myocardial infarction", seeds)


class TestWalkAndFetch(unittest.TestCase):
    def test_walk_collects_typed_evidence_with_provenance(self):
        engine, _ = make_engine()
        evidence = engine.walk(["myocardial infarction"])
        self.assertTrue(evidence)
        self.assertTrue(all(e.source_id.startswith("text_unit:") for e in evidence))
        self.assertTrue(any("PRESENTS_WITH" in e.text for e in evidence))

    def test_fetch_returns_cited_chunks(self):
        engine, store = make_engine()
        seed_vectors(engine, store)
        evidence = engine.fetch_chunks(engine.probe("chest pain"), top_k=2)
        self.assertEqual(len(evidence), 2)
        self.assertTrue(all(e.source_id.startswith("text_unit:") for e in evidence))


class TestRank(unittest.TestCase):
    def test_cosine_only_without_system1(self):
        engine, store = make_engine(system1=None)
        seed_vectors(engine, store)
        evidence = engine.fetch_chunks(engine.probe("troponin testing"), top_k=3)
        ranked = engine.rank("troponin testing", engine.probe("troponin testing"),
                             evidence)
        self.assertLessEqual(len(ranked), engine.top_k_evidence)
        scores = [e.final_score for e in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_system1_blend(self):
        answers = {f"cand_{i}": {"type": "score",
                                 "probabilities": {"0": 0, "1": 0, "2": 0,
                                                   "3": 0, "4": 1}}
                   for i in range(3)}
        client = System1Client(transport=ScriptedSystem1(answers))
        engine, store = make_engine(system1=client)
        seed_vectors(engine, store)
        evidence = engine.fetch_chunks(engine.probe("chest pain"), top_k=3)
        ranked = engine.rank("chest pain", engine.probe("chest pain"), evidence)
        self.assertTrue(all(e.system1_score == 1.0 for e in ranked))

    def test_system1_failure_degrades_to_cosine(self):
        client = System1Client(transport=lambda *a: (None, ""))
        engine, store = make_engine(system1=client)
        seed_vectors(engine, store)
        evidence = engine.fetch_chunks(engine.probe("chest pain"), top_k=2)
        ranked = engine.rank("chest pain", engine.probe("chest pain"), evidence)
        self.assertTrue(all(e.final_score == e.cosine_score for e in ranked))


class TestAsk(unittest.TestCase):
    def test_local_end_to_end_offline(self):
        engine, store = make_engine(system1=None)
        seed_vectors(engine, store)
        result = engine.ask("What presents with chest pain?", mode="local")
        self.assertIsNone(result.refusal)
        self.assertEqual(result.search_mode, "local")
        self.assertTrue(result.source_ids)
        self.assertTrue(all(s.startswith("text_unit:") for s in result.source_ids))

    def test_basic_end_to_end_offline(self):
        engine, store = make_engine(system1=None)
        seed_vectors(engine, store)
        result = engine.ask("troponin testing", mode="basic")
        self.assertIsNone(result.refusal)
        self.assertEqual(result.search_mode, "basic")

    def test_ungroundable_refuses(self):
        engine, store = make_engine(system1=None)
        result = engine.ask("xylophone repair", mode="local")
        self.assertIn(REFUSAL_UNGROUNDABLE, result.refusal or "")

    def test_global_without_reports_refuses(self):
        engine, _ = make_engine(system1=None)
        result = engine.ask("themes?", mode="global")
        self.assertIn(REFUSAL_UNGROUNDABLE, result.refusal or "")

    def test_global_with_reports(self):
        engine, _ = make_engine(system1=None)
        engine.community_reports = {
            "L0_0000": {"summary": "cardiac conditions and their symptoms",
                        "member_text_units": ["mi::0", "mi::1"]},
            "L0_0001": {"summary": "respiratory conditions",
                        "member_text_units": ["mi::2"]},
        }
        result = engine.ask("cardiac themes?", mode="global")
        self.assertIsNone(result.refusal)
        self.assertTrue(result.generated_interpretation)
        self.assertTrue(all(s.startswith("community:") for s in result.source_ids))


class TestCitationValidation(unittest.TestCase):
    def test_resolvable_passes(self):
        from graphrag.query import QueryResult
        result = QueryResult(facts=["x"], source_ids=["text_unit:mi::0"])
        self.assertEqual(validate_source_ids(result, {"mi::0", "mi::1"}), [])

    def test_unresolvable_is_flagged(self):
        from graphrag.query import QueryResult
        result = QueryResult(facts=["x"], source_ids=["text_unit:nope::9"])
        problems = validate_source_ids(result, {"mi::0"})
        self.assertTrue(any("unresolvable" in p for p in problems))

    def test_community_cited_as_fact_is_flagged(self):
        from graphrag.query import QueryResult
        result = QueryResult(facts=["x"], source_ids=["community:L0_0000"])
        problems = validate_source_ids(result, {"mi::0"})
        self.assertTrue(any("generated text" in p for p in problems))

    def test_malformed_flagged(self):
        from graphrag.query import QueryResult
        result = QueryResult(facts=["x"], source_ids=["entity:tesla"])
        self.assertTrue(validate_source_ids(result, {"mi::0"}))


class TestMemoryStore(unittest.TestCase):
    def test_upsert_overwrites(self):
        store = MemoryVectorStore()
        store.ensure_collections()
        store.upsert("entities", ["a"], [[1.0] * 384], [{"k": 1}])
        store.upsert("entities", ["a"], [[1.0] * 384], [{"k": 2}])
        self.assertEqual(store.count("entities"), 1)
        self.assertEqual(store.search("entities", [1.0] * 384)[0].payload["k"], 2)

    def test_payload_filter(self):
        store = MemoryVectorStore()
        store.ensure_collections()
        store.upsert("text_units", ["a", "b"], [[1.0] * 384, [1.0] * 384],
                     [{"doc_id": "x"}, {"doc_id": "y"}])
        hits = store.search("text_units", [1.0] * 384,
                            payload_filter={"doc_id": "y"})
        self.assertEqual([h.id for h in hits], ["b"])

    def test_stable_point_ids(self):
        from graphrag.stores import stable_point_id
        self.assertEqual(stable_point_id("entities", "tesla"),
                         stable_point_id("entities", "tesla"))
        self.assertNotEqual(stable_point_id("entities", "tesla"),
                            stable_point_id("text_units", "tesla"))


class TestPipelineHelpers(unittest.TestCase):
    def test_pipeline_end_to_end_with_stubs(self):
        """Indexing stages I1–I8 on temp dirs with a stub extractor."""
        import tempfile
        from pathlib import Path as _Path
        from graphrag.checkpoint import ExtractionCheckpoint
        from graphrag.chunking import chunk_document
        from graphrag.pipeline import build_graph_from_checkpoint, run_extraction
        from graphrag.graph import Graph
        from graphrag.cost import CostLedger

        with tempfile.TemporaryDirectory() as tmp:
            root = _Path(tmp)
            checkpoint = ExtractionCheckpoint(
                triples_path=root / "triples.jsonl", cache_dir=root / "cache",
                failed_path=root / "failed.json")
            chunks = chunk_document(
                "MI Note",
                "Myocardial infarction presents with chest pain. " * 30,
                target_chars=400, overlap_chars=40)

            class StubExtractor:
                def extract_batch(self, texts, *, max_triples):
                    return [{"entities": [{"name": "Myocardial infarction",
                                           "type": "condition"}],
                             "triples": [{"head": "Myocardial infarction",
                                          "relation": "PRESENTS_WITH",
                                          "tail": "Chest pain"}]}
                            for _ in texts]

            counts = run_extraction(chunks, checkpoint, StubExtractor(),  # type: ignore[arg-type]
                                    max_triples=5, batch_size=2, workers=2)
            self.assertEqual(counts["extracted"], len(chunks))
            # Second run: everything already paid for.
            counts2 = run_extraction(chunks, checkpoint, StubExtractor(),  # type: ignore[arg-type]
                                     max_triples=5, batch_size=2, workers=2)
            self.assertEqual(counts2["pending"], 0)

            graph = Graph()
            built = build_graph_from_checkpoint(checkpoint, graph)
            self.assertGreater(built["triples_added"], 0)
            self.assertIn("myocardial infarction", graph.nodes)
            self.assertEqual(graph.dangling_edges(), [])
            _ = CostLedger(model="stub")


if __name__ == "__main__":
    unittest.main()