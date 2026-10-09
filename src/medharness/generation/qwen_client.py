"""Qwen client with client-side tool calling (S6). Hosted Kaggle endpoint.

IMPORTANT (docs/decisions.md 2026-10-09): the llama.cpp server has NO native
tool support - do NOT pass the OpenAI `tools=` parameter (silently ignored).
Tool schemas go in the system prompt as Qwen <tools> XML; the model emits
tool_call tags as plain text; we regex-parse, execute via the registry, and
return results in tool_response turns.

The delimiters below are assembled from parts on purpose: writing the literal
tags into source is fragile (tooling consumes them). They never appear verbatim.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable

# --- delimiter parts (never written as full tags in source) ---
_TC_O, _TC_C = "<" + "tool_call>", "<" + "/tool_call>"
_TR_O, _TR_C = "<" + "tool_response>", "<" + "/tool_response>"
CALL_RE = re.compile(_TC_O + r"\s*(.*?)\s*" + _TC_C, re.S)

MEDICAL_PERSONA = (
    "a careful medical information assistant. You never diagnose, never name a "
    "disease as a conclusion, and never give treatment or dosing advice. You "
    "explain general information and cite your sources. If the user asks for a "
    "diagnosis, dose, or how to treat, you refuse and point them to a clinician."
)

TOOL_SPECS = [
    {"name": "kb_search",
     "description": "Search the trusted medical corpus; returns cited passages.",
     "parameters": {"type": "object",
                    "properties": {"query": {"type": "string"},
                                   "top_k": {"type": "integer"}},
                    "required": ["query"]}},
    {"name": "get_lab_ref",
     "description": "Reference range and critical thresholds for a lab test.",
     "parameters": {"type": "object",
                    "properties": {"test_name": {"type": "string"}},
                    "required": ["test_name"]}},
    {"name": "get_drug_interactions",
     "description": "Known interaction list and severity for a set of drugs.",
     "parameters": {"type": "object",
                    "properties": {"drugs": {"type": "array",
                                             "items": {"type": "string"}}},
                    "required": ["drugs"]}},
    {"name": "search_external_docs",
     "description": ("Deep search over the external medical corpus with a full "
                     "graph walk (local 2-hop or global/community). Use this when "
                     "kb_search returns too little or nothing and you need to "
                     "connect facts across the corpus."),
     "parameters": {"type": "object",
                    "properties": {"query": {"type": "string"},
                                   "mode": {"type": "string",
                                            "enum": ["local", "global", "basic"]}},
                    "required": ["query"]}},
]


@dataclass
class GenerationResult:
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    refused: bool = False
    reason: str = ""
    steps: int = 0


def build_system_prompt(persona: str = MEDICAL_PERSONA,
                        tools: list[dict] | None = None) -> str:
    specs = [{"type": "function", "function": t} for t in (tools or TOOL_SPECS)]
    return (
        f"You are {persona}\n\n"
        "# Tools\n\n"
        "You may call one or more functions to assist with the user query.\n\n"
        "Function signatures are within <tools></tools> XML tags:\n<tools>\n"
        + "\n".join(json.dumps(s) for s in specs)
        + "\n</tools>\n\n"
        f"For each function call, return a json object with function name and "
        f"arguments within {_TC_O}{_TC_C} XML tags:\n{_TC_O}\n"
        '{"name": <function-name>, "arguments": <args-json-object>}\n'
        f"{_TC_C}\n"
        "If no tool is needed, answer directly. Never invent tool output; wait "
        "for the result before claiming any fact. Cite source ids you received. "
        "Prefer kb_search first; if it is not enough, call search_external_docs "
        "for a deeper, full-graph search across the corpus."
    )


def parse_tool_calls(text: str) -> tuple[list[dict], list[str]]:
    """Return (calls, errors). Mirrors test/local_kaggle_llm.py contract."""
    calls, errors = [], []
    for raw in CALL_RE.findall(text):
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict) or "name" not in obj:
                raise ValueError("missing 'name'")
            args = obj.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            calls.append({"name": obj["name"], "arguments": args})
        except Exception as exc:
            errors.append(f"Bad tool call {raw[:120]!r}: {exc}")
    if _TC_O in text and not calls and not errors:
        errors.append("Tool call block was not closed.")
    return calls, errors


@dataclass
class QwenClient:
    base_url: str = ""
    model: str = ""
    timeout: int = 600
    max_tokens: int = 1024
    temperature: float = 0.2
    _client: object = None

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or os.getenv(
            "KAGGLE_LLM_BASE_URL",
            "https://pond-breathing-foto-advocate.trycloudflare.com/v1")).rstrip("/")
        self.model = self.model or os.getenv("KAGGLE_LLM_MODEL", "qwen2.5-32b-instruct")

    def _openai(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key="sk-local",
                                  timeout=self.timeout)
        return self._client

    def complete(self, messages: list[dict]) -> str:
        """One chat-completions call. NO `tools=` param - server ignores it."""
        resp = self._openai().chat.completions.create(
            model=self.model, messages=messages, max_tokens=self.max_tokens,
            temperature=self.temperature)
        return resp.choices[0].message.content or ""


def run_tool_loop(client, role: str, user_text: str, dispatch: Callable,
                  *, max_steps: int = 6) -> GenerationResult:
    """Client-side ReAct over the medical tools. dispatch(tool, role, args)."""
    messages = [{"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": user_text}]
    citations: list[str] = []
    for step in range(1, max_steps + 1):
        text = client.complete(messages)
        calls, errors = parse_tool_calls(text)
        if not calls and not errors:
            return GenerationResult(answer=text, citations=citations, steps=step)
        messages.append({"role": "assistant", "content": text})
        results = list(errors)
        for c in calls:
            try:
                tf = dispatch(c["name"], role, c["arguments"])
                citations.extend(tf.source_ids)
                results.append(json.dumps({"facts": tf.facts,
                                           "source_ids": tf.source_ids}))
            except Exception as exc:  # tool error is an observation, never a crash
                results.append(f"Tool error: {exc}")
        messages.append({"role": "user",
                         "content": "\n".join(f"{_TR_O}\n{r[:4000]}\n{_TR_C}"
                                              for r in results)})
    return GenerationResult(answer="Stopped: too many steps.", citations=citations,
                            steps=max_steps)
