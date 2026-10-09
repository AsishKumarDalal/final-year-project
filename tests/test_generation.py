"""Generation tests (S6). No network - QwenClient is stubbed via a fake
complete(). Exercises parse, tool loop, policy refusals, banned-phrase + citation gates.
"""
from __future__ import annotations

import unittest

from medharness.generation.qwen_client import (
    GenerationResult,
    build_system_prompt,
    parse_tool_calls,
    run_tool_loop,
    _TC_O,
    _TC_C,
)
from medharness.generation.policy import (
    crisis_text,
    find_banned,
    refusal_for,
    screen,
)


def _call(json_body: str) -> str:
    """Wrap a tool-call body in the delimiter tags (assembled, never literal)."""
    return f"{_TC_O}\n{json_body}\n{_TC_C}"


class ParseTests(unittest.TestCase):
    def test_parse_valid(self):
        calls, errors = parse_tool_calls(
            _call('{"name": "get_lab_ref", "arguments": {"test_name": "troponin"}}'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "get_lab_ref")
        self.assertEqual(calls[0]["arguments"]["test_name"], "troponin")
        self.assertEqual(errors, [])

    def test_parse_string_arguments(self):
        calls, _ = parse_tool_calls(
            _call('{"name": "kb_search", "arguments": "{\\"query\\": \\"flu\\"}"}'))
        self.assertEqual(calls[0]["arguments"], {"query": "flu"})

    def test_parse_bad_json_is_error(self):
        calls, errors = parse_tool_calls(_call("{oops"))
        self.assertEqual(calls, [])
        self.assertTrue(errors)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return self.script.pop(0) if self.script else "done"


class ToolLoopTests(unittest.TestCase):
    def test_tool_then_answer(self):
        call = _call('{"name": "get_lab_ref", "arguments": {"test_name": "troponin"}}')
        client = FakeClient([call, "Troponin normal range is 0.00-0.04 ng/mL."])
        seen = {}

        def dispatch(tool, role, args):
            from medharness.contracts.models import ToolFact
            seen[tool] = args
            return ToolFact(facts=["troponin 0.00-0.04"], source_ids=["lab_ranges.json:troponin"],
                            source_type="lab_table")

        res = run_tool_loop(client, "patient", "what is troponin?", dispatch)
        self.assertEqual(res.steps, 2)
        self.assertIn("get_lab_ref", seen)
        self.assertEqual(res.citations, ["lab_ranges.json:troponin"])

    def test_budget_exhausted(self):
        client = FakeClient([_call("{}")] * 10)

        def dispatch(tool, role, args):
            from medharness.contracts.models import ToolFact
            return ToolFact(facts=["x"], source_ids=["a"], source_type="t")

        res = run_tool_loop(client, "patient", "go", dispatch, max_steps=3)
        self.assertIn("too many steps", res.answer)


class PolicyTests(unittest.TestCase):
    def test_banned_phrase(self):
        r = screen("You have diabetes.", ["text_unit:x"])
        self.assertFalse(r["ok"])
        self.assertIn("banned_phrase", r["reason"])

    def test_community_citation_rejected(self):
        r = screen("Some claim.", ["community:5"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "generated_citation")

    def test_good_answer_passes(self):
        r = screen("Chest pain may signal a cardiac emergency.", ["text_unit:chest_pain::0"])
        self.assertTrue(r["ok"])

    def test_refusal_diagnosis(self):
        self.assertIsNotNone(refusal_for({"diagnosis": True}))
        self.assertEqual(refusal_for({"diagnosis": True})["reason"], "diagnosis_request")

    def test_refusal_crisis_is_config(self):
        r = refusal_for({"self_harm": True})
        self.assertEqual(r["answer"], crisis_text())
        self.assertNotEqual(crisis_text(), "")

    def test_system_prompt_has_tools(self):
        p = build_system_prompt()
        self.assertIn("<tools>", p)
        self.assertIn("kb_search", p)
        self.assertIn("never diagnose", p.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
