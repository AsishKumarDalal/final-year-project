"""Cost ledger for Track B indexing.

Every LLM call records its tokens and latency here, tagged by stage. This is
the data behind ``make rag-cost`` (docs/graphrag_plan.md §4): tokens and cost per
indexing stage, measured rather than estimated.

The ledger never sees the API key — only token counts.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StageUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    latency_s: float = 0.0

    def as_dict(self) -> dict:
        total_out = self.completion_tokens
        text_out = max(0, total_out - self.reasoning_tokens)
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "text_tokens": text_out,
            "total_tokens": self.prompt_tokens + total_out,
            "latency_s": round(self.latency_s, 2),
            "mean_latency_s": round(self.latency_s / self.calls, 2) if self.calls else 0.0,
        }


@dataclass
class CostLedger:
    """Append-only accumulator. One instance per indexing run."""

    model: str = ""
    stages: dict[str, StageUsage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(
        self,
        *,
        stage: str,
        prompt_tokens: int,
        completion_tokens: int,
        reasoning_tokens: int = 0,
        latency_s: float = 0.0,
    ) -> None:
        with self._lock:
            entry = self.stages.setdefault(stage, StageUsage())
            entry.calls += 1
            entry.prompt_tokens += max(0, prompt_tokens)
            entry.completion_tokens += max(0, completion_tokens)
            entry.reasoning_tokens += max(0, reasoning_tokens)
            entry.latency_s += max(0.0, latency_s)

    @property
    def total_calls(self) -> int:
        return sum(entry.calls for entry in self.stages.values())

    @property
    def total_tokens(self) -> int:
        return sum(
            entry.prompt_tokens + entry.completion_tokens
            for entry in self.stages.values()
        )

    def summary(self) -> dict:
        return {
            "model": self.model,
            "total_calls": self.total_calls,
            "total_tokens": self.total_tokens,
            "stages": {
                name: entry.as_dict() for name, entry in sorted(self.stages.items())
            },
        }

    def save(self, path: Path) -> None:
        """Write ``rag-cost.json``. The artifact — never hand-edited."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.summary(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "CostLedger":
        payload = json.loads(path.read_text(encoding="utf-8"))
        ledger = cls(model=payload.get("model", ""))
        for name, raw in (payload.get("stages", {}) or {}).items():
            ledger.stages[name] = StageUsage(
                calls=int(raw.get("calls", 0)),
                prompt_tokens=int(raw.get("prompt_tokens", 0)),
                completion_tokens=int(raw.get("completion_tokens", 0)),
                reasoning_tokens=int(raw.get("reasoning_tokens", 0)),
                latency_s=float(raw.get("latency_s", 0.0)),
            )
        return ledger