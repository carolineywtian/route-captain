"""Turn-by-turn cues computed from the route geometry (code, not the LLM)."""
from __future__ import annotations

import math

import networkx as nx
import numpy as np

from .mapdata import edge_points, haversine_m, highway_types
from .routing import METERS_PER_MILE

UNNAMED = {"footway": "footpath", "path": "path", "steps": "steps", "track": "trail",
           "cycleway": "bike path", "pedestrian": "pedestrian walkway", "service": "service road"}


def _edge(graph, u, v) -> dict:
    return min(graph.get_edge_data(u, v).values(), key=lambda d: d["length"])


SIDEWALK_MATCH_M = 30  # a footpath this close to a named street is treated as its sidewalk


def _clean_name(name) -> str | None:
    if isinstance(name, list):
        name = name[0]
    if not name or str(name) == "nan":
        return None
    return str(name).strip("[]'\"").split("',")[0]


_STREET_POINTS: dict[int, tuple] = {}


def _named_street_points(graph):
    """Sample points along every named street, for matching sidewalks to streets."""
    if id(graph) in _STREET_POINTS:
        return _STREET_POINTS[id(graph)][:3]
    lats, lons, names = [], [], []
    for u, v, d in graph.edges(data=True):
        name = _clean_name(d.get("name"))
        if not name or "footway" in highway_types(d.get("highway")):
            continue
        for lat, lon in edge_points(graph, u, v, d):
            lats.append(lat); lons.append(lon); names.append(name)
    _STREET_POINTS[id(graph)] = (np.array(lats), np.array(lons), names, graph)
    return _STREET_POINTS[id(graph)][:3]


def _way_name(graph, u, v, data: dict) -> str:
    name = _clean_name(data.get("name"))
    if name:
        return name
    types = highway_types(data.get("highway"))
    if "footway" in types:
        lat = (graph.nodes[u]["y"] + graph.nodes[v]["y"]) / 2
        lon = (graph.nodes[u]["x"] + graph.nodes[v]["x"]) / 2
        lats, lons, names = _named_street_points(graph)
        dist = haversine_m(lat, lon, lats, lons)
        i = int(np.argmin(dist))
        if dist[i] <= SIDEWALK_MATCH_M:
            return names[i]
    return UNNAMED.get(types[0], "unnamed street") if types else "unnamed street"


def _bearing(graph, a, b) -> float:
    ya, xa = graph.nodes[a]["y"], graph.nodes[a]["x"]
    yb, xb = graph.nodes[b]["y"], graph.nodes[b]["x"]
    return math.degrees(math.atan2((xb - xa) * math.cos(math.radians(ya)), yb - ya)) % 360


def _turn(before: float, after: float) -> str:
    delta = (after - before + 540) % 360 - 180
    if abs(delta) < 30:
        return "continue"
    if abs(delta) > 150:
        return "turn around"
    side = "right" if delta > 0 else "left"
    return f"{'bear' if abs(delta) < 60 else 'turn'} {side}"


def route_cues(graph: nx.MultiGraph, nodes: list[int], min_leg_m: float = 120) -> list[dict]:
    """Merge consecutive edges on the same way into legs; describe each change of way."""
    legs: list[dict] = []
    for u, v in zip(nodes, nodes[1:]):
        data = _edge(graph, u, v)
        name = _way_name(graph, u, v, data)
        if legs and legs[-1]["name"] == name:
            legs[-1]["length"] += data["length"]
            legs[-1]["end"] = (u, v)
        else:
            legs.append({"name": name, "length": data["length"], "start": (u, v), "end": (u, v)})

    # fold short connector bits (crosswalks, sidewalk stubs) into the previous leg
    merged: list[dict] = []
    for leg in legs:
        if merged and leg["length"] < min_leg_m:
            merged[-1]["length"] += leg["length"]
            merged[-1]["end"] = leg["end"]
        elif merged and merged[-1]["name"] == leg["name"]:
            merged[-1]["length"] += leg["length"]
            merged[-1]["end"] = leg["end"]
        else:
            merged.append(dict(leg))

    cues, at = [], 0.0
    for i, leg in enumerate(merged):
        if i == 0:
            action = "Start on"
        else:
            prev = merged[i - 1]["end"]
            action = _turn(_bearing(graph, *prev), _bearing(graph, *leg["start"])).capitalize() + " onto"
        cues.append({"mile": round(at / METERS_PER_MILE, 1),
                     "text": f"{action} {leg['name']} for {leg['length'] / METERS_PER_MILE:.1f} mi"})
        at += leg["length"]
    return cues
