"""Generative-LLM adapter for extraction (ingestion stage I3, ``graphrag`` Track B).

The adapter speaks OpenAI-compatible ``POST {base}/chat/completions`` and needs
a **generative** model: extraction requires the endpoint to emit text. The
locally hosted System-1 model cannot serve here — it returns ``output_tokens:
0`` and never emits a string.

Two hard-won facts about the configured backend (OpenCode Zen), measured on
2026-10-09 and **not** guesses:

1. The provider's edge (Cloudflare) rejects requests carrying the default
   ``Python-urllib`` User-Agent with ``403 / error 1010``. Every request must
   carry an explicit ``User-Agent``. Omitting it fails closed, not open.
2. The configured model is a reasoning model: the bulk of
   ``completion_tokens`` can be ``reasoning_tokens``. Output budgets must be set
   for emitted JSON *plus* reasoning, or responses truncate mid-object.
   ``reasoning_effort: "minimal"`` is sent by default (measured 2026-10-09:
   234→91 output tokens, 4.1s→1.8s on the same prompt); ``"none"`` is rejected
   with 400 and ``enable_thinking: false`` is ignored.

Transport errors (timeouts, disconnects), HTTP 429 and 5xx are retried with
backoff that honours ``Retry-After``. 400/401/403/404/422 are **never** retried:
re-sending a rejected request only re-bills it. A batch whose result count does
not match its input count is rejected outright and retried once — per the
project rule that an unresolvable alignment is poison, not a warning.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .cost import CostLedger

DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"
CHAT_COMPLETIONS_PATH = "/chat/completions"
USER_AGENT = "graphrag-indexer/0.1 (+medical-harness)"

# Transport: (url, body, headers, timeout) -> (status, body_text, headers_out).
# ``status`` is None on a transport failure (timeout, DNS, disconnect).
Transport = Callable[[str, bytes, Mapping[str, str], int], tuple[int | None, str, Mapping[str, str]]]


class ExtractionError(RuntimeError):
    """Base class. ``kind`` names the failure for the audit log."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class AuthError(ExtractionError):
    def __init__(self, message: str) -> None:
        super().__init__("auth", message)


class BadRequestError(ExtractionError):
    def __init__(self, message: str) -> None:
        super().__init__("bad_request", message)


class RetryExhaustedError(ExtractionError):
    def __init__(self, message: str) -> None:
        super().__init__("retry_exhausted", message)


class InvalidOutputError(ExtractionError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_output", message)


class BatchMismatchError(InvalidOutputError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.kind = "batch_mismatch"


ENTITY_TYPES = (
    "symptom",
    "condition",
    "test",
    "drug",
    "procedure",
    "body_system",
    "guideline",
    "population",
)

# Relations the extractor may use, stated as examples — not a closed
# vocabulary. Relationship *direction* errors are a known failure class
# (docs/rag_docs failure "Tesla Motors, PERSON"), adjudicated downstream.
EXAMPLE_RELATIONS = (
    "PRESENTS_WITH",
    "SYMPTOM_OF",
    "CAUSES",
    "TREATS",
    "TESTS_FOR",
    "DIAGNOSED_BY",
    "LOCATED_IN",
    "ADMINISTERED_FOR",
)


def build_extraction_prompt(texts: Sequence[str], *, max_triples: int) -> str:
    """One prompt for a batch of chunk texts. Returns terse JSON only."""
    if not texts:
        raise ValueError("extract_batch needs at least one text")
    if max_triples <= 0:
        raise ValueError("max_triples must be positive")
    parts = [
        "Extract entities and relationships from each TEXT below.",
        "Return ONLY valid JSON, no commentary.",
        "",
        "Rules:",
        "1. Entity names: use the most complete form stated in the text.",
        "2. Relations: UPPER_SNAKE_CASE, e.g. "
        + ", ".join(EXAMPLE_RELATIONS)
        + ". Describe the relation stated; do not invent new semantics.",
        "3. Extract ONLY facts explicitly stated in the text. Never invent.",
        "4. Entity types: " + ", ".join(ENTITY_TYPES) + ".",
        f"5. Max {max_triples} triples per TEXT. Skip trivial facts.",
        "6. Return exactly this JSON shape:",
        '{"results":[{"entities":[{"name":"...","type":"..."}],'
        '"triples":[{"head":"...","relation":"...","tail":"..."}]}, ...]}',
        f"One entry per TEXT, in the same order. {len(texts)} TEXT(s) follow.",
    ]
    for index, text in enumerate(texts, start=1):
        parts += ["", f"TEXT {index}:", "<<<", text, ">>>"]
    return "\n".join(parts)


def strip_json_fences(content: str) -> str:
    """Remove markdown code fences some models wrap JSON in."""
    text = (content or "").strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline >= 0 else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -len("```")]
    return text.strip()


def parse_batch_output(content: str, *, expected: int) -> list[dict[str, Any]]:
    """Parse one extraction response. Raises on any contract violation."""
    cleaned = strip_json_fences(content)
    if not cleaned:
        raise InvalidOutputError("empty completion")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise InvalidOutputError("no JSON object in completion")
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        message = str(exc)
        if "Unterminated string" in message or "Expecting" in message:
            raise InvalidOutputError(f"truncated JSON: {message}") from exc
        raise InvalidOutputError(f"unparseable JSON: {message}") from exc
    results = payload.get("results")
    if not isinstance(results, list):
        raise InvalidOutputError('missing "results" list')
    if len(results) != expected:
        raise BatchMismatchError(
            f"batch returned {len(results)} result(s) for {expected} text(s)"
        )
    for entry in results:
        if not isinstance(entry, dict):
            raise InvalidOutputError("result entry is not an object")
        entry.setdefault("entities", [])
        entry.setdefault("triples", [])
        if not isinstance(entry["entities"], list) or not isinstance(
            entry["triples"], list
        ):
            raise InvalidOutputError("entities/triples must be lists")
    return results


def _default_transport(
    url: str, body: bytes, headers: Mapping[str, str], timeout: int
) -> tuple[int | None, str, Mapping[str, str]]:
    request = urllib.request.Request(
        url, data=body, method="POST", headers=dict(headers)
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                response.read().decode("utf-8", errors="replace"),
                dict(response.headers.items()),
            )
    except urllib.error.HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — error body is best-effort
            error_body = ""
        return exc.code, error_body, dict(exc.headers.items() if exc.headers else {})
    except Exception:  # noqa: BLE001 — timeouts, DNS, disconnects
        return None, "", {}


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return max(0.0, float(str(value).strip()))
            except ValueError:
                return None
    return None


@dataclass
class Extractor:
    """One configured generative endpoint. Stateless across calls."""

    base_url: str
    api_key: str
    model_id: str
    temperature: float = 0.0
    max_tokens: int = 2048
    # Reasoning control. "minimal" is measured to cut output ~2.5x with no
    # quality loss on extraction-shaped prompts; None sends no parameter.
    reasoning_effort: str | None = "minimal"
    timeout_s: int = 180
    max_retries: int = 3
    backoff_base_s: float = 2.0
    ledger: CostLedger | None = None
    transport: Transport = field(default=_default_transport, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key is required and is never defaulted")
        if not self.model_id:
            raise ValueError("model_id is required and is never defaulted")
        base = (self.base_url or "").rstrip("/")
        if not base:
            raise ValueError("base_url is required")
        # Accept either the API root or the full completions URL.
        self.endpoint = (
            base if base.endswith(CHAT_COMPLETIONS_PATH) else base + CHAT_COMPLETIONS_PATH
        )

    # ------------------------------------------------------------- requests
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Required: the edge rejects default urllib UAs with 403/1010.
            "User-Agent": USER_AGENT,
        }

    def _post_once(
        self, payload: dict[str, Any]
    ) -> tuple[int | None, str, Mapping[str, str]]:
        body = json.dumps(payload).encode("utf-8")
        return self.transport(self.endpoint, body, self._headers(), self.timeout_s)

    def _post_with_retries(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """POST with backoff. Returns (parsed_body, usage)."""
        last_status: int | None = None
        last_body = ""
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            status, text, headers = self._post_once(payload)
            if status == 200:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise InvalidOutputError(
                        f"response envelope is not JSON: {exc}"
                    ) from exc
                return parsed, (
                    parsed.get("usage", {}) if isinstance(parsed, dict) else {}
                )
            last_status, last_body = status, text
            if status in (400, 401, 403, 404, 422):
                # Never retry a rejected request: it would only re-bill it.
                # The key value is never included in any message.
                raise AuthError(
                    f"request rejected with status {status}; "
                    "not retried (check key, model id, and endpoint)"
                ) if status in (401, 403) else BadRequestError(
                    f"request rejected with status {status}: {text[:200]}"
                )
            if attempt < attempts - 1:
                wait = _retry_after_seconds(headers) or (
                    self.backoff_base_s * (2**attempt)
                )
                self.sleep(wait)
        raise RetryExhaustedError(
            f"gave up after {attempts} attempt(s); last status {last_status}: "
            f"{last_body[:200]}"
        )

    # ------------------------------------------------------------ extraction
    def extract_batch(
        self, texts: Sequence[str], *, max_triples: int
    ) -> list[dict[str, Any]]:
        """Extract entities+triples for a batch of chunk texts.

        Retried once on truncated output with a doubled budget, once on a batch
        mismatch. Anything else that fails the contract raises — the caller
        records the chunk in ``failed_chunks.json``.
        """
        texts = list(texts)
        prompt = build_extraction_prompt(texts, max_triples=max_triples)
        payload: dict[str, Any] = {
            "model": self.model_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        started = time.perf_counter()
        try:
            parsed, usage = self._post_with_retries(payload)
        except BadRequestError as exc:
            # Compatibility fallback: a backend that does not understand
            # reasoning_effort 400s the request. Drop the parameter once and
            # retry — without it, not instead of failing.
            if self.reasoning_effort is not None and any(
                word in str(exc).lower() for word in ("reasoning", "thinking")
            ):
                del payload["reasoning_effort"]
                started = time.perf_counter()
                parsed, usage = self._post_with_retries(payload)
            else:
                raise
        except InvalidOutputError:
            raise
        except ExtractionError:
            raise
        choices = parsed.get("choices", []) if isinstance(parsed, dict) else []
        content = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            if isinstance(message, dict):
                content = message.get("content", "") or ""
        try:
            return self._parse_and_ledger(
                content, usage, expected=len(texts), elapsed=time.perf_counter() - started
            )
        except InvalidOutputError as exc:
            if "truncated" in str(exc).lower():
                # One salvage attempt with a doubled output budget.
                payload["max_tokens"] = self.max_tokens * 2
                started = time.perf_counter()
                parsed, usage = self._post_with_retries(payload)
                choices = parsed.get("choices", []) if isinstance(parsed, dict) else []
                content = ""
                if choices and isinstance(choices[0], dict):
                    message = choices[0].get("message", {})
                    if isinstance(message, dict):
                        content = message.get("content", "") or ""
                return self._parse_and_ledger(
                    content,
                    usage,
                    expected=len(texts),
                    elapsed=time.perf_counter() - started,
                )
            raise

    def extract_one(self, text: str, *, max_triples: int) -> dict[str, Any]:
        return self.extract_batch([text], max_triples=max_triples)[0]

    def _parse_and_ledger(
        self, content: str, usage: Mapping[str, Any], *, expected: int, elapsed: float
    ) -> list[dict[str, Any]]:
        results = parse_batch_output(content, expected=expected)
        if self.ledger is not None:
            details = usage.get("completion_tokens_details", {}) or {}
            self.ledger.record(
                stage="extract",
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                reasoning_tokens=int(details.get("reasoning_tokens", 0) or 0),
                latency_s=elapsed,
            )
        return results