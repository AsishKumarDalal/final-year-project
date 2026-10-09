"""Thin OpenAI-SDK chat-completions client. No policy, no retries (P6)."""

from __future__ import annotations

import os

_client = None


def _client_or_raise():
    global _client
    if _client is not None:
        return _client
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai SDK is not installed.") from exc
    base_url = os.getenv("BASE_URL", "https://opencode.ai/zen/v1")
    api_key = os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("API_KEY (or OPENAI_API_KEY) is not set.")
    _client = OpenAI(base_url=base_url, api_key=api_key)
    return _client


def chat(messages, tools):
    """One chat-completions call, normalised to {role, content, tool_calls?}."""
    client = _client_or_raise()
    model = os.getenv("MODEL", "space-bunny-free")
    resp = client.chat.completions.create(
        model=model, messages=messages, tools=tools or None)
    msg = resp.choices[0].message
    out = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name,
                          "arguments": tc.function.arguments or "{}"}}
            for tc in msg.tool_calls
        ]
    return out
