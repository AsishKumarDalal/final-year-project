"""Seed corpus (S1). 4 synthetic docs -> chunks -> MiniLM embed -> both stores.

Synthetic only (sect 4.5). Chunk ids are stable provenance: Doc::N.
"""
from __future__ import annotations

import hashlib
import re

DOCS = {
    "chest_pain": (
        "Chest pain with pressure and tightness may signal a cardiac emergency. "
        "Severe chest pain spreading to the left arm needs immediate emergency care. "
        "Do not wait for further assessment when pressure and sweating occur together."
    ),
    "stroke": (
        "Facial droop with weakness on one side of the body may signal a stroke. "
        "Difficulty speaking and numbness need immediate emergency care. "
        "Note the time symptoms started and seek emergency help at once."
    ),
    "breathing": (
        "Severe breathlessness with inability to speak in full sentences signals "
        "respiratory distress. Wheezing and low oxygen need urgent assessment. "
        "Sit upright and seek urgent care when breathing is laboured."
    ),
    "bleeding": (
        "Uncontrolled bleeding and vomiting blood are major haemorrhage signs. "
        "Black tarry stools with dizziness need urgent care. "
        "Apply pressure to external bleeding and call emergency services."
    ),
}

ENTITIES = {
    "chest_pain": [("chest pain", "symptom"), ("pressure", "symptom"),
                   ("cardiac emergency", "condition"), ("left arm", "body_system")],
    "stroke": [("facial droop", "symptom"), ("weakness", "symptom"),
               ("stroke", "condition"), ("difficulty speaking", "symptom")],
    "breathing": [("breathlessness", "symptom"), ("respiratory distress", "condition"),
                  ("wheezing", "symptom")],
    "bleeding": [("bleeding", "symptom"), ("major haemorrhage", "condition"),
                 ("vomiting blood", "symptom")],
}

EDGES = [
    ("chest pain", "cardiac emergency", "MAY_SIGNAL", "chest_pain"),
    ("facial droop", "stroke", "MAY_SIGNAL", "stroke"),
    ("breathlessness", "respiratory distress", "MAY_SIGNAL", "breathing"),
    ("bleeding", "major haemorrhage", "MAY_SIGNAL", "bleeding"),
]


def stable_id(collection: str, key: str) -> str:
    d = hashlib.sha256(f"{collection}:{key}".encode()).hexdigest()
    return f"{d[:8]}-{d[8:12]}-{d[12:16]}-{d[16:20]}-{d[20:32]}"


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def seed(vector_store, graph_store, embed) -> dict[str, int]:
    """Embed every chunk + entity, load both stores. Returns counts."""
    vector_store.ensure_collections("text_units", "entities")
    chunk_ids, chunk_vecs, chunk_pays = [], [], []
    for doc_id, text in DOCS.items():
        cid = f"{doc_id}::0"
        chunk_ids.append(stable_id("text_units", cid))
        chunk_vecs.append(embed([text])[0])
        chunk_pays.append({"text_unit_id": cid, "doc_id": doc_id, "text": text})
    vector_store.upsert("text_units", chunk_ids, chunk_vecs, chunk_pays)
    ent_ids, ent_vecs, ent_pays = [], [], []
    for doc_id, ents in ENTITIES.items():
        for name, etype in ents:
            key = name.lower().replace(" ", "_")
            graph_store.add_entity(key, type=etype, display=name, mentions=1)
            ent_ids.append(stable_id("entities", key))
            ent_vecs.append(embed([name])[0])
            ent_pays.append({"entity_key": key, "display": name, "type": etype})
    vector_store.upsert("entities", ent_ids, ent_vecs, ent_pays)
    for head, tail, rel, doc in EDGES:
        graph_store.add_edge(head.lower().replace(" ", "_"), tail.lower().replace(" ", "_"),
                             rel, f"{doc}::0")
    return {"chunks": len(chunk_ids), "entities": len(ent_ids),
            "nodes": graph_store.node_count(), "edges": graph_store.edge_count()}


def hashed_embed(dim: int = 32):
    """Deterministic, dependency-free embedder for offline dev. Real MiniLM
    drops into the same embed(texts)->vectors seam (memory.md S1 note)."""
    import hashlib
    import math

    def embed(texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * dim
            for tok in t.lower().split():
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out

    return embed


def build_dev_stores(dim: int = 32):
    """One seeded (vector_store, graph_store) pair shared by internal + external search."""
    from medharness.stores.vector import VectorStore
    from medharness.stores.graph import GraphStore
    v, g = VectorStore(dim=dim), GraphStore()
    seed(v, g, hashed_embed(dim))
    return v, g
