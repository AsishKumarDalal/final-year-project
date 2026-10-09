"""FAISS vector store for dev/test only (never product code).

Same method names as ``graphrag.stores.MemoryVectorStore`` so the same
``QueryEngine`` runs on either backend:

    ensure_collections(*names) / upsert / search / count / save / load

Backend: ``faiss.IndexFlatIP`` over L2-normalised float32 vectors (= cosine).
Payloads live in a plain dict (FAISS holds geometry only). A numpy copy of the
vectors is kept alongside so ``payload_filter`` searches stay correct — the
corpus is small in dev, correctness beats sharding.

``faiss`` is imported lazily so a missing install fails with a named error at
first use, never at ``import dev``. Not part of ``make validate``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass
class ScoredHit:
    id: str
    score: float
    payload: dict[str, Any]


def _normalise(vecs):
    import numpy as np

    mat = np.asarray(vecs, dtype=np.float32)
    if mat.ndim == 1:
        mat = mat.reshape(1, -1)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (mat / norms).astype(np.float32)


@dataclass
class FaissVectorStore:
    """Dev FAISS store. One FlatIP index per collection + payload sidecar."""

    dim: int = 384
    _indexes: dict = field(default_factory=dict, repr=False)
    _ids: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _payloads: dict[str, dict[str, dict]] = field(default_factory=dict, repr=False)
    _vectors: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    def _require_faiss(self):
        try:
            import faiss  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "faiss is not installed in this interpreter "
                "(pip install faiss-cpu) — dev-only dependency."
            ) from exc
        import faiss

        return faiss

    def ensure_collections(self, *names: str) -> None:
        faiss = self._require_faiss()
        for name in names:
            if name not in self._indexes:
                self._indexes[name] = faiss.IndexFlatIP(self.dim)
                self._ids.setdefault(name, [])
                self._payloads.setdefault(name, {})
                self._vectors.setdefault(name, {})

    def upsert(self, collection: str, ids: Sequence[str],
               vectors: Sequence[Sequence[float]],
               payloads: Sequence[Mapping[str, Any]]) -> int:
        faiss = self._require_faiss()
        _ = faiss  # backend assert; geometry goes through _normalise below
        self.ensure_collections(collection)
        mat = _normalise(vectors)
        self._indexes[collection].add(mat)
        id_list = self._ids[collection]
        for pid, vec, pay in zip(ids, mat, payloads):
            if pid not in self._payloads[collection]:
                id_list.append(pid)
            self._payloads[collection][pid] = dict(pay)
            self._vectors[collection][pid] = vec
        return len(ids)

    def search(self, collection: str, vector: Sequence[float], *,
               top_k: int = 10,
               payload_filter: Mapping[str, Any] | None = None) -> list[ScoredHit]:
        import numpy as np

        if collection not in self._indexes:
            return []
        if payload_filter:
            # Small dev corpus: brute-force the filtered subset for correctness.
            q = _normalise([vector])[0]
            scored: list[ScoredHit] = []
            for pid, payload in self._payloads[collection].items():
                if not all(payload.get(k) == v for k, v in payload_filter.items()):
                    continue
                v = self._vectors[collection][pid]
                scored.append(ScoredHit(id=pid, score=float(q @ v),
                                        payload=dict(payload)))
            scored.sort(key=lambda h: h.score, reverse=True)
            return scored[:top_k]
        q = _normalise([vector])
        index = self._indexes[collection]
        if index.ntotal == 0:
            return []
        k = min(top_k, index.ntotal)
        scores, idxs = index.search(q, k)
        id_list = self._ids[collection]
        out: list[ScoredHit] = []
        for score, idx in zip(scores[0], idxs[0]):
            if int(idx) < 0 or int(idx) >= len(id_list):
                continue
            pid = id_list[int(idx)]
            out.append(ScoredHit(id=pid, score=float(score),
                                 payload=dict(self._payloads[collection][pid])))
        _ = np  # keep numpy import honest (used in _normalise)
        return out

    def count(self, collection: str) -> int:
        return len(self._ids.get(collection, []))

    # ---------------------------------------------------------- checkpoint
    def save(self, directory: Path) -> None:
        """Persist one ``<collection>.index`` + ``<collection>.json`` per collection."""
        faiss = self._require_faiss()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "dim.json").write_text(json.dumps({"dim": self.dim}))
        for name, index in self._indexes.items():
            faiss.write_index(index, str(directory / f"{name}.index"))
            (directory / f"{name}.json").write_text(json.dumps(
                {"ids": self._ids[name], "payloads": self._payloads[name]},
                ensure_ascii=False))

    @classmethod
    def load(cls, directory: Path) -> "FaissVectorStore":
        directory = Path(directory)
        dim = int(json.loads((directory / "dim.json").read_text())["dim"])
        store = cls(dim=dim)
        faiss = store._require_faiss()
        for index_path in sorted(directory.glob("*.index")):
            name = index_path.stem
            store._indexes[name] = faiss.read_index(str(index_path))
            meta = json.loads((directory / f"{name}.json").read_text())
            store._ids[name] = list(meta["ids"])
            store._payloads[name] = {k: dict(v) for k, v in meta["payloads"].items()}
            # Rebuild normalised vectors from the index for filtered search.
            n = int(store._indexes[name].ntotal)
            if n:
                import numpy as np

                vecs = {}
                for i, pid in enumerate(store._ids[name]):
                    vecs[pid] = np.asarray(
                        store._indexes[name].reconstruct(int(i)),
                        dtype=np.float32)
                store._vectors[name] = vecs
            else:
                store._vectors[name] = {}
        return store
