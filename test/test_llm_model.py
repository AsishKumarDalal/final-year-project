"""Manual generative-LLM smoke test. Live only, never part of pytest.

Usage: PYTHONPATH=. ~/venvs/graphrag/bin/python test/test_llm_model.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.request


def chat_completion(messages, *, model=None, tools=None, max_tokens=256):
    base = os.getenv("LLM_BASE_URL", "https://opencode.ai/zen/v1").rstrip("/")
    key = os.getenv("LLM_API_KEY") or os.getenv("OPENCODE_ZEN_API")
    if not key:
        raise RuntimeError("LLM_API_KEY is not set")
    payload = {
        "model": model or os.getenv("LLM_MODEL_ID", "space-bunny-free"),
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "harness-llm-test/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    print("== basic completion ==")
    started = time.perf_counter()
    out = chat_completion([{"role": "user", "content": "What is 2+2? Reply with just the number."}])
    elapsed = time.perf_counter() - started
    content = out["choices"][0]["message"]["content"]
    usage = out.get("usage", {})
    print(f"answer: {content!r}")
    print(f"latency: {elapsed:.1f}s  tokens: {usage}")

    print("\n== tool calling ==")
    tools = [{"type": "function", "function": {
        "name": "get_weather",
        "description": "Get the weather for a city.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string"}}, "required": ["city"]},
    }}]
    out = chat_completion(
        [{"role": "user", "content": "What is the weather in Paris?"}],
        tools=tools,
    )
    msg = out["choices"][0]["message"]
    print(f"tool_calls: {json.dumps(msg.get('tool_calls'), indent=2)}")

    print("\n== bad key (expect 401/403) ==")
    try:
        chat_completion([{"role": "user", "content": "hi"}],
                        ) if False else None
        req = urllib.request.Request(
            "https://opencode.ai/zen/v1/chat/completions",
            data=json.dumps({"model": "space-bunny-free",
                             "messages": [{"role": "user", "content": "hi"}]}).encode(),
            method="POST",
            headers={"Authorization": "Bearer wrong",
                     "Content-Type": "application/json",
                     "User-Agent": "harness-llm-test/0.1"},
        )
        urllib.request.urlopen(req, timeout=30)
        print("UNEXPECTED: no error")
    except Exception as exc:
        print(f"expected error: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
