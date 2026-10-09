"""Paragraph-aware chunking with stable provenance ids.

Transcribed from ``docs/rag_docs/graph_making.md`` §3 ("Chunking").

Three properties this module exists to guarantee:

* **Provenance from birth.** Every chunk gets an id like ``Myocardial_Infarct::7``
  the moment it is created. Every triple extracted from it carries that id
  forever. This is what makes a citation resolvable, which PROMPT.md §7.3.4
  requires as a hard failure rather than a warning.
* **Boundary insurance.** Each chunk starts with the tail of the previous one, so
  a fact stated across a boundary is seen whole by at least one chunk.
* **Determinism.** Same input, same chunks, same ids, every run. §7.2.1 depends
  on it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .config import DEFAULT_CHUNK_OVERLAP_CHARS, DEFAULT_CHUNK_TARGET_CHARS
from .textnorm import slugify

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    """One text unit. ``chunk_id`` is the provenance handle for every citation."""

    chunk_id: str
    doc_id: str
    title: str
    index: int
    text: str
    char_start: int
    char_end: int

    @property
    def content_hash(self) -> str:
        """Stable hash of the chunk text — the extraction cache key.

        Hashing the *text* rather than the id means a document re-fetched with
        cosmetic edits only re-bills the chunks that actually changed.
        """
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        return digest[:32]

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "title": self.title,
            "index": self.index,
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "content_hash": self.content_hash,
        }


def _split_oversized(paragraph: str, target_chars: int) -> list[str]:
    """Break a paragraph that is larger than the target, at sentence boundaries."""
    if len(paragraph) <= target_chars:
        return [paragraph]
    pieces: list[str] = []
    buffer = ""
    for sentence in _SENTENCE_SPLIT_RE.split(paragraph):
        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if len(candidate) <= target_chars:
            buffer = candidate
            continue
        if buffer:
            pieces.append(buffer)
        # A single sentence longer than the target: hard-wrap it.
        while len(sentence) > target_chars:
            pieces.append(sentence[:target_chars])
            sentence = sentence[target_chars:]
        buffer = sentence
    if buffer:
        pieces.append(buffer)
    return pieces


def chunk_text(
    text: str,
    *,
    target_chars: int = DEFAULT_CHUNK_TARGET_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> list[tuple[str, int, int]]:
    """Pack paragraphs into chunks.

    Returns ``(chunk_text, char_start, char_end)`` triples. Offsets are relative
    to the input string so a citation can be located back in the source document.
    """
    if target_chars <= 0:
        raise ValueError("target_chars must be positive")
    if overlap_chars < 0 or overlap_chars >= target_chars:
        raise ValueError("overlap_chars must be >= 0 and < target_chars")
    if not text or not text.strip():
        return []

    paragraphs: list[tuple[str, int]] = []
    offset = 0
    for raw in re.split(r"\n\s*\n", text):
        stripped = raw.strip()
        if not stripped:
            offset += len(raw) + 2
            continue
        start = text.find(stripped, offset)
        if start < 0:
            start = offset
        for piece in _split_oversized(stripped, target_chars):
            piece_start = text.find(piece, start)
            if piece_start < 0:
                piece_start = start
            paragraphs.append((piece, piece_start))
            start = piece_start + len(piece)
        offset += len(raw) + 2

    chunks: list[tuple[str, int, int]] = []
    buffer: list[str] = []
    buffer_len = 0
    buffer_start = 0
    buffer_end = 0
    limit = len(text)

    def flush() -> None:
        nonlocal buffer, buffer_len, buffer_start, buffer_end
        if not buffer:
            return
        body = "\n\n".join(buffer)
        # The overlap prefix is real text taken from immediately before the
        # buffer, so a fact spanning a boundary is visible whole in one chunk.
        prefix_start = buffer_start - overlap_chars if chunks else buffer_start
        prefix_start = max(0, prefix_start)
        prefix = text[prefix_start:buffer_start]
        full = f"{prefix}\n\n{body}" if prefix.strip() else body
        chunks.append((full.strip(), prefix_start, min(limit, buffer_end)))
        buffer = []
        buffer_len = 0

    for para_text, para_start in paragraphs:
        if buffer_len and buffer_len + len(para_text) + 2 > target_chars:
            flush()
        if not buffer:
            buffer_start = para_start
        buffer.append(para_text)
        buffer_len += len(para_text) + 2
        buffer_end = para_start + len(para_text)
    flush()

    return [(body, start, end) for body, start, end in chunks if body.strip()]


def chunk_document(
    title: str,
    text: str,
    *,
    target_chars: int = DEFAULT_CHUNK_TARGET_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> list[Chunk]:
    """Chunk a whole document and stamp provenance ids on every chunk."""
    doc_id = slugify(title)
    chunks: list[Chunk] = []
    for index, (body, start, end) in enumerate(
        chunk_text(text, target_chars=target_chars, overlap_chars=overlap_chars)
    ):
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}::{index}",
                doc_id=doc_id,
                title=title,
                index=index,
                text=body,
                char_start=start,
                char_end=end,
            )
        )
    return chunks