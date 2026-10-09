"""Configuration, read from the environment only.

No credential is ever defaulted, hardcoded, or logged. ``from_env`` loads a
repo-local ``.env`` if one exists, but every value can equally come from real
environment variables — which is what CI uses.

There is deliberately no ``.env`` parser dependency: the project treats the
architecture gate as zero-dependency (Plan.md D9), and a settings loader is not
worth breaking that for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .llm import DEFAULT_BASE_URL as DEFAULT_LLM_BASE_URL
from pathlib import Path

# Chunking defaults follow PROMPT.md's question of roughly 1200 tokens per text
# unit with ~100 tokens of overlap. At ~4 characters per token that is 4800/400.
# The rag_docs design record used 1000 chars for a Wikipedia test corpus; both
# are legitimate, so this is a setting rather than a constant (graphrag_plan.md).
DEFAULT_CHUNK_TARGET_CHARS = 4800
DEFAULT_CHUNK_OVERLAP_CHARS = 400


def load_dotenv(path: Path | None = None) -> None:
    """Populate ``os.environ`` from a ``KEY=VALUE`` file. Existing values win."""
    env_path = path or (Path(__file__).resolve().parents[1] / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        # os.environ.setdefault: a real environment variable beats the file.
        os.environ.setdefault(key, value)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Everything the indexer needs, resolved once at startup."""

    # --- paths -------------------------------------------------------------
    repo_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])
    corpus_dirname: str = "corpus_test"      # under data/
    triples_path: Path | None = None         # data/triples.jsonl
    graph_path: Path | None = None           # data/graph.json
    failed_path: Path | None = None          # data/failed_chunks.json
    cache_dirname: str = "cache/extraction"  # under data/

    # --- chunking ----------------------------------------------------------
    chunk_target_chars: int = DEFAULT_CHUNK_TARGET_CHARS
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS

    # --- extraction --------------------------------------------------------
    max_triples: int = 10          # terse output is the dominant cost lever
    max_mentions: int = 7          # a name longer than this is funding-round junk
    temperature: float = 0.0       # extraction is a lookup: same input, same output
    reasoning_effort: str | None = "minimal"  # measured 2.5x output cut; None = unset
    request_timeout_s: int = 120
    max_retries: int = 3

    # --- stores ------------------------------------------------------------
    qdrant_url: str = "http://127.0.0.1:6333"
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384

    # --- System 1 (Laya) — decisions only, never generation ----------------
    laya_base_url: str = "http://127.0.0.1:8000"
    laya_model_id: str = "laya"

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model_id: str | None = None

    # ---------------------------------------------------------------- helpers
    @property
    def data_dir(self) -> Path:
        return self.repo_root / "data"

    @property
    def corpus_dir(self) -> Path:
        return self.data_dir / self.corpus_dirname

    @property
    def triples_file(self) -> Path:
        return self.triples_path or (self.data_dir / "triples.jsonl")

    @property
    def graph_file(self) -> Path:
        return self.graph_path or (self.data_dir / "graph.json")

    @property
    def failed_file(self) -> Path:
        return self.failed_path or (self.data_dir / "failed_chunks.json")

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / self.cache_dirname

    def require_llm(self) -> tuple[str, str, str]:
        """Return the generative-LLM triple, or explain precisely what is missing.

        Resolution order, each step documented so the next human knows where a
        value came from:
        - base URL: ``LLM_BASE_URL`` → ``OPENCODE_ZEN_BASE_URL`` → Zen default
        - key:      ``LLM_API_KEY`` → ``OPENCODE_ZEN_API``
        - model:    ``LLM_MODEL_ID`` → ``OPENCODE_MODEl`` → ``OPENCODE_MODEL``

        The locally hosted System-1 model (Laya) cannot serve here: it is a
        decision model, returns ``output_tokens: 0``, and never emits text.
        Extraction needs a generative endpoint.
        """
        missing = [
            name
            for name, value in (
                ("LLM_BASE_URL", self.llm_base_url),
                ("LLM_API_KEY", self.llm_api_key),
                ("LLM_MODEL_ID", self.llm_model_id),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "extraction needs a generative LLM; missing from the environment: "
                + ", ".join(missing)
                + ". (The System-1 model at "
                + self.laya_base_url
                + " cannot extract — it returns probabilities, not text.)"
            )
        return self.llm_base_url or "", self.llm_api_key or "", self.llm_model_id or ""

    def require_stores(self) -> None:
        if not self.neo4j_password:
            raise RuntimeError(
                "NEO4J_PASSWORD is not set. Credentials come from the environment "
                "and are never committed (PROMPT.md 7.4)."
            )

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        root = Path(__file__).resolve().parents[1]
        return cls(
            repo_root=root,
            corpus_dirname=_env("GRAPHRAG_CORPUS_DIR", "corpus_test") or "corpus_test",
            chunk_target_chars=_env_int(
                "GRAPHRAG_CHUNK_CHARS", DEFAULT_CHUNK_TARGET_CHARS
            ),
            chunk_overlap_chars=_env_int(
                "GRAPHRAG_CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP_CHARS
            ),
            max_triples=_env_int("GRAPHRAG_MAX_TRIPLES", 10),
            max_mentions=_env_int("GRAPHRAG_MAX_MENTION_TOKENS", 7),
            reasoning_effort=_env("GRAPHRAG_REASONING_EFFORT", "minimal") or None,
            request_timeout_s=_env_int("GRAPHRAG_TIMEOUT_S", 120),
            max_retries=_env_int("GRAPHRAG_MAX_RETRIES", 3),
            qdrant_url=_env("QDRANT_URL", "http://127.0.0.1:6333") or "",
            neo4j_uri=_env("NEO4J_URI", "bolt://127.0.0.1:7687") or "",
            neo4j_user=_env("NEO4J_USER", "neo4j") or "neo4j",
            neo4j_password=_env("NEO4J_PASSWORD"),
            embedding_model=_env(
                "GRAPHRAG_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
            )
            or "",
            laya_base_url=_env("LAYA_BASE_URL", "http://127.0.0.1:8000") or "",
            laya_model_id=_env("LAYA_MODEL_ID", "laya") or "laya",
            llm_base_url=(
                _env("LLM_BASE_URL")
                or _env("OPENCODE_ZEN_BASE_URL")
                or DEFAULT_LLM_BASE_URL
            ),
            llm_api_key=_env("LLM_API_KEY") or _env("OPENCODE_ZEN_API"),
            llm_model_id=(
                _env("LLM_MODEL_ID") or _env("OPENCODE_MODEl") or _env("OPENCODE_MODEL")
            ),
        )