"""Rule engine thresholds (S3). Versioned safety parameters — one file, one unit.

D13: thresholds are safety parameters; change as a unit, on the record, never to
hit a target number. Copy sect 9 of PROMPT.md verbatim.
"""
from __future__ import annotations

from dataclasses import dataclass

VERSION = "v1"

SUICIDE_FLAG = "red_flag_suicide_risk"
SUICIDE_THRESHOLD = 0.50          # sect 9: suicide risk is lower (more sensitive)
OTHER_FLAG_THRESHOLD = 0.70       # sect 9: any other red_flag_*
ACUITY_URGENT_INDEX = 2           # sect 9: expected level >= urgent (ordinal idx)
RESUSCITATION_PROB_THRESHOLD = 0.10   # sect 9: P(resuscitation) >= 0.10
ABSTAIN_FLAG_THRESHOLD = 0.40     # sect 9: abstained AND any flag >= 0.40
MIN_CONFIDENCE = 0.30             # sect 7.1 abstain trigger (min over salient keys)


@dataclass(frozen=True)
class Thresholds:
    suicide: float = SUICIDE_THRESHOLD
    other: float = OTHER_FLAG_THRESHOLD
    acuity_urgent_index: int = ACUITY_URGENT_INDEX
    resuscitation_prob: float = RESUSCITATION_PROB_THRESHOLD
    abstain_flag: float = ABSTAIN_FLAG_THRESHOLD
    version: str = VERSION
