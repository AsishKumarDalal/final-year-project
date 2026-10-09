"""Extraction adapter: prompts, parsing, retries, header discipline.

The transport is injected, so every test runs offline. The fake key is
``TEST-KEY`` — never a real secret — and assertions check the *shape* of the
Authorization header, never a value from the environment.
"""

from __future__ import annotations

import json
import os
import unittest
from typing import Any, Mapping
from unittest.mock import patch

from graphrag import llm
from graphrag.config import Settings
from graphrag.cost import CostLedger
from graphrag.llm import (
    AuthError,
    BadRequestError,
    BatchMismatchError,
    Extractor,
    InvalidOutputError,
    RetryExhaustedError,
    build_extraction_prompt,
    parse_batch_output,
)

FAKE_KEY = "TEST-KEY-NOT-REAL"
FAKE_MODEL = "test-model"

GOOD_RESULT = {
    "results": [
        {
            "entities": [{"name": "Acute myocardial infarction", "type": "condition"}],
            "triples": [
                {"head": "Acute myocardial infarction", "relation": "PRESENTS_WITH",
                 "tail": "Severe chest pain"}
            ],
        }
    ]
}


def envelope(content: str, *, prompt_tokens: int = 10, completion_tokens: int = 20,
             reasoning: int = 0) -> str:
    return json.dumps({
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    })


class StubTransport:
    """Scripted (status, body, headers); records every call."""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[tuple[str, bytes, dict, int]] = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, dict(headers), timeout))
        status, text, resp_headers = self.script.pop(0)
        return status, text, resp_headers


def make_extractor(script, **kwargs):
    transport = StubTransport(script)
    sleeps: list[float] = []
    extractor = Extractor(
        base_url="https://example.invalid/v1",
        api_key=FAKE_KEY,
        model_id=FAKE_MODEL,
        transport=transport,
        sleep=sleeps.append,
        backoff_base_s=2.0,
        **kwargs,
    )
    return extractor, transport, sleeps


class TestPrompt(unittest.TestCase):
    def test_rejects_empty_batch(self):
        with self.assertRaises(ValueError):
            build_extraction_prompt([], max_triples=10)

    def test_batch_shape_lists_entity_types_and_counts(self):
        prompt = build_extraction_prompt(["chunk one", "chunk two"], max_triples=7)
        self.assertIn("2 TEXT(s)", prompt)
        self.assertIn("Max 7 triples", prompt)
        self.assertIn("body_system", prompt)
        self.assertIn("TEXT 1:", prompt)
        self.assertIn("TEXT 2:", prompt)
        self.assertIn('{"results":[', prompt.replace(" ", ""))

    def test_rejects_nonpositive_max_triples(self):
        with self.assertRaises(ValueError):
            build_extraction_prompt(["x"], max_triples=0)


class TestParse(unittest.TestCase):
    def test_happy_path(self):
        results = parse_batch_output(json.dumps(GOOD_RESULT), expected=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["triples"][0]["relation"], "PRESENTS_WITH")

    def test_strips_markdown_fences(self):
        fenced = "```json\n" + json.dumps(GOOD_RESULT) + "\n```"
        results = parse_batch_output(fenced, expected=1)
        self.assertEqual(len(results), 1)

    def test_tolerates_surrounding_prose(self):
        messy = "Here you go:\n" + json.dumps(GOOD_RESULT) + "\nDone."
        results = parse_batch_output(messy, expected=1)
        self.assertEqual(len(results), 1)

    def test_empty_completion_rejected(self):
        with self.assertRaises(InvalidOutputError):
            parse_batch_output("   ", expected=1)

    def test_missing_results_rejected(self):
        with self.assertRaises(InvalidOutputError):
            parse_batch_output('{"nope": []}', expected=1)

    def test_batch_mismatch_rejected(self):
        with self.assertRaises(BatchMismatchError):
            parse_batch_output(json.dumps(GOOD_RESULT), expected=2)

    def test_truncated_json_flagged(self):
        partial = json.dumps(GOOD_RESULT)[:120]
        with self.assertRaises(InvalidOutputError) as ctx:
            parse_batch_output(partial, expected=1)
        self.assertIn("truncated", str(ctx.exception).lower())


class TestHeadersAndEndpoint(unittest.TestCase):
    def test_user_agent_and_bearer_are_sent(self):
        extractor, transport, _ = make_extractor(
            [(200, envelope(json.dumps(GOOD_RESULT)), {})]
        )
        extractor.extract_one("some chunk text", max_triples=5)
        url, body, headers, timeout = transport.calls[0]
        self.assertTrue(url.endswith("/chat/completions"), url)
        self.assertEqual(headers["Authorization"], f"Bearer {FAKE_KEY}")
        # The edge rejects default urllib agents with 403/1010 — this must
        # never regress to the library default.
        self.assertIn("graphrag-indexer", headers["User-Agent"])
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["model"], FAKE_MODEL)
        self.assertEqual(payload["temperature"], 0)

    def test_full_completions_url_accepted_as_base(self):
        extractor, transport, _ = make_extractor(
            [(200, envelope(json.dumps(GOOD_RESULT)), {})]
        )
        extractor.base_url = "https://example.invalid/v1"
        extractor.endpoint = "https://example.invalid/v1/chat/completions"
        extractor.extract_one("x", max_triples=5)
        self.assertTrue(transport.calls[0][0].endswith("/chat/completions"))

    def test_missing_key_fails_at_construction(self):
        with self.assertRaises(ValueError):
            Extractor(base_url="https://example.invalid/v1", api_key="",
                      model_id=FAKE_MODEL, transport=StubTransport([]))


class TestRetries(unittest.TestCase):
    def test_429_then_success(self):
        extractor, transport, sleeps = make_extractor([
            (429, "slow down", {"Retry-After": "3"}),
            (200, envelope(json.dumps(GOOD_RESULT)), {}),
        ])
        result = extractor.extract_one("x", max_triples=5)
        self.assertIn("triples", result)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(sleeps, [3.0], "Retry-After must be honoured")

    def test_500_uses_exponential_backoff(self):
        extractor, transport, sleeps = make_extractor([
            (500, "boom", {}),
            (503, "boom", {}),
            (200, envelope(json.dumps(GOOD_RESULT)), {}),
        ])
        extractor.extract_one("x", max_triples=5)
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_transport_failure_is_retried(self):
        extractor, transport, sleeps = make_extractor([
            (None, "", {}),
            (200, envelope(json.dumps(GOOD_RESULT)), {}),
        ])
        extractor.extract_one("x", max_triples=5)
        self.assertEqual(len(transport.calls), 2)

    def test_persistent_500_exhausts_and_raises(self):
        extractor, transport, sleeps = make_extractor(
            [(500, "boom", {})] * 5, max_retries=2
        )
        with self.assertRaises(RetryExhaustedError):
            extractor.extract_one("x", max_triples=5)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_401_is_never_retried(self):
        extractor, transport, sleeps = make_extractor(
            [(401, "bad key", {})]
        )
        with self.assertRaises(AuthError):
            extractor.extract_one("x", max_triples=5)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(sleeps, [])

    def test_403_is_never_retried(self):
        extractor, transport, sleeps = make_extractor(
            [(403, "error code: 1010", {})]
        )
        with self.assertRaises(AuthError):
            extractor.extract_one("x", max_triples=5)
        self.assertEqual(len(transport.calls), 1)

    def test_400_is_never_retried(self):
        extractor, transport, sleeps = make_extractor(
            [(400, "bad payload", {})]
        )
        with self.assertRaises(BadRequestError):
            extractor.extract_one("x", max_triples=5)
        self.assertEqual(len(transport.calls), 1)


class TestLedgerWiring(unittest.TestCase):
    def test_usage_is_recorded(self):
        ledger = CostLedger(model=FAKE_MODEL)
        extractor, _, _ = make_extractor(
            [(200, envelope(json.dumps(GOOD_RESULT), prompt_tokens=244,
                            completion_tokens=498, reasoning=429), {})],
            ledger=ledger,
        )
        extractor.extract_one("x", max_triples=5)
        summary = ledger.summary()
        self.assertEqual(summary["total_calls"], 1)
        self.assertEqual(summary["stages"]["extract"]["reasoning_tokens"], 429)
        self.assertEqual(summary["stages"]["extract"]["text_tokens"], 498 - 429)


class TestReasoningControl(unittest.TestCase):
    def test_effort_sent_by_default(self):
        extractor, transport, _ = make_extractor(
            [(200, envelope(json.dumps(GOOD_RESULT)), {})]
        )
        extractor.extract_one("x", max_triples=5)
        payload = json.loads(transport.calls[0][1].decode("utf-8"))
        self.assertEqual(payload.get("reasoning_effort"), "minimal")

    def test_unset_sends_no_parameter(self):
        extractor, transport, _ = make_extractor(
            [(200, envelope(json.dumps(GOOD_RESULT)), {})],
            reasoning_effort=None,
        )
        extractor.extract_one("x", max_triples=5)
        payload = json.loads(transport.calls[0][1].decode("utf-8"))
        self.assertNotIn("reasoning_effort", payload)

    def test_400_on_reasoning_falls_back_without_it(self):
        extractor, transport, _ = make_extractor([
            (400, "reasoning_effort is not supported", {}),
            (200, envelope(json.dumps(GOOD_RESULT)), {}),
        ])
        result = extractor.extract_one("x", max_triples=5)
        self.assertIn("triples", result)
        second = json.loads(transport.calls[1][1].decode("utf-8"))
        self.assertNotIn("reasoning_effort", second)

    def test_400_unrelated_is_not_retried(self):
        extractor, transport, _ = make_extractor(
            [(400, "malformed payload", {})]
        )
        with self.assertRaises(BadRequestError):
            extractor.extract_one("x", max_triples=5)
        self.assertEqual(len(transport.calls), 1)


class TestSalvage(unittest.TestCase):
    def test_truncated_output_retried_with_doubled_budget(self):
        partial = json.dumps(GOOD_RESULT)[:120]
        extractor, transport, _ = make_extractor([
            (200, envelope(partial), {}),
            (200, envelope(json.dumps(GOOD_RESULT)), {}),
        ], max_tokens=500)
        result = extractor.extract_one("x", max_triples=5)
        self.assertIn("triples", result)
        second_payload = json.loads(transport.calls[1][1].decode("utf-8"))
        self.assertEqual(second_payload["max_tokens"], 1000)

    def test_still_truncated_after_salvage_raises(self):
        partial = json.dumps(GOOD_RESULT)[:120]
        extractor, _, _ = make_extractor([
            (200, envelope(partial), {}),
            (200, envelope(partial), {}),
        ])
        with self.assertRaises(InvalidOutputError):
            extractor.extract_one("x", max_triples=5)


class TestConfigResolution(unittest.TestCase):
    def _env(self, **overrides: Any) -> dict[str, str]:
        base = {
            "LLM_BASE_URL": "",
            "LLM_API_KEY": "",
            "LLM_MODEL_ID": "",
            "OPENCODE_ZEN_BASE_URL": "",
            "OPENCODE_ZEN_API": "",
            "OPENCODE_MODEl": "",
            "OPENCODE_MODEL": "",
            "LAYA_BASE_URL": "http://127.0.0.1:8000",
            "LAYA_MODEL_ID": "laya",
        }
        base.update({k: v for k, v in overrides.items() if v is not None})
        return {k: v for k, v in base.items() if v}

    def _settings(self, env: dict[str, str]) -> Settings:
        # load_dotenv would re-read the repo .env (which now holds real keys),
        # so these resolution tests run with the file loader disabled.
        with patch.dict(os.environ, env, clear=True), \
                patch("graphrag.config.load_dotenv", lambda *a, **k: None):
            return Settings.from_env()

    def test_zen_fallbacks_resolve(self):
        settings = self._settings(
            self._env(OPENCODE_ZEN_API="zen-key", OPENCODE_MODEl="zen-model")
        )
        base, key, model = settings.require_llm()
        self.assertEqual(base, llm.DEFAULT_BASE_URL)
        self.assertEqual(key, "zen-key")
        self.assertEqual(model, "zen-model")

    def test_explicit_llm_vars_win_over_zen(self):
        settings = self._settings(self._env(
            LLM_BASE_URL="https://llm.example/v1",
            LLM_API_KEY="explicit-key",
            LLM_MODEL_ID="explicit-model",
            OPENCODE_ZEN_API="zen-key",
            OPENCODE_MODEl="zen-model",
        ))
        base, key, model = settings.require_llm()
        self.assertEqual((base, key, model),
                         ("https://llm.example/v1", "explicit-key", "explicit-model"))

    def test_missing_key_and_model_are_named(self):
        settings = self._settings(self._env())
        with self.assertRaises(RuntimeError) as ctx:
            settings.require_llm()
        message = str(ctx.exception)
        # The base URL has a default (Zen); only key and model are required.
        self.assertIn("LLM_API_KEY", message)
        self.assertIn("LLM_MODEL_ID", message)
        self.assertNotIn("LLM_BASE_URL", message)


if __name__ == "__main__":
    unittest.main()