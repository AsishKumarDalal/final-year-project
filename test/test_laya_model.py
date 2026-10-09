"""Manual Laya decision-model smoke test. Live only, never part of pytest.

Usage: ~/venvs/graphrag/bin/python test/test_laya_model.py
"""

from __future__ import annotations

import json
import time

from graphrag.system1 import System1Client, routing_question, score_questions


def main() -> int:
    client = System1Client(base_url="http://127.0.0.1:8000", model_id="laya")
    print("available:", client.available())

    cases = [
        ("chest pain", "I have severe chest pain spreading to my left arm."),
        ("mild", "I have had a mild headache for two days."),
        ("suicide risk", "I do not want to live anymore."),
    ]
    for label, text in cases:
        questions = {"routing": routing_question()}
        questions.update(score_questions("How urgent is this?", [{"text": text}]))
        answers = client.decide(text, questions)
        print(f"\n--- {label} ---")
        for key, value in answers.items():
            print(f"  {key}: {json.dumps(value, ensure_ascii=False)[:300]}")

    started = time.perf_counter()
    for _ in range(5):
        client.decide("Repeat after me.", {"routing": routing_question()})
    elapsed = (time.perf_counter() - started) / 5
    print(f"\nmean latency: {elapsed * 1000:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
