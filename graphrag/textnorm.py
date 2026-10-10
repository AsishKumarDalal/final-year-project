"""Text normalisation and the validation gate.

Transcribed from ``docs/rag_docs/graph_making.md`` §6 ("the gate: validation
pipeline per triple") and ``docs/rag_docs/solution.md`` §3 (the suffix
whitelist).

Two rules from those docs drive everything here:

1. **LLM output is a suggestion, not data.** Every triple is checked before it
   touches the graph.
2. **Over-merging is worse than under-merging.** Two fragments of one entity cost
   some edge weight; one false merge poisons every future traversal. So the
   suffix whitelist is deliberately narrow — it strips ``inc``/``corp``/``ltd``
   but keeps ``motors``, ``group`` and ``energy``, because "General Motors" and
   "Tesla Energy" are real names.

The gate returns a *reason string* on rejection rather than a bool, so every
rejection is auditable — a silent drop is indistinguishable from a chunk that was
never extracted.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# --- entity types -----------------------------------------------------------
# The rag_docs design record uses a business-entity taxonomy. Our domain is
# clinical, so the list below is the medical one from
# docs/rag_implementationplan.md §2. It is NOT yet domain-reviewed; that is an
# open question recorded in docs/graphrag_plan.md §7.
VALID_ENTITY_TYPES: frozenset[str] = frozenset(
    {
        "symptom",
        "condition",
        "test",
        "drug",
        "procedure",
        "body_system",
        "guideline",
        "population",
    }
)

MAX_HEAD_TAIL_CHARS = 100
MAX_RELATION_CHARS = 60

# Legal-form noise. Purely syntactic — safe to strip without world knowledge.
CORPORATE_SUFFIXES: frozenset[str] = frozenset(
    {"inc", "corp", "ltd", "plc", "gmbh", "ag", "llc"}
)

# Words that carry no identity in an entity name.
STOPWORDS: frozenset[str] = frozenset(
    {"a", "an", "the", "of", "and", "or", "in", "on", "at", "to", "for"}
)

# graph_making.md §6: reject names that begin with a quantifier — these are
# always bucket names, never entities ("other automakers", "various countries").
VAGUE_PREFIX_RE = re.compile(
    r"^(other|others|various|several|many|some|multiple|numerous|few|both|"
    r"companies|company|organisations|organizations|governments?|"
    r"countries|countries|cities|hospitals|patients|people|persons|"
    r"doctors?|physicians?|nurses?|drugs?|medications?|treatments?|"
    r"symptoms?|conditions?|tests?|procedures?|diseases?)\b",
    re.IGNORECASE,
)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_RELATION_RE = re.compile(r"[^A-Z0-9]+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """``"Tesla, Inc."`` -> ``"tesla_inc"``. Used for document and chunk ids."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return _SLUG_RE.sub("_", ascii_text.lower()).strip("_")


def normalize_entity(name: str) -> str:
    """Build the canonical merge key for an entity mention.

    lowercase -> strip punctuation -> drop stop words -> strip corporate
    suffixes. ``"Tesla, Inc."`` and ``"Tesla Inc"`` both become ``"tesla"``;
    ``"Tesla Motors"`` stays ``"tesla motors"``, because "motors" is a real word
    and stripping it would over-merge ("General Motors" -> "General").
    """
    text = unicodedata.normalize("NFKC", name or "").lower()
    text = _PUNCT_RE.sub(" ", text)
    tokens = [t for t in _WS_RE.split(text.strip()) if t and t not in STOPWORDS]
    # Strip repeatedly: "tesla inc ltd" -> "tesla".
    while tokens and tokens[-1] in CORPORATE_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


_LEADING_FUNCTION_WORDS: frozenset[str] = frozenset(
    {"IS", "WAS", "WERE", "ARE", "BE", "BEEN", "BEING", "HAS", "HAVE", "HAD",
     "DOES", "DO", "DID", "CAN", "MAY", "MIGHT", "WILL", "WOULD",
     "THE", "A", "AN"}
)


def normalize_relation(relation: str) -> str:
    """``"is CEO of"`` -> ``"CEO_OF"``.

    Leading function words are dropped: an LLM writes "is the CEO of", "has a
    symptom of", "can be caused by", and all of those describe the same edge as
    the bare label. Keeping the verb would split one relation into three nodes.
    """
    text = (relation or "").strip().upper()
    text = _RELATION_RE.sub(" ", text)
    tokens = [t for t in text.split() if t]
    while tokens and tokens[0] in _LEADING_FUNCTION_WORDS:
        tokens.pop(0)
    return "_".join(tokens)


def display_name(name: str) -> str:
    """The human-facing form. Merge keys are stripped; display names are not."""
    text = _WS_RE.sub(" ", (name or "").strip())
    return text.rstrip(" ,;:")


def split_compound(name: str) -> list[str]:
    """``"A and B"`` -> ``["A", "B"]``.

    A compound entity name jams two entities into one node. Only split on the
    bare conjunction; a name that merely contains the word "and" as part of
    another word is untouched because we match on whole tokens.
    """
    parts = [p.strip() for p in re.split(r"\s+and\s+", name or "", flags=re.IGNORECASE)]
    cleaned = [p for p in parts if p]
    if len(cleaned) > 1:
        return cleaned
    return [name.strip()] if name and name.strip() else []


def is_vague(name: str) -> bool:
    return bool(VAGUE_PREFIX_RE.match((name or "").strip()))


def name_token_count(name: str) -> int:
    return len([t for t in (name or "").split() if t])


def validate_entity_name(
    name: str, *, max_mention_tokens: int = 7
) -> str | None:
    """Validate a standalone entity mention. ``None`` means acceptable."""
    if not name or not name.strip():
        return "missing_field"
    if len(name) > MAX_HEAD_TAIL_CHARS:
        return "entity_too_long"
    if is_vague(name):
        return "vague_entity"
    if name_token_count(name) > max_mention_tokens:
        return "name_too_many_tokens"
    return None


def validate_triple(
    head: str,
    relation: str,
    tail: str,
    *,
    max_mention_tokens: int = 7,
) -> str | None:
    """Return ``None`` if the triple is acceptable, else a rejection reason.

    Order matters and is taken directly from the gate diagram in
    graph_making.md §6.
    """
    if not head or not relation or not tail:
        return "missing_field"
    if not isinstance(head, str) or not isinstance(tail, str):
        return "non_string_entity"
    if head.strip() == tail.strip():
        return "self_loop"
    if len(head) > MAX_HEAD_TAIL_CHARS or len(tail) > MAX_HEAD_TAIL_CHARS:
        return "entity_too_long"
    if len(relation) > MAX_RELATION_CHARS:
        return "relation_too_long"
    if is_vague(head) or is_vague(tail):
        return "vague_entity"
    if name_token_count(head) > max_mention_tokens or name_token_count(tail) > max_mention_tokens:
        return "name_too_many_tokens"
    return None


def normalize_entity_type(raw: str | None) -> str:
    """Map a model's type label onto ``VALID_ENTITY_TYPES``.

    Unknown types collapse to ``condition`` rather than being dropped: losing a
    node entirely breaks traversal, whereas a wrong-but-present type is fixed by
    the type-backfill vote (ingestion stage I8).
    """
    text = (raw or "").strip().lower()
    if text in VALID_ENTITY_TYPES:
        return text
    return "condition"


def iter_entity_surfaces(names: Iterable[str]) -> list[str]:
    """Expand compound names into their separate surfaces, preserving order."""
    out: list[str] = []
    for name in names:
        out.extend(split_compound(name))
    return out