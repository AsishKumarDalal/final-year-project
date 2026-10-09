"""Lab reference lookup (S4). Pure file lookup. No LLM, no guessing.

sect 10.5: if a fact is not in the table, return not_found — never invent.
Returns facts + source_type; never prose conclusions.
"""
from __future__ import annotations

import json
from pathlib import Path

from medharness.contracts.models import ToolFact

TABLES = Path(__file__).resolve().parents[3] / "data" / "tables"


def _load(name: str) -> dict:
    return json.loads((TABLES / name).read_text())


def get_lab_ref(test_name: str) -> ToolFact:
    table = _load("lab_ranges.json")
    key = test_name.strip().lower().replace(" ", "_")
    entry = table["ranges"].get(key)
    if entry is None:
        return ToolFact(facts=[], source_ids=[], source_type="lab_table",
                        not_found=True)
    unit = entry.get("unit", "")
    facts = [f"{test_name}: reference range {entry.get('ref_low')}-{entry.get('ref_high')} {unit}".strip()]
    for bound in ("critical_low", "critical_high"):
        if bound in entry:
            label = "critical LOW" if bound == "critical_low" else "critical HIGH"
            facts.append(f"{test_name}: {label} threshold {entry[bound]} {unit}".strip())
    return ToolFact(facts=facts, source_ids=[f"lab_ranges.json:{key}"],
                    source_type="lab_table")


def get_drug_interactions(drugs: list[str]) -> ToolFact:
    table = _load("drug_interactions.json")
    pairs = table["pairs"]
    norm = sorted({d.strip().lower() for d in drugs})
    facts: list[str] = []
    sources: list[str] = []
    hit = False
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            key = f"{norm[i]}+{norm[j]}"
            if key in pairs:
                hit = True
                rec = pairs[key]
                facts.append(f"{norm[i]} + {norm[j]}: {rec['severity']} interaction — {rec['note']}")
                sources.append(f"drug_interactions.json:{key}")
    if not hit:
        return ToolFact(facts=[], source_ids=[], source_type="drug_table",
                        not_found=True)
    return ToolFact(facts=facts, source_ids=sources, source_type="drug_table")
