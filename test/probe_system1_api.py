#!/usr/bin/env python3
"""Live connectivity probe for the System 1 decision model (`d1:free`).

MANUAL ONLY. This script is not part of `pytest` (it is not named test_*),
not imported by anything, and never runs inside `make validate` — the project
rule is that no automated test touches the network.

The key `LIQUILD_AI_API` is read from the environment or the repo `.env`
file and sent only as an `Authorization: Bearer` header to the provider.
The key value is never printed, logged, or written to any file.

Usage:
    python3 test/probe_system1_api.py

Exit codes: 0 = at least one endpoint answered a decision request,
            1 = no endpoint worked (or the key is missing).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "d1:free"
TIMEOUT_S = 30
MAX_BODY_CHARS = 2000

# Question payload mirrors PROMPT.md §8 question types (score / noul / choice)
# with synthetic text — no real patient data anywhere.
DECISION_QUESTIONS = {
    "model": MODEL,
    "state": "Chest pain and shortness of breath since this morning.",
    "questions": {
        "red_flag_chest_pain": {
            "type": "noul",
            "instructions": "Does the patient report chest pain, pressure, or tightness?",
        },
        "body_system": {
            "type": "choice",
            "instructions": "Which body system is the main complaint about?",
            "criteria": {
                "cardiac": "chest pain, palpitations, breathlessness on exertion",
                "respiratory": "cough, breathlessness, wheezing, sputum",
                "other": "anything that does not clearly fit the above",
            },
        },
        "acuity": {
            "type": "score",
            "instructions": (
                "Rate the urgency of this patient's presentation, from least to "
                "most urgent, based only on the information given."
            ),
            "criteria": ["routine", "soon", "urgent", "resuscitation"],
        },
    },
}

CHAT_PAYLOAD = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
    "max_tokens": 16,
}

PROBES = [
    (
        "decisions endpoint (official)",
        "https://api.liquid.ai/decisions/v1/systemone",
        DECISION_QUESTIONS,
    ),
    (
        "decisions endpoint, alt path",
        "https://api.liquid.ai/v1/systemone",
        DECISION_QUESTIONS,
    ),
    (
        "OpenAI-style chat completions",
        "https://api.liquid.ai/v1/chat/completions",
        CHAT_PAYLOAD,
    ),
]


def load_key() -> str:
    key = os.environ.get("LIQUILD_AI_API", "").strip()
    source = "environment"
    if not key:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("LIQUILD_AI_API"):
                    _, _, value = line.partition("=")
                    key = value.strip().strip("\"'")
                    source = ".env"
                    break
    if not key:
        sys.exit("FAIL: LIQUILD_AI_API not found in environment or .env — cannot probe.")
    print(f"[key] loaded from {source} (value not printed, length={len(key)})")
    return key


def post_json(url: str, payload: dict, key: str) -> tuple[int | None, str, float]:
    """POST JSON with bearer auth. Returns (status, body, elapsed_seconds)."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            status = response.status
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        text = exc.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — report any transport failure, never crash
        elapsed = time.perf_counter() - started
        return None, f"<transport error: {type(exc).__name__}: {exc}>", elapsed
    elapsed = time.perf_counter() - started
    return status, text, elapsed


def summarise(label: str, status: int | None, text: str, elapsed: float) -> bool:
    shown = text if len(text) <= MAX_BODY_CHARS else text[:MAX_BODY_CHARS] + " …<truncated>"
    status_label = str(status) if status is not None else "no-status"
    print(f"\n=== {label} ===")
    print(f"    status={status_label}  latency={elapsed * 1000:.0f} ms")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print(f"    body (non-JSON): {shown}")
        return False
    print(f"    body: {json.dumps(parsed, indent=2)[:MAX_BODY_CHARS]}")
    if status == 200 and "answers" in parsed:
        usage = parsed.get("usage", {})
        print(
            f"    OK decision answered — usage: {usage}"
        )
        for name, answer in parsed.get("answers", {}).items():
            if isinstance(answer, dict):
                summary = {
                    k: answer[k]
                    for k in ("type", "noul", "choice", "score", "confidence")
                    if k in answer
                }
                probs = answer.get("probabilities")
                if probs is not None:
                    summary["probabilities"] = probs
                print(f"      {name}: {summary}")
        return True
    return False


def main() -> int:
    key = load_key()
    ok = False
    for label, url, payload in PROBES:
        print(f"\n--- probe: {label}")
        print(f"    POST {url}  model={MODEL}")
        status, text, elapsed = post_json(url, payload, key)
        if summarise(label, status, text, elapsed):
            ok = True
    print("\n=== RESULT ===")
    if ok:
        print("PASS — at least one endpoint accepted the key and answered a decision request.")
        return 0
    print("FAIL — no endpoint answered. See statuses above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
