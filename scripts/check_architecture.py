#!/usr/bin/env python3
"""Arch gate: rules/ imports ONLY contracts (+stdlib). Only engine.py builds
Escalation. Tools/decision/generation never imported by rules/."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "medharness"
RULES = ROOT / "rules"
ALLOWED_TOP = {"contracts"}
STDLIB_HINT = {  # top-level stdlib modules we rely on; anything else flags
    "__future__", "dataclasses", "datetime", "enum", "hashlib", "json",
    "typing", "uuid",
}


def top_name(mod: str) -> str:
    return mod.split(".")[0]


def check() -> list[str]:
    errors: list[str] = []
    for path in sorted(RULES.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = top_name(a.name)
                    if top in ("medharness", "src"):
                        errors.append(f"{path.name}: absolute medharness import {a.name!r}")
                    elif top not in ALLOWED_TOP and top not in STDLIB_HINT:
                        errors.append(f"{path.name}: third-party import {a.name!r}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.startswith("medharness.rules"):
                    continue  # intra-rules import is fine
                if mod in ("medharness.contracts", "contracts") or mod.startswith("medharness.contracts."):
                    continue
                if mod.startswith("medharness.") or mod.startswith("src."):
                    errors.append(f"{path.name}: medharness import {mod!r} (only contracts allowed)")
                elif top_name(mod) not in STDLIB_HINT and mod:
                    errors.append(f"{path.name}: third-party import {mod!r}")
    # Only engine.py may construct Escalation(
    for path in sorted(RULES.glob("*.py")):
        src = path.read_text()
        if path.name != "engine.py" and "Escalation(" in src:
            errors.append(f"{path.name}: only engine.py may construct Escalation")
    return errors


if __name__ == "__main__":
    errs = check()
    for e in errs:
        print(f"ARCH FAIL: {e}")
    sys.exit(1 if errs else 0)
