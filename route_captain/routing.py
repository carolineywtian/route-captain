"""Route-building tools. All distances are computed on the map graph, never by the LLM.

Routes are lists of node ids. Distances are in meters internally; tools that
the agents call report miles.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import networkx as nx

from .mapdata import edge_points

METERS_PER_MILE = 1609.344
DISTANCE_TOLERANCE = 0.05  # routes must be within ±5% of the requested distance
REUSE_PENALTY = 6.0        # discourages a loop from running back over its own path


@dataclass
class RouteResult:
    nodes: list[int]
    length_m: float
    shape: str  # "loop", "out_and_back", "point_to_point"
    notes: list[str] = field(default_factory=list)

    @property
    def miles(self) -> float:
        return self.length_m / METERS_PER_MILE


def _edge_min(graph: nx.MultiGraph, u: int, v: int, attr: str) -> float:
    return min(d[attr] for d in graph.get_edge_data(u, v).values())


def path_length(graph: nx.MultiGraph, nodes: list[int]) -> float:
    return sum(_edge_min(graph, u, v, "length") for u, v in zip(nodes, nodes[1:]))


def _shortest(graph, source, target, weight="cost") -> list[int]:
    return nx.shortest_path(graph, source, target, weight=weight)


def _bearing(graph, a: int, b: int) -> float:
    ya, xa = graph.nodes[a]["y"], graph.nodes[a]["x"]
    yb, xb = graph.nodes[b]["y"], graph.nodes[b]["x"]
    return math.degrees(math.atan2(xb - xa, yb - ya)) % 360


def _true_lengths_along_cost_paths(graph, source, cutoff_m):
    """Dijkstra on `cost`, returning the real length (m) and cost of each best path."""
    costs, paths = nx.single_source_dijkstra(graph, source, weight="cost", cutoff=cutoff_m * 4)
    lengths = {}
    for node, path in paths.items():
        lengths[node] = path_length(graph, path)
    return costs, lengths, paths


def _penalized_copy_weight(used_edges: set[frozenset]):
    def weight(u, v, data):
        base = min(d["cost"] for d in data.values())
        return base * REUSE_PENALTY if frozenset((u, v)) in used_edges else base
    return weight


def _within(length_m: float, target_m: float, tol: float = DISTANCE_TOLERANCE) -> bool:
    return abs(length_m - target_m) <= tol * target_m


def build_out_and_back(graph: nx.MultiGraph, start: int, target_m: float,
                       avoid_bearings: list[float] | None = None) -> RouteResult | None:
    """Run out to a turnaround point at half the distance, then return the same way."""
    half = target_m / 2
    costs, lengths, paths = _true_lengths_along_cost_paths(graph, start, half * 1.1)
    candidates = [n for n, L in lengths.items() if _within(L, half, 0.02)]
    if not candidates:
        return None

    def score(n):
        ratio = costs[n] / max(lengths[n], 1)  # 1.0 = all footpaths
        spread = 0.0
        if avoid_bearings:
            b = _bearing(graph, start, n)
            spread = min(min(abs(b - a), 360 - abs(b - a)) for a in avoid_bearings)
        return ratio - spread / 360

    turn = min(candidates, key=score)
    out = paths[turn]
    nodes = out + out[-2::-1]
    return RouteResult(nodes, path_length(graph, nodes), "out_and_back",
                       [f"Turn around at the {_compass(_bearing(graph, start, turn))} end of the route."])


def build_through(graph: nx.MultiGraph, start: int, end: int, target_m: float,
                  attempts: int = 25) -> RouteResult | None:
    """The best of `through_routes` (see there)."""
    results = through_routes(graph, start, end, target_m, attempts)
    return min(results, key=lambda r: _route_rank(graph, r, target_m)) if results else None


def through_routes(graph: nx.MultiGraph, start: int, end: int, target_m: float,
                   attempts: int = 25) -> list[RouteResult]:
    """Route from start to end (equal for a loop) of about `target_m`, via two waypoints.

    Leg 1 goes to waypoint A; legs 2-3 go A -> B -> end on a graph that penalizes
    reusing leg 1, so loops actually loop instead of retracing.
    """
    is_loop = start == end
    from_start = nx.single_source_dijkstra_path_length(graph, start, weight="length",
                                                       cutoff=target_m)
    to_end = (from_start if is_loop else
              nx.single_source_dijkstra_path_length(graph, end, weight="length", cutoff=target_m))

    leg1_share = 0.33 if is_loop else 0.4
    a_candidates = [n for n, d in from_start.items()
                    if abs(d - target_m * leg1_share) < 0.05 * target_m]
    a_candidates.sort(key=lambda n: (_bearing(graph, start, n), n))
    if not a_candidates:
        return []
    step = max(1, len(a_candidates) // attempts)

    results: list[RouteResult] = []
    for a in a_candidates[::step][:attempts]:
        leg1 = _shortest(graph, start, a)
        used = {frozenset(e) for e in zip(leg1, leg1[1:])}
        len1 = path_length(graph, leg1)
        remaining = target_m - len1
        # B should sit so that A->B + B->end is about the remaining distance.
        from_a = nx.single_source_dijkstra_path_length(graph, a, weight="length", cutoff=remaining)
        b_candidates = [n for n, d in from_a.items()
                        if n in to_end and abs(d + to_end[n] - remaining) < 0.08 * target_m
                        and n not in leg1]
        if not b_candidates:
            continue
        b = min(b_candidates, key=lambda n: abs(from_a[n] + to_end[n] - remaining))
        weight = _penalized_copy_weight(used)
        leg2 = nx.shortest_path(graph, a, b, weight=weight)
        used |= {frozenset(e) for e in zip(leg2, leg2[1:])}
        leg3 = nx.shortest_path(graph, b, end, weight=_penalized_copy_weight(used))
        nodes = leg1 + leg2[1:] + leg3[1:]
        results.append(RouteResult(nodes, path_length(graph, nodes),
                                   "loop" if is_loop else "point_to_point"))
    return results


def _route_rank(graph, route: RouteResult, target_m: float) -> float:
    """Lower is better: distance error first, then how road-heavy the route is."""
    error = abs(route.length_m - target_m) / target_m
    cost = sum(_edge_min(graph, u, v, "cost") for u, v in zip(route.nodes, route.nodes[1:]))
    road_ratio = cost / max(route.length_m, 1)
    return (0 if error <= DISTANCE_TOLERANCE else 10 + error) + 0.2 * road_ratio + 0.5 * repeat_share(route)


def repeat_share(route: RouteResult) -> float:
    edges = [frozenset(e) for e in zip(route.nodes, route.nodes[1:])]
    return 1 - len(set(edges)) / max(len(edges), 1)


def _compass(bearing: float) -> str:
    names = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
    return names[int((bearing + 22.5) // 45) % 8]


def plan_route(graph: nx.MultiGraph, start: int, target_miles: float,
               end: int | None = None, prefer: str = "loop") -> RouteResult:
    """Best route of `target_miles` from start (to end, if given). Falls back to out-and-back."""
    target_m = target_miles * METERS_PER_MILE
    end = start if end is None else end
    candidates = []
    if prefer != "out_and_back" or end != start:
        through = build_through(graph, start, end, target_m)
        if through:
            candidates.append(through)
    if end == start:
        oab = build_out_and_back(graph, start, target_m)
        if oab:
            candidates.append(oab)
    if not candidates:
        raise ValueError(f"No route of {target_miles} mi found from this start point.")
    best = min(candidates, key=lambda r: _route_rank(graph, r, target_m))
    if not _within(best.length_m, target_m):
        best.notes.append(f"Closest route found is {best.miles:.2f} mi "
                          f"(requested {target_miles} mi).")
    return best


def _overlap(a: RouteResult, b: RouteResult) -> float:
    ea = {frozenset(e) for e in zip(a.nodes, a.nodes[1:])}
    eb = {frozenset(e) for e in zip(b.nodes, b.nodes[1:])}
    return len(ea & eb) / max(1, min(len(ea), len(eb)))


def route_candidates(graph: nx.MultiGraph, start: int, target_miles: float, end: int | None = None,
                     prefer: str = "loop", k: int = 3, max_overlap: float = 0.6) -> list[RouteResult]:
    """Up to k genuinely different routes of about `target_miles`, best first.

    The Navigator agent chooses among these; routes sharing more than `max_overlap`
    of their streets with a better one are dropped so the choices are real choices.
    """
    target_m = target_miles * METERS_PER_MILE
    end = start if end is None else end
    pool: list[RouteResult] = []
    if prefer == "out_and_back" and end == start:
        bearings: list[float] = []
        for _ in range(k):
            oab = build_out_and_back(graph, start, target_m, avoid_bearings=bearings or None)
            if not oab:
                break
            pool.append(oab)
            bearings.append(_bearing(graph, start, oab.nodes[len(oab.nodes) // 2]))
        if not pool:  # turnaround would be off the map: offer loops of the same distance instead
            pool = through_routes(graph, start, end, target_m)
            for route in pool:
                route.notes.append(f"An out-and-back of {target_miles} mi would turn around beyond the map "
                                   "area, so this is a loop of the same distance.")
    else:
        pool = through_routes(graph, start, end, target_m)
        if not pool and end == start:
            oab = build_out_and_back(graph, start, target_m)
            pool = [oab] if oab else []
    pool.sort(key=lambda r: _route_rank(graph, r, target_m))
    chosen: list[RouteResult] = []
    for route in pool:
        if not _within(route.length_m, target_m) and chosen:
            continue
        if all(_overlap(route, c) <= max_overlap for c in chosen):
            chosen.append(route)
        if len(chosen) >= k:
            break
    if not chosen:
        raise ValueError(f"No route of {target_miles} mi found from this start point.")
    for route in chosen:
        if not _within(route.length_m, target_m):
            route.notes.append(f"Closest route found is {route.miles:.2f} mi (requested {target_miles} mi).")
    return chosen


def route_coords(graph: nx.MultiGraph, nodes: list[int]) -> list[tuple[float, float]]:
    """(lat, lon) points along the route, following street geometry where available."""
    coords: list[tuple[float, float]] = []
    for u, v in zip(nodes, nodes[1:]):
        data = min(graph.get_edge_data(u, v).values(), key=lambda d: d["length"])
        pts = edge_points(graph, u, v, data)
        coords.extend(pts if not coords else pts[1:])
    return coords
