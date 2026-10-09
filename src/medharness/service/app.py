"""FastAPI edge (S8). Thin adapter over orchestrator.assess().

Role comes ONLY from the trusted X-Actor-Role header and is enforced inside the
harness (sect 6), never trusted from the body. POST /assess, GET /health.
"""
from __future__ import annotations

import os

from medharness.contracts.models import ActorRole, HarnessRequest, HarnessResponse
from medharness.orchestrator import Orchestrator


def build_orchestrator() -> Orchestrator:
    """Wire by env: live Laya if MEDH_LIVE=1 else fixtures; Qwen if
    MEDH_ENABLE_LLM=1. External search is always a graph-walk client."""
    live = os.getenv("MEDH_LIVE", "0") == "1"
    if live:
        from medharness.decision.http_adapter import LayaHttpClient
        decision = LayaHttpClient()
    else:
        from medharness.decision.fixture_adapter import FixtureDecisionClient
        decision = FixtureDecisionClient()

    llm_client = None
    if os.getenv("MEDH_ENABLE_LLM", "0") == "1":
        from medharness.generation.qwen_client import QwenClient
        llm_client = QwenClient()

    external_search = build_external_search(live=live)
    return Orchestrator(decision_client=decision, llm_client=llm_client,
                        external_search=external_search)


def build_external_search(live: bool = False):
    """One retrieval engine for both modes (graphrag QueryEngine):
      live   → Qdrant + Neo4j + MiniLM + Laya (local + community)
      offline→ MemoryVectorStore + seed corpus, system1=None (local only)"""
    if live:
        try:
            from medharness.tools.external_search import GraphRAGExternalSearch
            return GraphRAGExternalSearch(_live_engine())
        except Exception:
            pass  # fall through to the offline engine
    from medharness.tools.external_search import build_offline_external_search
    return build_offline_external_search()


def _live_engine():
    """Build a graphrag QueryEngine against the live stores (best-effort)."""
    from graphrag.query import QueryEngine
    from graphrag.stores import QdrantStore, Neo4jStore
    return QueryEngine(vector_store=QdrantStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6333")),
                       graph_store=Neo4jStore(os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
                                              user=os.getenv("NEO4J_USER", "neo4j"),
                                              password=os.getenv("NEO4J_PASSWORD", "")))


def create_app():
    try:
        from fastapi import FastAPI, Header
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("fastapi is not installed (dev-only edge)") from exc

    app = FastAPI(title="medical-harness", version="0.1.0")
    orch = build_orchestrator()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "tracks": "A(medharness) live"}

    @app.post("/assess")
    def assess(request: HarnessRequest,
               x_actor_role: str = Header(default="patient")) -> JSONResponse:
        req = HarnessRequest(text=request.text,
                             actor_role=x_actor_role if x_actor_role in
                             ("patient", "nurse", "doctor") else "patient")
        resp = orch.assess(req)
        return JSONResponse(resp.model_dump())

    return app
