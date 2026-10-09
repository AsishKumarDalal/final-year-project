"""Embeddings: local MiniLM client plus a deterministic stub.

The pinned model is ``all-MiniLM-L6-v2``, 384 dimensions, downloaded once and
run locally — no embedding API calls, no cost, reproducible output (Plan D18).

Unit tests use :class:`StubEmbedder` and never import ``sentence_transformers``.
The real client imports it lazily so a missing install fails with a named
error at first use, not an ImportError at ``import graphrag``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field


@dataclass
class StubEmbedder:
    """Deterministic hash vectors. Same interface as the real client; for tests
    and offline smoke runs only — never in a production path."""

    dim: int = 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            # Repeat the digest to fill dim, then centre to unit-ish scale.
            raw = [(digest[i % len(digest)] / 127.5 - 1.0) for i in range(self.dim)]
            norm = math.sqrt(sum(v * v for v in raw)) or 1.0
            vectors.append([v / norm for v in raw])
        return vectors

    @property
    def model_name(self) -> str:
        return "stub"


@dataclass
class MiniLMEmbedder:
    """Local ``all-MiniLM-L6-v2``. Loads once; embed calls are pure inference."""

    model_id: str = "sentence-transformers/all-MiniLM-L6-v2"
    batch_size: int = 64
    _model: object = field(default=None, repr=False)

    @property
    def dim(self) -> int:
        return 384

    @property
    def model_name(self) -> str:
        return self.model_id

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed in this interpreter. "
                "Install it (pip install sentence-transformers) or use StubEmbedder "
                "for offline tests."
            ) from exc
        self._model = SentenceTransformer(self.model_id)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._load()
        assert self._model is not None
        vectors = self._model.encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [list(map(float, row)) for row in vectors]


def cosine(first: list[float], second: list[float]) -> float:
    """Cosine similarity. Inputs from MiniLM are already normalised; the stub
    normalises too, so this is a plain dot product with a guard."""
    if not first or not second or len(first) != len(second):
        return 0.0
    dot = sum(a * b for a, b in zip(first, second))
    return max(-1.0, min(1.0, dot))


def describe_for_embedding(display: str, node_type: str, mentions: int,
                           aliases: list[str]) -> str:
    """Contextualised node text. Bare names ("tesla") embed ambiguously —
    embed_research.md §4b: describe, don't just name."""
    alias_bit = f" aliases: {', '.join(aliases[:4])}" if aliases else ""
    return f"{display} — {node_type} — {mentions} mentions{alias_bit}"