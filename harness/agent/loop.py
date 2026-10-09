"""The ReAct loop. Every protocol rule from harness_docs phase1 §1 lives here."""

from __future__ import annotations

import json

from agent.llm import chat
from agent.prompt import SYSTEM_PROMPT
from tools.registry import Finish, dispatch, schemas

MAX_TURNS = 25
MAX_TOOL_OUTPUT = 2000


def run(user_message: str, history=None):
    messages = ([{"role": "system", "content": SYSTEM_PROMPT}]
                + list(history or [])
                + [{"role": "user", "content": user_message}])
    for _ in range(1, MAX_TURNS + 1):
        assistant_msg = chat(messages, schemas())
        messages.append(assistant_msg)  # RULE 1: assistant BEFORE tool results
        tool_calls = assistant_msg.get("tool_calls", [])
        if not tool_calls:
            return assistant_msg.get("content", ""), messages[1:]
        for tc in tool_calls:
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception as exc:
                args = None
                parse_error = f"ERROR: could not parse tool arguments: {exc}"
            if args is None:
                result = parse_error
            else:
                try:
                    result = dispatch(tc["function"]["name"], args)
                except Finish as done:
                    return done.summary, messages[1:]
            if len(result) > MAX_TOOL_OUTPUT:
                result = result[:MAX_TOOL_OUTPUT] + "...[truncated]"
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": result or "(no output)"})
    return "Iteration budget exhausted — task incomplete.", messages[1:]
