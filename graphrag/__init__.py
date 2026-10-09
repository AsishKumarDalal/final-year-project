"""Track B — the GraphRAG indexer.

A standalone knowledge-graph builder. It is **not** part of the medical harness
(`src/harness/`): nothing here imports it, and it never imports it. It is
build-time and offline — it never runs inside a test, a request path, or
`make validate` (Plan.md D19).

Built to the design record in ``docs/rag_docs/``.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"