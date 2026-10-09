"""Laya HTTP adapter (S2). POST /v1/systemone, one batched call.

Env: LAYA_BASE_URL (default http://127.0.0.1:8000), LAYA_MODEL_ID (laya).
Live only — tests use fixture_adapter (no network in tests, ever).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping

from medharness.contracts.models import LayaVerdict
from medharness.decision.client import full_question_set, parse_verdict


class LayaError(RuntimeError):
    pass


def _post(url: str, body: bytes, timeout: int) -> tuple[int | None, str]:
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except Exception:
            return exc.code, ""
    except Exception:
        return None, ""


@dataclass
class LayaHttpClient:
    base_url: str = ""
    model_id: str = ""
    timeout_s: int = 120
    transport: Callable = _post

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or os.getenv("LAYA_BASE_URL",
                                                     "http://127.0.0.1:8000")).rstrip("/")
        self.model_id = self.model_id or os.getenv("LAYA_MODEL_ID", "laya")
        self.endpoint = self.base_url + "/v1/systemone"

    def raw_decide(self, state: str, questions: Mapping) -> dict:
        body = json.dumps({"model": self.model_id, "state": state,
                           "questions": dict(questions)}).encode()
        status, text = self.transport(self.endpoint, body, self.timeout_s)
        if status != 200:
            raise LayaError(f"system-one returned {status}: {text[:200]}")
        try:
            return json.loads(text).get("answers", {})
        except json.JSONDecodeError as exc:
            raise LayaError(f"system-one response not JSON: {exc}") from exc

    def decide(self, state: str) -> LayaVerdict:
        return parse_verdict(self.raw_decide(state, full_question_set()))

    def available(self, timeout_s: int = 3) -> bool:
        """Reachability check with a SHORT timeout (the full decide timeout is
        120s, which would block the dev runner for two minutes on every start)."""
        saved = self.timeout_s
        self.timeout_s = timeout_s
        try:
            self.raw_decide("ping", {"ok": {"type": "noul", "instructions": "Is this a ping?"}})
            return True
        except LayaError:
            return False
        finally:
            self.timeout_s = saved
