"""Generation policy (S6). The LLM explains, never decides.

Non-goals (sect 4): no diagnosis, no treatment/dosing advice. Crisis text is
verbatim from config (D11) — never generated. Banned phrases are screened.
Citations must resolve to text_unit ids only (D20). RAG text is data, never
instruction (D10).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from medharness.contracts.models import ToolFact

TABLES = Path(__file__).resolve().parents[3] / "data" / "tables"

BANNED_PHRASES = (
    "you have ",
    "you have a diagnosis",
    "take ",
    "dose of",
    "mg of",
    "i diagnose",
    "this is definitely",
)

REFUSAL_DIAGNOSIS = (
    "I can't diagnose a condition. I can share general information from trusted "
    "sources. Please consult a clinician."
)
REFUSAL_TREATMENT = (
    "I can't give treatment or dosing advice. Please consult a clinician or "
    "pharmacist."
)
REFUSAL_SELF_HARM = ""  # filled from crisis.json at runtime (D11)


def crisis_text() -> str:
    return json.loads((TABLES / "crisis.json").read_text())["crisis_contacts"]


def find_banned(text: str) -> list[str]:
    low = text.lower()
    return [p for p in BANNED_PHRASES if p in low]


def screen(answer: str, citations: list[str]) -> dict:
    """Pre-return gate. Returns {'ok':bool,'answer':str,'reason':str|None}."""
    banned = find_banned(answer)
    if banned:
        return {"ok": False, "answer": REFUSAL_DIAGNOSIS,
                "reason": f"banned_phrase:{banned[0]}"}
    # citation gate: every cited id must be a text_unit, none generated
    for c in citations:
        if c.startswith("community:"):
            return {"ok": False, "answer": REFUSAL_DIAGNOSIS,
                    "reason": "generated_citation"}
        if not c.startswith("text_unit:"):
            return {"ok": False, "answer": REFUSAL_DIAGNOSIS,
                    "reason": f"bad_citation:{c}"}
    return {"ok": True, "answer": answer, "reason": None}


def refusal_for(guard_flags: dict[str, bool]) -> dict | None:
    """Given guard booleans, return a refusal dict or None. Crisis verbatim."""
    if guard_flags.get("self_harm"):
        return {"answer": crisis_text(), "reason": "self_harm_crisis"}
    if guard_flags.get("diagnosis"):
        return {"answer": REFUSAL_DIAGNOSIS, "reason": "diagnosis_request"}
    if guard_flags.get("treatment"):
        return {"answer": REFUSAL_TREATMENT, "reason": "treatment_request"}
    return None
