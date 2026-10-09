"""System-1 client (Laya) for Track B decisions.

Used for: merge judging (I9/U3), query routing (Q1/U7), evidence reranking
(Q4/U5), answer guarding (Q6/U8). Decisions only — this client can never
generate text, which is exactly why it is safe to consult during indexing and
retrieval: there is no output channel for it to hallucinate through.

The model is expected at ``LAYA_BASE_URL`` (default ``http://127.0.0.1:8000``),
``POST /v1/systemone`` with ``{model, state, questions}``. Measured 2026-10-09:
~2.3 s per call on this box, byte-identical across runs — so batch many
questions per call and never use it for bulk per-chunk work.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

Transport = Callable[[str, bytes, Mapping[str, str], int], tuple[int | None, str]]


def _default_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: int):
    request = urllib.request.Request(url, data=body, method="POST", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return exc.code, ""
    except Exception:  # noqa: BLE001
        return None, ""


class System1Error(RuntimeError):
    pass


@dataclass
class System1Client:
    """Thin wrapper over the local decision endpoint. Stateless."""

    base_url: str = "http://127.0.0.1:8000"
    model_id: str = "laya"
    timeout_s: int = 120
    transport: Transport = field(default=_default_transport, repr=False)

    def __post_init__(self) -> None:
        self.endpoint = self.base_url.rstrip("/") + "/v1/systemone"

    def decide(self, state: str, questions: Mapping[str, Mapping[str, Any]]) -> dict:
        """One call, many questions. Returns the ``answers`` mapping."""
        body = json.dumps(
            {"model": self.model_id, "state": state, "questions": dict(questions)}
        ).encode("utf-8")
        status, text = self.transport(
            self.endpoint, body, {"Content-Type": "application/json"}, self.timeout_s
        )
        if status != 200:
            raise System1Error(f"system-one endpoint returned {status}: {text[:200]}")
        try:
            return json.loads(text).get("answers", {})
        except json.JSONDecodeError as exc:
            raise System1Error(f"system-one response not JSON: {exc}") from exc

    def available(self) -> bool:
        try:
            self.decide("ping", {"ok": {"type": "noul", "instructions": "Is this a ping?"}})
            return True
        except System1Error:
            return False


# ------------------------------------------------------------------ verdicts
def choice_probability(answer: Mapping[str, Any], option: str) -> float:
    """P(option) from a ``choice`` answer; 0.0 when absent or malformed."""
    try:
        return float((answer.get("probabilities", {}) or {}).get(option, 0.0))
    except (TypeError, ValueError):
        return 0.0


def merge_question(
    names: Sequence[str],
    *,
    context_lines: Sequence[str],
    option_merge: str = "merge",
    option_split: str = "keep_separate",
) -> dict[str, Any]:
    """One ``choice`` question judging whether names are the same entity.

    The judge sees names plus graph evidence (types, mention counts, real
    edges) — solution.md §6 proved names-only prompts merge sycophantically.
    The verdict is a probability, so the caller can gate on it (U3).
    """
    lines = [f"- {line}" for line in context_lines]
    return {
        "type": "choice",
        "instructions": (
            "Do these names refer to the SAME real-world entity? Judge by the "
            "evidence, not string similarity. If unsure, keep them separate — "
            "a missed merge is a small error, a wrong merge poisons the graph.\n"
            + "\n".join(lines[:24])
        ),
        "criteria": {
            option_merge: "same entity: " + "; ".join(names[:8]),
            option_split: "different entities that happen to look alike",
        },
    }


def routing_question() -> dict[str, Any]:
    """Q1/U7: which retrieval strategy answers this question? (D17)"""
    return {
        "type": "choice",
        "instructions": "Which retrieval strategy best answers this question?",
        "criteria": {
            "local": "about one specific condition, drug, test, or symptom",
            "global": "about patterns or themes across many conditions",
            "basic": "a direct lookup of a specific fact",
            "none": "not a medical information question at all",
        },
    }


def support_question(claim: str, evidence: str) -> dict[str, Any]:
    """Q6/U8: is the claim supported by the evidence text?"""
    return {
        "type": "choice",
        "instructions": (
            f"Claim: {claim}\nEvidence: {evidence[:2000]}\n"
            "Is the claim directly supported by the evidence?"
        ),
        "criteria": {
            "supported": "the evidence states the claim",
            "unsupported": "the evidence does not state the claim",
        },
    }


def score_questions(
    query: str, candidates: Sequence[Mapping[str, str]], *, score_levels: int = 5
) -> dict[str, dict[str, Any]]:
    """Q4/U5: one ``score`` question per candidate — batched in one call."""
    questions: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(candidates):
        questions[f"cand_{index}"] = {
            "type": "score",
            "instructions": (
                f"Query: {query}\nCandidate: {candidate.get('text', '')[:800]}\n"
                "How likely is this candidate to help answer the query?"
            ),
            "criteria": ["useless", "weak", "related", "helpful", "answers_it"][
                :score_levels
            ],
        }
    return questions


def score_value(answer: Mapping[str, Any]) -> float:
    """Expected score normalised to [0, 1] from a ``score`` answer."""
    try:
        probs = answer.get("probabilities", {}) or {}
        levels = sorted((int(k), float(v)) for k, v in probs.items())
        if not levels:
            return 0.0
        expected = sum(k * p for k, p in levels)
        top = max(k for k, _ in levels)
        return expected / top if top else 0.0
    except (TypeError, ValueError):
        return 0.0


def now_ms() -> int:
    return int(time.time() * 1000)