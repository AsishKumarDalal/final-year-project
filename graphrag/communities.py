"""Communities: hierarchical Leiden over the entity graph (ingestion I10).

``partition()`` returns levels of ``{id, level, members}``. The implementation
prefers ``leidenalg`` on ``igraph`` (the published GraphRAG method); when those
packages are absent it falls back to a stdlib Louvain implementation with a
fixed seed. Either way the contract is identical: deterministic community ids
for an identical input graph — re-running the indexer must not reshuffle them
(Plan M6 acceptance).

Community ids are content-derived (``L{level}_{rank:04d}``), not library row
numbers, so the two backends agree on shape if not on exact membership.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Community:
    id: str
    level: int
    members: tuple[str, ...]
    title: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "level": self.level,
                "members": list(self.members), "title": self.title}


def _edges_for_library(edges: Sequence[tuple[str, str, str, float]]) -> tuple[list[str], list[tuple[int, int]], list[float]]:
    names = sorted({name for edge in edges for name in (edge[0], edge[2])})
    index = {name: i for i, name in enumerate(names)}
    pairs = [(index[a], index[c]) for a, b, c, _ in edges]
    weights = [float(d) for _, _, _, d in edges]
    return names, pairs, weights


def _partition_leiden(names, pairs, weights, *, seed: int) -> list[list[int]]:
    import igraph
    import leidenalg
    graph = igraph.Graph(n=len(names), edges=pairs, directed=False)
    graph.es["weight"] = weights
    # RBConfigurationVertexPartition is deterministic given the seed and avoids
    # the resolution-limit collapse of plain modularity on small graphs.
    partition = leidenalg.find_partition(
        graph, leidenalg.RBConfigurationVertexPartition,
        weights="weight", seed=seed,
    )
    return [list(cluster) for cluster in partition]


def _partition_louvain_stdlib(names, pairs, weights, *, seed: int,
                              min_community: int = 2) -> list[list[int]]:
    """Seeded Louvain, one level. Fallback when leidenalg is not installed."""
    rng = random.Random(seed)
    adjacency: dict[int, dict[int, float]] = {i: {} for i in range(len(names))}
    degree: dict[int, float] = {i: 0.0 for i in range(len(names))}
    total = 0.0
    for (a, b), w in zip(pairs, weights):
        adjacency[a][b] = adjacency[a].get(b, 0.0) + w
        adjacency[b][a] = adjacency[b].get(a, 0.0) + w
        degree[a] += w
        degree[b] += w
        total += w
    if total <= 0:
        return [[i] for i in range(len(names))]
    community = list(range(len(names)))
    improved = True
    passes = 0
    while improved and passes < 10:
        improved = False
        passes += 1
        order = list(range(len(names)))
        rng.shuffle(order)
        for node in order:
            best_gain, best = 0.0, community[node]
            weights_in: dict[int, float] = {}
            for neighbour, w in adjacency[node].items():
                weights_in[community[neighbour]] = weights_in.get(community[neighbour], 0.0) + w
            for candidate, w_in in weights_in.items():
                if candidate == community[node]:
                    continue
                gain = w_in - degree[node] * sum(
                    degree[m] for m, c in enumerate(community) if c == candidate
                ) / (2.0 * total)
                if gain > best_gain:
                    best_gain, best = gain, candidate
            if best != community[node]:
                community[node] = best
                improved = True
    clusters: dict[int, list[int]] = {}
    for node, cid in enumerate(community):
        clusters.setdefault(cid, []).append(node)
    return [members for members in clusters.values() if len(members) >= min_community] or [
        list(range(len(names)))]


def partition(
    edges: Sequence[tuple[str, str, str, float]],
    *,
    levels: int = 2,
    seed: int = 42,
    min_community: int = 2,
) -> list[Community]:
    """Hierarchical partition. Level 0 is the finest; each higher level merges
    the previous level's communities as supernodes."""
    names, pairs, weights = _edges_for_library(list(edges))
    if not pairs:
        return [Community(id="L0_0000", level=0, members=tuple(names),
                          title="all_entities")]
    try:
        backend = _partition_leiden
        _partition_leiden(names, pairs, weights, seed=seed)  # import check
    except ImportError:
        backend = _partition_louvain_stdlib
    communities: list[Community] = []
    current_names = names
    current_pairs = pairs
    current_weights = weights
    for level in range(levels):
        clusters = backend(current_names, current_pairs, current_weights, seed=seed + level)
        # Deterministic order: largest first, then lexicographic.
        clusters.sort(key=lambda c: (-len(c), sorted(current_names[i] for i in c)))
        index_of = [0] * len(current_names)
        for rank, cluster in enumerate(clusters):
            community_id = f"L{level}_{rank:04d}"
            members = tuple(sorted(current_names[i] for i in cluster))
            communities.append(Community(
                id=community_id, level=level, members=members,
                title=f"community {community_id} ({len(members)} entities)"))
            for i in cluster:
                index_of[i] = rank
        if level + 1 >= levels or len(clusters) <= 1:
            break
        # Coarsen: each community becomes a supernode for the next level.
        current_names = [f"L{level}_{rank:04d}" for rank in range(len(clusters))]
        coarse: dict[tuple[int, int], float] = {}
        for (a, b), w in zip(current_pairs, current_weights):
            ca, cb = index_of[a], index_of[b]
            if ca != cb:
                key = (min(ca, cb), max(ca, cb))
                coarse[key] = coarse.get(key, 0.0) + w
        current_pairs = list(coarse.keys())
        current_weights = [coarse[k] for k in current_pairs]
        if not current_pairs:
            break
    # Drop singleton-only levels below the finest: singletons carry no theme.
    finest = [c for c in communities if c.level == 0]
    if len(finest) == len(names) and len(names) > min_community:
        communities = [c for c in communities if c.level != 0] or communities
    return communities


def community_edges(communities: Sequence[Community]) -> list[dict]:
    return [c.as_dict() for c in communities]


def flat_membership(communities: Sequence[Community]) -> dict[str, str]:
    """entity key -> finest-level community id."""
    membership: dict[str, str] = {}
    for community in sorted(communities, key=lambda c: c.level):
        for member in community.members:
            membership.setdefault(member, community.id)
    return membership