"""Recorded-fixture adapter (S2). Replays stored Laya traces offline.

Same decide() signature as the HTTP adapter. Unknown state -> conservative
default verdict (low flags, routine acuity) so the dev path never escalates on
unseen text. ALL automated tests use this.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from medharness.contracts.models import LayaVerdict
from medharness.decision.client import RED_FLAG_NAMES, parse_verdict

def _noul(p: float, confidence: float | None = None) -> dict:
    conf = confidence if confidence is not None else (
        0.90 if abs(p - 0.5) > 0.2 else 0.25)
    return {"type": "noul", "noul": "true" if p >= 0.5 else "false",
            "probabilities": {"true": p, "false": 1.0 - p},
            "confidence": conf}


def _acuity(level: str, p_top: float = 0.80) -> dict:
    from medharness.contracts.models import ACUITY_ORDER
    rest = (1.0 - p_top) / 3
    return {"type": "score", "score": level,
            "probabilities": {c: (p_top if c == level else rest) for c in ACUITY_ORDER},
            "confidence": 0.85}


def _choice(options: dict[str, float]) -> dict:
    top = max(options, key=options.get)
    return {"type": "choice", "choice": top, "probabilities": dict(options),
            "confidence": 0.80}


def _low() -> dict:
    return {n: _noul(0.02) for n in RED_FLAG_NAMES}


def _guards(dx: float = 0.05, tx: float = 0.05, sh: float = 0.01) -> dict:
    return {"guard_diagnosis_request": _noul(dx),
            "guard_treatment_request": _noul(tx),
            "guard_self_harm_request": _noul(sh)}


def _safe_verdict_answers(state: str) -> dict:
    """Offline fallback when neither Laya nor a recorded case matches.

    Deliberately conservative: low red flags, routine acuity, abstain off.
    Never invents probabilities for a real patient — this is a dev-only
    default; the live path always calls Laya. See dev/README.md.
    """
    return {**_low(),
            "acuity": _acuity("routine", p_top=0.85),
            "extraction": {"body_system": {"type": "choice",
                                           "choice": "unknown",
                                           "probabilities": {"unknown": 1.0},
                                           "confidence": 0.0},
                           "abstain": _noul(0.02)},
            "guard": _guards()}

# 20 recorded-style cases: id -> (state, answers). Synthetic only.
def _build() -> dict[str, dict]:
    cases: dict[str, dict] = {}
    cases["chest_pain_severe"] = {
        "state": "Severe chest pain with pressure spreading to my left arm since this morning.",
        "answers": {**_low(), "red_flag_chest_pain": _noul(0.94),
                    "red_flag_severe_dyspnea": _noul(0.55, confidence=0.80),
                    "acuity": _acuity("urgent"),
                    "body_system": _choice({"cardiac": 0.9, "respiratory": 0.05, "other": 0.05}),
                    **_guards()}}
    cases["stroke_signs"] = {
        "state": "My face is drooping on one side and I cannot speak clearly.",
        "answers": {**_low(), "red_flag_stroke_signs": _noul(0.91),
                    "red_flag_altered_consciousness": _noul(0.60, confidence=0.80),
                    "acuity": _acuity("urgent"),
                    "body_system": _choice({"neurological": 0.85, "other": 0.15}),
                    **_guards()}}
    cases["suicide_risk"] = {
        "state": "I do not want to live anymore.",
        "answers": {**_low(), "red_flag_suicide_risk": _noul(0.88),
                    "acuity": _acuity("urgent", 0.60),
                    "body_system": _choice({"other": 0.7, "general": 0.3}),
                    **_guards(sh=0.93)}}
    cases["mild_headache"] = {
        "state": "I have had a mild headache for two days, no other symptoms.",
        "answers": {**_low(), "acuity": _acuity("routine"),
                    "body_system": _choice({"neurological": 0.7, "general": 0.2, "other": 0.1}),
                    **_guards()}}
    cases["diagnosis_ask"] = {
        "state": "Do I have diabetes? My sugar feels high.",
        "answers": {**_low(), "acuity": _acuity("soon"),
                    "body_system": _choice({"endocrine": 0.8, "other": 0.2}),
                    **_guards(dx=0.95)}}
    cases["treatment_ask"] = {
        "state": "What dose of aspirin should I take for my headache?",
        "answers": {**_low(), "acuity": _acuity("routine"),
                    "body_system": _choice({"neurological": 0.6, "general": 0.4}),
                    **_guards(tx=0.96)}}
    cases["resuscitation_prob"] = {
        "state": "Unresponsive, barely breathing, weak pulse.",
        "answers": {**_low(),
                    "red_flag_altered_consciousness": _noul(0.65, confidence=0.80),
                    "red_flag_severe_dyspnea": _noul(0.60, confidence=0.80),
                    "acuity": {"type": "score", "score": "urgent",
                               "probabilities": {"routine": 0.02, "soon": 0.08,
                                                 "urgent": 0.75, "resuscitation": 0.15},
                               "confidence": 0.80},
                    "body_system": _choice({"general": 0.6, "other": 0.4}),
                    **_guards()}}
    cases["abstain_low_conf"] = {
        "state": "Something feels off, hard to describe.",
        "answers": {n: {"type": "noul", "noul": "false",
                        "probabilities": {"true": 0.45, "false": 0.55},
                        "confidence": 0.20} for n in RED_FLAG_NAMES} | {
            "acuity": {"type": "score", "score": "soon",
                       "probabilities": {"routine": 0.3, "soon": 0.4,
                                         "urgent": 0.2, "resuscitation": 0.1},
                       "confidence": 0.20},
            "body_system": _choice({"other": 0.5, "general": 0.5}),
            **_guards()}}
    extra = [
        ("bleeding_severe", "Vomiting blood and black stools since last night.",
         {"red_flag_major_haemorrhage": 0.93}, "urgent"),
        ("breathing_severe", "Cannot breathe, cannot finish a sentence, wheezing badly.",
         {"red_flag_severe_dyspnea": 0.90}, "urgent"),
        ("abdominal_severe", "Sudden rigid abdominal pain, worst pain of my life.",
         {"red_flag_severe_abdominal_pain": 0.89}, "urgent"),
        ("anaphylaxis", "Face and lips swelling after peanuts, throat closing.",
         {"red_flag_anaphylaxis": 0.92}, "urgent"),
        ("faint", "Fainted twice today, confused when I woke up.",
         {"red_flag_altered_consciousness": 0.85}, "urgent"),
        ("cold_mild", "Runny nose and mild cough for a day.", {}, "routine"),
        ("fever_soon", "Fever 101 for two days with body ache.", {}, "soon"),
        ("rash_soon", "Itchy rash on my arms spreading slowly.", {}, "soon"),
        ("back_pain", "Lower back ache after lifting a box.", {}, "routine"),
        ("fatigue", "Tired for a week, sleeping poorly.", {}, "routine"),
        ("chest_mild", "Brief chest tightness that passed in a minute.", {}, "soon"),
        ("dizzy_soon", "Dizzy when standing up quickly.", {}, "soon"),
    ]
    for cid, state, highs, ac in extra:
        ans = _low()
        for k, p in highs.items():
            ans[k] = _noul(p)
        ans["acuity"] = _acuity(ac)
        ans["body_system"] = _choice({"other": 0.6, "general": 0.4})
        ans.update(_guards())
        cases[cid] = {"state": state, "answers": ans}
    return cases


CASES = _build()
BY_STATE = {c["state"]: cid for cid, c in CASES.items()}


@dataclass
class FixtureDecisionClient:
    thresholds: dict = field(default_factory=dict)

    def decide(self, state: str) -> LayaVerdict:
        cid = BY_STATE.get(state)
        if cid is None:
            # No Laya and no recorded case -> default to the safe verdict so the
            # dev path never escalates on unknown text (fail toward caution).
            return parse_verdict(_safe_verdict_answers(state), thresholds=self.thresholds or None)
        return parse_verdict(CASES[cid]["answers"], thresholds=self.thresholds or None)

    def decide_case(self, case_id: str) -> LayaVerdict:
        if case_id not in CASES:
            raise KeyError(f"unknown fixture case {case_id!r}")
        return parse_verdict(CASES[case_id]["answers"], thresholds=self.thresholds or None)

    @staticmethod
    def case_ids() -> list[str]:
        return sorted(CASES)

