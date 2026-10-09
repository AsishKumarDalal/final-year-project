"""End-to-end demo (S8). No LLM by default (degraded but decides). Synthetic only.

    python -m medharness.demo                 # fixtures, no network
    MEDH_LIVE=1 python -m medharness.demo      # live Laya at :8000 (manual)
    MEDH_ENABLE_LLM=1 ... python -m medharness.demo   # + hosted Qwen explanation
"""
from __future__ import annotations

import json
import os
import sys

from medharness.contracts.models import HarnessRequest
from medharness.service.app import build_orchestrator

CASES = [
    ("patient", "Severe chest pain with pressure spreading to my left arm since this morning."),
    ("patient", "My face is drooping on one side and I cannot speak clearly."),
    ("patient", "I do not want to live anymore."),
    ("patient", "I have had a mild headache for two days, no other symptoms."),
    ("nurse", "Do I have diabetes? My sugar feels high."),
    ("doctor", "What dose of aspirin should I take for my headache?"),
]


def main() -> int:
    orch = build_orchestrator()
    show_handoff = "--handoff" in sys.argv
    for role, text in CASES:
        resp = orch.assess(HarnessRequest(text=text, actor_role=role))
        head = f"[{resp.decision.upper():8}] role={role} | {text[:46]}"
        print(head)
        if resp.escalated:
            print(f"    level={resp.escalation.level} llm_called={resp.escalation.llm_called}")
            for r in resp.escalation.reasons[:3]:
                print(f"      - {r.flag}: {r.probability} >= {r.threshold}")
        else:
            print(f"    explanation: {resp.explanation[:90]}")
            if resp.citations:
                print(f"    citations: {resp.citations[:2]}")
        if show_handoff:
            from medharness.handoff import render_handoff
            print(render_handoff(resp, reported_text=text))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
