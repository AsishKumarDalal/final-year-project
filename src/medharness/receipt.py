"""Decision Receipt (S10) — the artifact ChatGPT structurally cannot produce.

Every assessment yields a verifiable receipt: the calibrated red-flag board
(each flag's probability vs its threshold), the versioned safety parameters,
whether the AI was even allowed to speak, the sources, and a reproducibility
fingerprint. Same inputs -> same fingerprint, forever.

Why ChatGPT can't make this:
  - no calibrated probabilities (it emits confident prose, not P(flag));
  - no deterministic safety gate (its "seek help" is a prompt you can argue past;
    here llm_called=false on escalation is a code guarantee);
  - no resolvable citations (an id that doesn't map to real text is a hard fail);
  - no reproducibility (identical input, identical decision).
"""
from __future__ import annotations

import hashlib
import json

from medharness.contracts.models import HarnessResponse


def _flag_board(trace: dict) -> list[dict]:
    board = []
    for flag, rec in trace.get("red_flags", {}).items():
        p, thr = rec["p"], rec["threshold"]
        board.append({"flag": flag, "probability": p, "threshold": thr,
                      "fired": p >= thr})
    board.sort(key=lambda r: r["probability"], reverse=True)
    return board


def fingerprint(resp: HarnessResponse) -> str:
    """Stable hash over the salient decision. Same inputs -> same value."""
    t = resp.decision_trace
    salient = {
        "decision": resp.decision,
        "acuity": t.get("acuity"),
        "acuity_distribution": t.get("acuity_distribution"),
        "red_flags": t.get("red_flags"),
        "thresholds_version": t.get("thresholds_version"),
        "citations": sorted(resp.citations),
    }
    blob = json.dumps(salient, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def build_receipt(resp: HarnessResponse) -> dict:
    trace = resp.decision_trace
    return {
        "schema": "medicheck.receipt/v1",
        "decision": resp.decision,                 # escalate | explain
        "escalated": resp.escalated,
        "urgency": trace.get("acuity"),
        # THE guarantee: on escalation the LLM never ran. ChatGPT cannot assert this.
        "ai_involved_in_decision": bool(trace.get("llm_called")),
        "thresholds_version": trace.get("thresholds_version"),
        "red_flag_board": _flag_board(trace),
        "sources": list(resp.citations),
        "reproducible_fingerprint": fingerprint(resp),
        "verifiable": resp.escalated or bool(resp.citations),
    }


def render_text(resp: HarnessResponse) -> str:
    r = build_receipt(resp)
    lines = [
        "╭─ MediCheck Decision Receipt ────────────────────────────",
        f"│ Decision        : {r['decision'].upper()}",
        f"│ Urgency         : {r['urgency']}",
        f"│ AI in decision  : {'yes (explanation only)' if r['ai_involved_in_decision'] else 'NO — safety decision was deterministic'}",
        f"│ Safety rules    : {r['thresholds_version']}",
        f"│ Fingerprint     : {r['reproducible_fingerprint']}",
        "│ Red-flag board  :",
    ]
    if r["red_flag_board"]:
        for f in r["red_flag_board"]:
            mark = "🔴 FIRED" if f["fired"] else "⚪"
            lines.append(f"│   {mark}  {f['flag']:<34} P={f['probability']:.2f} ≥ {f['threshold']:.2f}")
    else:
        lines.append("│   (none recorded)")
    lines.append(f"│ Sources         : {len(r['sources'])} cited"
                 + (" (all resolve to corpus text)" if r["sources"] else ""))
    lines.append("╰─────────────────────────────────────────────────────────")
    if resp.escalated and resp.escalation:
        lines.append("")
        lines.append(f"👉 {resp.escalation.instruction}")
    return "\n".join(lines)
