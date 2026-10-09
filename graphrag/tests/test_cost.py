"""Cost ledger accounting. Pure arithmetic — no network, no fixtures."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graphrag.cost import CostLedger


class TestRecording(unittest.TestCase):
    def test_empty_ledger(self):
        ledger = CostLedger(model="m")
        summary = ledger.summary()
        self.assertEqual(summary["total_calls"], 0)
        self.assertEqual(summary["total_tokens"], 0)
        self.assertEqual(summary["model"], "m")

    def test_record_accumulates_per_stage(self):
        ledger = CostLedger(model="m")
        ledger.record(stage="extract", prompt_tokens=100, completion_tokens=50,
                      reasoning_tokens=30, latency_s=2.0)
        ledger.record(stage="extract", prompt_tokens=100, completion_tokens=50,
                      reasoning_tokens=30, latency_s=4.0)
        ledger.record(stage="reports", prompt_tokens=10, completion_tokens=5,
                      latency_s=1.0)
        summary = ledger.summary()
        self.assertEqual(summary["total_calls"], 3)
        self.assertEqual(summary["total_tokens"], 100 + 50 + 100 + 50 + 10 + 5)
        extract = summary["stages"]["extract"]
        self.assertEqual(extract["calls"], 2)
        self.assertEqual(extract["prompt_tokens"], 200)
        self.assertEqual(extract["reasoning_tokens"], 60)
        # text_tokens excludes reasoning — what was actually emitted.
        self.assertEqual(extract["text_tokens"], 100 - 60)
        self.assertEqual(extract["mean_latency_s"], 3.0)

    def test_negative_values_are_clamped(self):
        ledger = CostLedger()
        ledger.record(stage="x", prompt_tokens=-5, completion_tokens=-1,
                      reasoning_tokens=-2, latency_s=-1.0)
        summary = ledger.summary()
        self.assertEqual(summary["total_tokens"], 0)


class TestPersistence(unittest.TestCase):
    def test_round_trip(self):
        ledger = CostLedger(model="space-bunny-free")
        ledger.record(stage="extract", prompt_tokens=244, completion_tokens=498,
                      reasoning_tokens=429, latency_s=12.9)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rag-cost.json"
            ledger.save(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("stages", payload)
            reloaded = CostLedger.load(path)
        self.assertEqual(reloaded.summary(), ledger.summary())


if __name__ == "__main__":
    unittest.main()