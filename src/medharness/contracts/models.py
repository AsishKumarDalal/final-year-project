"""Track A contracts (M1). Pydantic v2. The ONLY module rules/ may import.

Non-goals (PROMPT.md sect 4): no diagnosis output, no treatment/dosing advice,
no patient-record storage, synthetic cases only.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ActorRole = Literal["patient", "nurse", "doctor"]
AcuityLevel = Literal["routine", "soon", "urgent", "resuscitation"]
ACUITY_ORDER = ("routine", "soon", "urgent", "resuscitation")
SCHEMA_VERSION = "v1"


class HarnessRequest(BaseModel):
    text: str = Field(min_length=1)
    actor_role: ActorRole = "patient"


class RedFlag(BaseModel):
    probability: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)


class LayaVerdict(BaseModel):
    """Layer 1 output: numbers only, never prose."""

    acuity: AcuityLevel
    acuity_distribution: dict[str, float]
    red_flags: dict[str, RedFlag]
    body_system: str = "other"
    abstained: bool = False
    schema_version: str = SCHEMA_VERSION


class EscalationReason(BaseModel):
    flag: str
    probability: float
    threshold: float


class Escalation(BaseModel):
    level: str
    reasons: list[EscalationReason]
    instruction: str = (
        "This presentation may be life-threatening. "
        "Seek immediate emergency medical care now. "
        "Do not wait for further assessment."
    )
    llm_called: bool = False


class ToolFact(BaseModel):
    facts: list[str] = []
    source_ids: list[str] = []
    source_type: str = ""
    search_mode: str = ""
    not_found: bool = False


class HarnessResponse(BaseModel):
    decision: str
    escalated: bool
    escalation: Escalation | None = None
    explanation: str = ""
    citations: list[str] = []
    decision_trace: dict = {}


class SearchResult(BaseModel):
    """External deep-retrieval result (full graph walk). Facts carry text_unit
    citations only; community/global summaries are interpretation, never facts."""
    facts: list[str] = []
    source_ids: list[str] = []
    search_mode: str = "none"          # local | global | basic | none
    generated_interpretation: str = ""
    refusal: str | None = None
    not_found: bool = False
