"""Tool registry (S4). The ONLY door from a call to execution — and the point
where role-based permission (PROMPT.md sect 6) is enforced, not the UI.

Role matrix (asserted via trusted X-Actor-Role header, enforced here):
  patient: labs (read-only reference) allowed; drugs allowed; kb allowed.
  nurse:   everything patient + drug detail.
  doctor:  everything + full lookup.
Unknown role or unknown tool -> denied (fail closed). Permission denial is
recorded so it can be tested and logged.
"""
from __future__ import annotations

from typing import Any, Callable

from medharness.contracts.models import ToolFact

Handler = Callable[..., ToolFact]

# tool -> set of roles allowed to call it (sect 6 matrix, conservative default)
PERMISSIONS: dict[str, set[str]] = {
    "get_lab_ref": {"patient", "nurse", "doctor"},
    "get_drug_interactions": {"patient", "nurse", "doctor"},
    "kb_search": {"patient", "nurse", "doctor"},
    "search_external_docs": {"patient", "nurse", "doctor"},
}
VALID_ROLES = {"patient", "nurse", "doctor"}


class PermissionDenied(Exception):
    pass


def check_permission(tool: str, role: str) -> None:
    if role not in VALID_ROLES:
        raise PermissionDenied(f"unknown role {role!r}")
    allowed = PERMISSIONS.get(tool)
    if allowed is None:
        raise PermissionDenied(f"unknown tool {tool!r}")
    if role not in allowed:
        raise PermissionDenied(f"role {role!r} may not call {tool!r}")


def dispatch(tool: str, role: str, args: dict[str, Any],
             external_search: object | None = None) -> ToolFact:
    """Single chokepoint: permission check first, then handler. Fail closed.

    external_search (optional) powers search_external_docs; when absent the tool
    degrades to not_found rather than guessing.
    """
    check_permission(tool, role)  # raises PermissionDenied before any execution
    if tool == "get_lab_ref":
        from medharness.tools.lookup import get_lab_ref
        return get_lab_ref(str(args.get("test_name", "")))
    if tool == "get_drug_interactions":
        from medharness.tools.lookup import get_drug_interactions
        return get_drug_interactions(list(args.get("drugs", [])))
    if tool == "kb_search":
        from medharness.tools.kb import kb_search
        return kb_search(str(args.get("query", "")), top_k=int(args.get("top_k", 5)))
    if tool == "search_external_docs":
        if external_search is None:
            return ToolFact(facts=[], source_ids=[], source_type="external_graphrag",
                            search_mode=str(args.get("mode", "local")), not_found=True)
        result = external_search.search(str(args.get("query", "")),
                                        mode=(args.get("mode") or None))
        return ToolFact(facts=list(result.facts), source_ids=list(result.source_ids),
                        source_type="external_graphrag", search_mode=result.search_mode,
                        not_found=result.not_found)
    raise PermissionDenied(f"unknown tool {tool!r}")
