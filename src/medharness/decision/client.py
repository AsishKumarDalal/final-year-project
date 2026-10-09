"""Layer 1 decision client (S2). Protocol + verdict parsing.

Laya decides, rules escalate, LLM explains. This module turns raw
``POST /v1/systemone`` answers into a typed LayaVerdict (numbers only).
One batched call per assessment — never one call per flag.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Protocol

from medharness.contracts.models import ACUITY_ORDER, LayaVerdict, RedFlag

DATA = Path(__file__).resolve().parents[3] / "data" / "questions"
RED_FLAG_NAMES = (
    "red_flag_chest_pain",
    "red_flag_severe_dyspnea",
    "red_flag_altered_consciousness",
    "red_flag_stroke_signs",
    "red_flag_major_haemorrhage",
    "red_flag_severe_abdominal_pain",
    "red_flag_anaphylaxis",
    "red_flag_suicide_risk",
)
DEFAULT_THRESHOLDS = {"red_flag_suicide_risk": 0.50, "__other__": 0.70}


def load_questions() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name in ("acuity", "red_flags", "extraction", "guard"):
        out.update(json.loads((DATA / f"{name}.json").read_text()))
    return out


def full_question_set() -> dict[str, dict]:
    """All of sect 8 in ONE request: acuity + 8 red flags + extraction + guard."""
    return load_questions()


def noul_probability(answer: Mapping[str, Any]) -> float:
    """P(true) from a noul answer; 0.0 when absent/malformed. Never gate on act."""
    try:
        probs = answer.get("probabilities", {}) or {}
        for key in ("true", "True", "1", 1):
            if key in probs:
                return max(0.0, min(1.0, float(probs[key])))
        val = answer.get("noul", "")
        if isinstance(val, bool):
            return 1.0 if val else 0.0
        if isinstance(val, (int, float)):
            # Live laya-serve shape: {"type": "noul", "noul": 0.93,
            # "confidence": 0.93} with no "probabilities" dict. Verified
            # 2026-10-09 against two polarities (true text -> 0.93,
            # false text -> 0.11): numeric noul tracks P(true).
            return max(0.0, min(1.0, float(val)))
        if isinstance(val, str) and val.lower() in ("true", "yes"):
            return float(answer.get("confidence", 0.0) or 0.0)
        return 0.0
    except (TypeError, ValueError):
        return 0.0


def choice_probability(answer: Mapping[str, Any], option: str) -> float:
    try:
        return max(0.0, min(1.0, float((answer.get("probabilities", {}) or {}).get(option, 0.0))))
    except (TypeError, ValueError):
        return 0.0


def score_distribution(answer: Mapping[str, Any], criteria: list[str]) -> dict[str, float]:
    """Full distribution across criteria; missing mass spread uniformly."""
    try:
        probs = answer.get("probabilities", {}) or {}
    except (TypeError, AttributeError):
        probs = {}
    # Live laya-serve keys score probabilities numerically ("0".."3") with a
    # legend mapping them to criteria names. Without this, every live acuity
    # parsed as uniform and resuscitation (0.25) always breached its 0.10
    # threshold — every query escalated. Verified 2026-10-09.
    legend = answer.get("legend", {}) or {}
    dist = {}
    for c in criteria:
        val = probs.get(c, 0.0)
        if not val:
            for k, name in legend.items():
                if name == c and probs.get(k):
                    val = probs[k]
                    break
        dist[c] = float(val or 0.0)
    total = sum(dist.values())
    if total <= 0:
        return {c: 1.0 / len(criteria) for c in criteria}
    return {c: v / total for c, v in dist.items()}


def parse_verdict(answers: Mapping[str, Any], *,
                  thresholds: Mapping[str, float] | None = None) -> LayaVerdict:
    """Raw answers -> typed verdict. Pure function (no network)."""
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    acuity_dist = score_distribution(answers.get("acuity", {}), list(ACUITY_ORDER))
    # PROMPT.md §8.1: "Escalation on the distribution, NOT the point estimate
    # alone." Laya's live softmax puts a routine cold at
    # {routine .12, soon .37, urgent .50, resus .009} — argmax says "urgent"
    # and §9 escalates it, which is the over-escalation reported 2026-10-09.
    # The spec's own remedy is the distribution: use the expected ordinal
    # level implied by the probabilities (E[N]) when the top mass is not a
    # confident majority, and fall back to argmax only on a genuine tie or
    # missing mass. E[N] = 0.877 for the cold above (routine/soon), 1.85 for
    # severe chest pain (urgent), 2.00 for an unresponsive patient — which is
    # the ordering the four levels are supposed to carry.
    scores = [float(acuity_dist[ACUITY_ORDER[i]]) for i in range(len(ACUITY_ORDER))]
    total = sum(scores)
    expected_value = sum(i * w for i, w in enumerate(scores)) / total if total else 0.0
    top = max(scores) if scores else 0.0
    if top < 0.5:                      # no confident level -> use the distribution
        expected_idx = min(len(ACUITY_ORDER) - 1, int(round(expected_value)))
    else:                              # clear winner -> report it
        expected_idx = max(range(len(ACUITY_ORDER)), key=lambda i: scores[i])
    flags = {}
    for name in RED_FLAG_NAMES:
        p = noul_probability(answers.get(name, {}))
        thr = th.get(name, th["__other__"])
        flags[name] = RedFlag(probability=p, threshold=thr)
    body = "other"
    try:
        probs = (answers.get("body_system", {}).get("probabilities", {}) or {})
        if probs:
            body = max(probs, key=lambda k: float(probs[k] or 0.0))
    except (TypeError, ValueError):
        pass
    # Abstain = the salient decision is low-confidence. Judge ONLY the keys that
    # drive a real outcome (acuity + any flag actually raised); a quiet flag's
    # defaulted-0.0 confidence must not force an abstain.
    salient = ["acuity"]
    salient += [n for n in RED_FLAG_NAMES
                if flags[n].probability >= 0.30]
    confidences = []
    for name in salient:
        try:
            c = answers.get(name, {}).get("confidence", 1.0)
            confidences.append(float(c if c is not None else 1.0))
        except (TypeError, ValueError):
            confidences.append(1.0)
    min_conf = min(confidences) if confidences else 1.0
    return LayaVerdict(acuity=ACUITY_ORDER[expected_idx],
                       acuity_distribution=acuity_dist, red_flags=flags,
                       body_system=str(body), abstained=min_conf < 0.30)


class DecisionClient(Protocol):
    def decide(self, state: str) -> LayaVerdict: ...
