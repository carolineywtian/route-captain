"""Loads the cached OpenStreetMap snapshot and prepares it for routing.

Map data © OpenStreetMap contributors, available under the ODbL.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path

import networkx as nx
import numpy as np

DATA_DIR = Path(__file__).resolve().parent.parent / "sample_data"
GRAPH_FILE = DATA_DIR / "new_haven_graph.json.gz"  # built by scripts/build_compact_graph.py

# Multiplier on edge length used as routing cost: lower = preferred for runners.
# Separate footpaths (incl. mapped sidewalks) are best; busy roads are avoided.
HIGHWAY_PENALTY = {
    "footway": 1.0, "path": 1.0, "pedestrian": 1.0, "track": 1.0,
    "cycleway": 1.0, "living_street": 1.1, "residential": 1.3,
    "unclassified": 1.4, "service": 1.4, "tertiary": 2.0,
    "tertiary_link": 2.0, "secondary": 3.0, "secondary_link": 3.0,
    "primary": 5.0, "primary_link": 5.0, "steps": 4.0,
    "trunk": 10.0, "trunk_link": 10.0,
}
DEFAULT_PENALTY = 1.5
BUSY_ROADS = {"primary", "primary_link", "secondary", "secondary_link", "trunk", "trunk_link"}


def highway_types(value) -> list[str]:
    """OSM `highway` may be a string, a list, or a stringified list after graphml load."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value)
    if text.startswith("["):
        return [t.strip(" '\"") for t in text.strip("[]").split(",") if t.strip()]
    return [text]


def edge_penalty(data: dict) -> float:
    types = highway_types(data.get("highway"))
    if not types:
        return DEFAULT_PENALTY
    return max(HIGHWAY_PENALTY.get(t, DEFAULT_PENALTY) for t in types)


@lru_cache(maxsize=1)
def load_graph() -> nx.MultiGraph:
    """Undirected walking graph (runners can go both ways) with a routing `cost` per edge.

    Each edge keeps `points`: its shape as [lat, lon] pairs from one end to the other.
    """
    if not GRAPH_FILE.exists():
        raise FileNotFoundError(f"Map file missing: {GRAPH_FILE}. Run scripts/build_compact_graph.py")
    data = json.loads(gzip.decompress(GRAPH_FILE.read_bytes()))
    graph = nx.MultiGraph()
    for node, (lat, lon, tag, *elev) in data["nodes"].items():
        attrs = {"highway": tag} if tag else {}
        if elev and elev[0] is not None:
            attrs["elev"] = elev[0]  # meters above sea level (USGS 10 m elevation data)
        graph.add_node(int(node), y=lat, x=lon, **attrs)
    for u, v, length, attrs, points in data["edges"]:
        attrs = dict(attrs, length=length, points=points)
        attrs["cost"] = length * edge_penalty(attrs)
        graph.add_edge(u, v, **attrs)
    return graph


def edge_points(graph: nx.MultiGraph, u: int, v: int, data: dict) -> list[tuple[float, float]]:
    """The edge's shape as (lat, lon) points running from u to v."""
    pts = data.get("points")
    if not pts:
        return [(graph.nodes[u]["y"], graph.nodes[u]["x"]), (graph.nodes[v]["y"], graph.nodes[v]["x"])]
    uy, ux = graph.nodes[u]["y"], graph.nodes[u]["x"]
    first, last = pts[0], pts[-1]
    if (last[0] - uy) ** 2 + (last[1] - ux) ** 2 < (first[0] - uy) ** 2 + (first[1] - ux) ** 2:
        pts = pts[::-1]
    return [tuple(p) for p in pts]


_NODE_INDEX: dict[int, tuple] = {}


def _node_index(graph: nx.MultiGraph):
    key = id(graph)
    if key not in _NODE_INDEX:
        ids = np.array(list(graph.nodes))
        _NODE_INDEX[key] = (ids, np.array([graph.nodes[n]["y"] for n in ids]),
                            np.array([graph.nodes[n]["x"] for n in ids]), graph)
    return _NODE_INDEX[key][:3]


def nearest_node(graph: nx.MultiGraph, lat: float, lon: float) -> int:
    ids, lats, lons = _node_index(graph)
    meters = haversine_m(lat, lon, lats, lons)
    return int(ids[int(np.argmin(meters))])


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in meters; works on scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6_371_000 * np.arcsin(np.sqrt(a))
