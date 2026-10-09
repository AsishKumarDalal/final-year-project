"""Deterministic rule engine (S3, safety-critical). Pure function of
(verdict, thresholds). No model call, no LLM, no IO. May import ONLY contracts.

Escalate if ANY of (PROMPT.md sect 9):
  1. red_flag_suicide_risk      >= 0.50
  2. any other red_flag_*        >= 0.70
  3. acuity expected level       >= urgent (ordinal index >= 2)
  4. P(resuscitation)            >= 0.10
  5. Laya abstained AND any flag >= 0.40  -> reason insufficient_confidence

Downgrade is forbidden: a high-probability negative never suppresses a fired flag.
On any escalation llm_called is false — the LLM is never reached.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from medharness.contracts.models import (
    ACUITY_ORDER,
    Escalation,
    EscalationReason,
)
from medharness.rules.thresholds import SUICIDE_FLAG, Thresholds

if TYPE_CHECKING:
    from medharness.contracts.models import LayaVerdict


def escalate(verdict: "LayaVerdict",
             thresholds: Thresholds | None = None) -> Escalation | None:
    """Return an Escalation if any sect 9 branch fires, else None. Deterministic."""
    t = thresholds or Thresholds()
    reasons: list[EscalationReason] = []

    # 1 + 2: red-flag thresholds (suicide is more sensitive than other flags)
    for name, flag in verdict.red_flags.items():
        thr = t.suicide if name == SUICIDE_FLAG else t.other
        if flag.probability >= thr:
            reasons.append(EscalationReason(
                flag=name, probability=round(flag.probability, 4), threshold=thr))

    # 3: expected acuity level >= urgent (ordinal)
    idx = ACUITY_ORDER.index(verdict.acuity)
    if idx >= t.acuity_urgent_index:
        reasons.append(EscalationReason(
            flag="acuity_level", probability=float(idx),
            threshold=float(t.acuity_urgent_index)))

    # 4: resuscitation probability
    resus = verdict.acuity_distribution.get("resuscitation", 0.0)
    if resus >= t.resuscitation_prob:
        reasons.append(EscalationReason(
            flag="resuscitation_probability", probability=round(resus, 4),
            threshold=t.resuscitation_prob))

    # 5: abstained AND any flag >= 0.40 -> escalate with insufficient_confidence
    if verdict.abstained:
        for name, flag in verdict.red_flags.items():
            if flag.probability >= t.abstain_flag:
                reasons.append(EscalationReason(
                    flag=f"insufficient_confidence:{name}",
                    probability=round(flag.probability, 4), threshold=t.abstain_flag))

    if not reasons:
        return None

    level = "resuscitation" if resus >= t.resuscitation_prob else (
        "urgent" if idx >= t.acuity_urgent_index else "soon")
    return Escalation(level=level, reasons=reasons,
                      llm_called=False)  # sect 9 invariant: always false
