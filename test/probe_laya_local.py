#!/usr/bin/env python3
"""Local System-1 (Laya) endpoint probe — the harness's live decision service.

Target: Ollama's System One endpoint, `POST http://localhost:11434/v1/systemone`,
body `{model, state, questions}`. Endpoint and model id come from the environment
(`LAYA_BASE_URL`, `LAYA_MODEL_ID`) or `.env` — never hardcoded credentials.

MANUAL ONLY: not named `test_*`, not imported, never part of `make validate`.
No secret is printed or logged.

Usage:
    python3 test/probe_laya_local.py            # 1 request + answer shapes
    python3 test/probe_laya_local.py --repeat 5 # determinism check (§16.3)

Exit codes: 0 = endpoint healthy, 1 = unhealthy / unreachable.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "http://localhost:11434"
TIMEOUT_S = 120

# The PROMPT.md §8 question types, one question set, sent as a single request.
QUESTIONS = {
    "acuity": {
        "type": "score",
        "instructions": (
            "Rate the urgency of this patient's presentation, from least to most "
            "urgent, based only on the information given."
        ),
        "criteria": ["routine", "soon", "urgent", "resuscitation"],
    },
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
}

STATE = "Chest pain and shortness of breath since this morning."


def load_env_file() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def post(url: str, payload: dict) -> tuple[int | None, str, float]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            status = response.status
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        text = exc.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — report, never crash
        return None, f"<transport error: {type(exc).__name__}: {exc}>", time.perf_counter() - started
    return status, text, time.perf_counter() - started


def main() -> int:
    load_env_file()
    repeat = 1
    if "--repeat" in sys.argv:
        repeat = int(sys.argv[sys.argv.index("--repeat") + 1])

    base_url = os.environ.get("LAYA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("LAYA_MODEL_ID", "laya")
    url = f"{base_url}/v1/systemone"
    payload = {"model": model, "state": STATE, "questions": QUESTIONS}

    print(f"endpoint : POST {url}")
    print(f"model    : {model}")
    print(f"state    : {STATE!r}  (synthetic test text)")

    bodies: list[str] = []
    latencies: list[float] = []
    healthy = False

    for index in range(repeat):
        status, text, elapsed = post(url, payload)
        latencies.append(elapsed * 1000)
        status_label = str(status) if status is not None else "no-status"
        print(f"\n--- request {index + 1}/{repeat}: status={status_label} latency={elapsed * 1000:.0f} ms")
        if status != 200:
            print(f"    body: {text[:800]}")
            continue
        parsed = json.loads(text)
        bodies.append(json.dumps(parsed.get("answers", {}), sort_keys=True))
        healthy = True
        if repeat == 1:
            print(f"    model echoed: {parsed.get('model')}")
            print(f"    usage      : {parsed.get('usage')}")
            for name, answer in parsed.get("answers", {}).items():
                if isinstance(answer, dict):
                    summary = {
                        key: answer[key]
                        for key in ("type", "noul", "choice", "score", "confidence")
                        if key in answer
                    }
                    if "probabilities" in answer:
                        summary["probabilities"] = answer["probabilities"]
                    print(f"    {name}: {summary}")

    latencies_sorted = sorted(latencies)
    print(
        f"\nlatency  : min={latencies_sorted[0]:.0f} ms  "
        f"median={latencies_sorted[len(latencies_sorted) // 2]:.0f} ms  "
        f"max={latencies_sorted[-1]:.0f} ms  (budget PROMPT.md 7.1 = 150 ms)"
    )

    if repeat > 1 and len(bodies) > 1:
        identical = len(set(bodies)) == 1
        print(
            f"determinism ({len(bodies)} answered runs, PROMPT.md 16.3 expects identical): "
            f"{'IDENTICAL' if identical else 'DIFFERENT'}"
        )
        if not identical:
            first = json.loads(bodies[0])
            second = json.loads(bodies[1])
            for name in first:
                if first[name] != second.get(name):
                    print(f"    differs: {name}\n      run1: {first[name]}\n      run2: {second.get(name)}")

    print("\n=== RESULT ===")
    if healthy:
        print("PASS — local System-1 endpoint answered.")
        return 0
    print("FAIL — endpoint did not answer. Is `ollama serve` running and the model pulled?")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())