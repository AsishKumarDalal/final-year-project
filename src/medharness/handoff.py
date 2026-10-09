"""Clinician Handoff (S11) — the "bring this to your doctor" pre-chart.

Bundles one assessment into a single, shareable, structured artifact: the typed
triage decision, the calibrated red-flag board, the reproducibility fingerprint,
the cited explanation, and the honest disclaimer. It exists ONLY because this
system's output is typed + calibrated + deterministic + cited — a general chatbot
emits confident prose with no numbers, no reproducibility, and no verifiable
sources, so it cannot produce this.
"""
from __future__ import annotations

from medharness.contracts.models import HarnessResponse
from medharness.receipt import build_receipt

DISCLAIMER = ("Decision-support only — not a diagnosis, not clinically validated. "
              "Confirm with a qualified clinician.")


def build_handoff(resp: HarnessResponse, reported_text: str = "") -> dict:
    r = build_receipt(resp)
    fired = [f for f in r["red_flag_board"] if f["fired"]]
    return {
        "schema": "medicheck.handoff/v1",
        "decision": resp.decision,                      # escalate | explain
        "triage_level": "ESCALATE" if resp.escalated else r["urgency"],
        "urgency": r["urgency"],
        "ai_in_decision": r["ai_involved_in_decision"],  # False on escalation
        "thresholds_version": r["thresholds_version"],
        "fingerprint": r["reproducible_fingerprint"],
        "reported_text": reported_text,
        "red_flags_fired": fired,
        "red_flag_board": r["red_flag_board"],
        "recommendation": resp.explanation,
        "sources": list(resp.citations),
        "disclaimer": DISCLAIMER,
    }


def render_handoff(resp: HarnessResponse, reported_text: str = "") -> str:
    h = build_handoff(resp, reported_text)
    L = [
        "════════ MEDICHECK — CLINICIAN HANDOFF ════════",
        f"Triage level : {h['triage_level']}",
        f"Decision     : {h['decision']}",
        f"AI in decision : {'NO (deterministic safety gate)' if not h['ai_in_decision'] else 'explanation only'}",
        f"Safety rules : {h['thresholds_version']}   Fingerprint: {h['fingerprint']}",
    ]
    if h["reported_text"]:
        L += ["", "PATIENT REPORTED:", f'  "{h["reported_text"]}"']
    L += ["", "RED-FLAG ASSESSMENT (calibrated):"]
    if h["red_flag_board"]:
        for f in h["red_flag_board"]:
            mark = "🔴 FIRED" if f["fired"] else "⚪"
            L.append(f"  {mark}  {f['flag']:<32} P={f['probability']:.2f} (≥ {f['threshold']:.2f})")
    else:
        L.append("  (no red-flag data)")
    L += ["", "RECOMMENDATION / NOTES:", f"  {h['recommendation']}"]
    if h["sources"]:
        L += ["", "SOURCES CITED:"]
        L += [f"  - {s}" for s in h["sources"]]
    L += ["", "─" * 52, f"⚠ {h['disclaimer']}"]
    return "\n".join(L)


def to_intake_json(resp: HarnessResponse, reported_text: str = "") -> dict:
    """Structured pre-chart for an intake form / EHR field pre-population.

    This is the "clinician pre-chart" export: schema-clean, serialisable, with
    coded red-flag fields (present + calibrated probability), the deterministic
    safety-gate flag, and a reproducible fingerprint. A general chatbot cannot
    emit this — it has no calibrated numbers and no typed decision.
    """
    h = build_handoff(resp, reported_text)
    return {
        "schema": "medicheck.intake/v1",
        "status": "final",
        "triage": {"level": h["triage_level"], "decision": h["decision"],
                   "urgency": h["urgency"]},
        "safety": {"ai_in_decision": h["ai_in_decision"],
                   "thresholds_version": h["thresholds_version"],
                   "fingerprint": h["fingerprint"]},
        "patient_reported": reported_text,
        "red_flags": {
            f["flag"]: {"present": f["fired"], "probability": f["probability"],
                        "threshold": f["threshold"]}
            for f in h["red_flag_board"]
        },
        "recommendation": h["recommendation"],
        "sources": list(h["sources"]),
        "disclaimer": h["disclaimer"],
    }
