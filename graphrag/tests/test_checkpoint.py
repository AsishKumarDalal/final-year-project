"""Checkpoint resume, content-hash cache, and failure handling."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graphrag.checkpoint import ExtractionCheckpoint


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.ckpt = ExtractionCheckpoint(
            triples_path=root / "triples.jsonl",
            cache_dir=root / "cache",
            failed_path=root / "failed.json",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def record(self, chunk_id: str, content_hash: str = "h1", chunk: str = "C") -> None:
        self.ckpt.record(
            chunk_id=chunk_id,
            doc_id="doc",
            title="Doc",
            content_hash=content_hash,
            result={"entities": [{"name": chunk, "type": "condition"}], "triples": []},
        )


class TestResume(Harness):
    def test_fresh_checkpoint_has_nothing_done(self):
        self.assertEqual(self.ckpt.done_count, 0)
        self.assertFalse(self.ckpt.is_done("a::0"))

    def test_recorded_chunk_is_not_repaid(self):
        self.record("a::0")
        self.assertTrue(self.ckpt.is_done("a::0"))
        self.assertEqual(self.ckpt.done_count, 1)

    def test_second_instance_resumes_from_disk(self):
        """The run is interrupted and restarted — this is the whole point."""
        self.record("a::0")
        self.record("a::1", content_hash="h2")
        reopened = ExtractionCheckpoint(
            triples_path=self.ckpt.triples_path,
            cache_dir=self.ckpt.cache_dir,
            failed_path=self.ckpt.failed_path,
        )
        self.assertEqual(reopened.done_count, 2)
        self.assertTrue(reopened.is_done("a::1"))

    def test_truncated_final_line_does_not_abandon_the_log(self):
        self.record("a::0")
        with self.ckpt.triples_path.open("a", encoding="utf-8") as handle:
            handle.write('{"chunk_id": "a::1", "doc')  # crash mid-write
        reopened = ExtractionCheckpoint(
            triples_path=self.ckpt.triples_path,
            cache_dir=self.ckpt.cache_dir,
            failed_path=self.ckpt.failed_path,
        )
        self.assertEqual(reopened.done_count, 1)
        self.assertFalse(reopened.is_done("a::1"))


class TestContentHashCache(Harness):
    def test_miss_then_hit(self):
        self.assertIsNone(self.ckpt.cache_get("abc"))
        self.record("a::0", content_hash="abc")
        hit = self.ckpt.cache_get("abc")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["entities"][0]["name"], "C")
        self.assertEqual(self.ckpt.stats.cache_hits, 1)

    def test_identical_text_hits_cache_after_a_rebuild(self):
        """Editing merge rules must not re-bill the LLM."""
        self.record("a::0", content_hash="stable")
        self.assertIsNotNone(self.ckpt.cache_get("stable"))

    def test_changed_text_is_a_cache_miss(self):
        self.record("a::0", content_hash="v1")
        self.assertIsNone(self.ckpt.cache_get("v2"))

    def test_cache_entry_records_the_output_contract(self):
        self.record("a::0", content_hash="abc")
        payload = json.loads(
            (self.ckpt.cache_dir / "abc.json").read_text(encoding="utf-8")
        )
        self.assertIn("output_contract", payload)


class TestFailures(Harness):
    def test_failure_is_recorded_with_a_reason(self):
        self.ckpt.record_failure("bad::0", "invalid json after 3 retries")
        failures = self.ckpt.load_failures()
        self.assertEqual(failures["bad::0"], "invalid json after 3 retries")

    def test_failed_chunk_is_never_retried_blindly(self):
        self.ckpt.record_failure("bad::0", "invalid json")
        self.assertTrue(self.ckpt.is_failed("bad::0"))
        self.assertTrue(self.ckpt.is_done("bad::0"))

    def test_failures_survive_a_restart(self):
        self.ckpt.record_failure("bad::0", "timeout")
        reopened = ExtractionCheckpoint(
            triples_path=self.ckpt.triples_path,
            cache_dir=self.ckpt.cache_dir,
            failed_path=self.ckpt.failed_path,
        )
        self.assertTrue(reopened.is_failed("bad::0"))


class TestReplay(Harness):
    def test_iter_results_replays_every_extraction(self):
        """How the graph rebuilds for free after a merge-logic change."""
        self.record("a::0")
        self.record("a::1", content_hash="h2")
        replayed = list(self.ckpt.iter_results())
        self.assertEqual([r["chunk_id"] for r in replayed], ["a::0", "a::1"])


if __name__ == "__main__":
    unittest.main()