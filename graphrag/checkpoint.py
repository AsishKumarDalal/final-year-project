"""Extraction checkpointing and the content-hash cache.

Transcribed from ``docs/rag_docs/graph_making.md`` §5, plus the content-hash
cache that ``docs/decisions.md`` (2026-10-08) promotes from "cost optimisation"
to the thing standing between a three-day model window and a total loss.

Two artefacts:

``data/triples.jsonl``
    Append-only, one JSON object per extracted chunk, written **before** the
    graph is touched. A run interrupted at minute 80 resumes without re-paying
    for the 79 minutes already done.

``data/cache/extraction/<hash>.json``
    Extraction responses keyed by the SHA-256 of the chunk **text**. Re-running
    the indexer after editing the merge rules must never re-bill the LLM, and a
    document re-fetched with cosmetic edits should only re-bill the chunks that
    actually changed.

``data/failed_chunks.json``
    Permanently failed chunk ids. One stubborn chunk must not block a run, and
    it must never be retried blindly on every subsequent run.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# Extraction output contract. Consumed by graph.py and the merge funnel; which
# kind of model produced it does not matter (NER_model.md §1.2).
OUTPUT_CONTRACT = '{"entities":[{"name","type"}],"triples":[{"head","relation","tail"}]}'


@dataclass
class CheckpointStats:
    extracted: int = 0
    cache_hits: int = 0
    failed: int = 0
    cache_writes: int = 0

    def as_dict(self) -> dict:
        return {
            "extracted": self.extracted,
            "cache_hits": self.cache_hits,
            "failed": self.failed,
            "cache_writes": self.cache_writes,
        }


@dataclass
class ExtractionCheckpoint:
    """Append-only extraction log plus a content-hash response cache."""

    triples_path: Path
    cache_dir: Path
    failed_path: Path
    stats: CheckpointStats = field(default_factory=CheckpointStats)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _done: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        self.triples_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.failed_path.parent.mkdir(parents=True, exist_ok=True)
        self._done = self._load_done()

    # ------------------------------------------------------------ resuming
    def _load_done(self) -> set[str]:
        """Chunk ids already paid for. Also recovers truncated final lines."""
        done: set[str] = set()
        if not self.triples_path.exists():
            return done
        with self.triples_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    done.add(json.loads(line)["chunk_id"])
                except (json.JSONDecodeError, KeyError):
                    # A crash mid-write leaves a partial last line. Skip it
                    # rather than abandoning the file.
                    continue
        return done

    def is_done(self, chunk_id: str) -> bool:
        return chunk_id in self._done

    @property
    def done_count(self) -> int:
        return len(self._done)

    # -------------------------------------------------------------- cache
    def _cache_path(self, content_hash: str) -> Path:
        return self.cache_dir / f"{content_hash}.json"

    def cache_get(self, content_hash: str) -> dict[str, Any] | None:
        path = self._cache_path(content_hash)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        self.stats.cache_hits += 1
        return payload.get("result")

    def cache_put(self, content_hash: str, result: dict[str, Any]) -> None:
        payload = {
            "content_hash": content_hash,
            "output_contract": OUTPUT_CONTRACT,
            "result": result,
        }
        self._cache_path(content_hash).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        self.stats.cache_writes += 1

    # -------------------------------------------------------------- record
    def record(
        self,
        *,
        chunk_id: str,
        doc_id: str,
        title: str,
        content_hash: str,
        result: dict[str, Any],
    ) -> None:
        """Append one extraction result. Written before the graph is updated."""
        entry = {
            "chunk_id": chunk_id,
            "doc_id": doc_id,
            "title": title,
            "content_hash": content_hash,
            "entities": result.get("entities", []),
            "triples": result.get("triples", []),
        }
        with self._lock:
            with self.triples_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
            self._done.add(chunk_id)
            self.stats.extracted += 1
        self.cache_put(content_hash, result)

    def record_failure(self, chunk_id: str, reason: str) -> None:
        """Record a permanent failure. Never retried automatically."""
        with self._lock:
            failures = self.load_failures()
            failures[chunk_id] = reason
            self.failed_path.write_text(
                json.dumps(failures, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            self._done.add(chunk_id)  # do not retry on the next run
            self.stats.failed += 1

    def load_failures(self) -> dict[str, str]:
        if not self.failed_path.exists():
            return {}
        try:
            payload = json.loads(self.failed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def is_failed(self, chunk_id: str) -> bool:
        return chunk_id in self.load_failures()

    # ---------------------------------------------------------- iteration
    def iter_results(self) -> Iterator[dict[str, Any]]:
        """Replay every stored extraction — how the graph rebuilds for free."""
        if not self.triples_path.exists():
            return
        with self.triples_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue