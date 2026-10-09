"""P1 exam (harness_docs phase1 §6) with a stubbed chat — no network."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from agent import loop
from tools.registry import dispatch


def _answer(text):
    return {"role": "assistant", "content": text}


def _call(name, args, call_id="c1"):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name,
                                         "arguments": json.dumps(args)}}]}


class TestLoop(unittest.TestCase):
    def test_direct_answer_no_tools(self):
        with patch.object(loop, "chat", return_value=_answer("4")) as m:
            answer, history = loop.run("What is 2+2?")
            self.assertEqual(answer, "4")
            self.assertEqual(m.call_count, 1)

    def test_read_then_answer(self):
        calls = [_call("read_file", {"path": "main.py"}), _answer("done")]

        def fake_chat(messages, tools):
            return calls.pop(0)

        with patch.object(loop, "chat", side_effect=fake_chat):
            with patch("tools.registry.REGISTRY",
                       {"read_file": lambda a: "line1\nline2"}):
                answer, history = loop.run("read it")
        self.assertEqual(answer, "done")
        tool_msgs = [m for m in history if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)

    def test_bad_json_is_observation(self):
        bad = {"role": "assistant", "content": "",
               "tool_calls": [{"id": "c1", "type": "function",
                               "function": {"name": "read_file",
                                            "arguments": "{oops"}}]}
        seq = [bad, _answer("recovered")]

        def fake_chat(messages, tools):
            return seq.pop(0)

        with patch.object(loop, "chat", side_effect=fake_chat):
            answer, history = loop.run("go")
        self.assertEqual(answer, "recovered")
        self.assertIn("ERROR: could not parse",
                      [m for m in history if m["role"] == "tool"][0]["content"])

    def test_unknown_tool_is_denied(self):
        self.assertIn("ERROR", dispatch("nope", {}))

    def test_finish_returns_summary(self):
        seq = [_call("finish", {"summary": "all good", "evidence": "x"}),
               _answer("unreached")]

        def fake_chat(messages, tools):
            return seq.pop(0)

        with patch.object(loop, "chat", side_effect=fake_chat):
            answer, _ = loop.run("go")
        self.assertEqual(answer, "all good")

    def test_long_output_truncated(self):
        seq = [_call("read_file", {"path": "big"}), _answer("ok")]

        def fake_chat(messages, tools):
            return seq.pop(0)

        with patch.object(loop, "chat", side_effect=fake_chat):
            with patch("tools.registry.REGISTRY",
                       {"read_file": lambda a: "z" * 5000}):
                _, history = loop.run("go")
        content = [m for m in history if m["role"] == "tool"][0]["content"]
        self.assertTrue(content.endswith("...[truncated]"))

    def test_budget_exhausted_is_honest(self):
        with patch.object(loop, "chat",
                          return_value=_call("read_file", {"path": "x"})):
            with patch.object(loop, "MAX_TURNS", 2):
                with patch("tools.registry.REGISTRY",
                           {"read_file": lambda a: "data"}):
                    answer, _ = loop.run("go")
        self.assertIn("budget exhausted", answer)


if __name__ == "__main__":
    unittest.main()
