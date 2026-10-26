"""Safety scoring for a route, computed from OpenStreetMap tags (never by the LLM).

What the snapshot can tell us reliably: the type of street/path for every
segment and tagged crossings/signals. Lighting is rarely tagged in New Haven,
so it is reported as unknown and daylight is judged from sunset time instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import networkx as nx
from astral import LocationInfo
from astral.sun import sun

from .mapdata import BUSY_ROADS, edge_points, highway_types
from .routing import METERS_PER_MILE, RouteResult
from .timefmt import fmt

NEW_HAVEN = LocationInfo("New Haven", "USA", "America/New_York", 41.3083, -72.9279)
PROTECTED = {"footway", "path", "pedestrian", "track", "cycleway", "steps"}
QUIET = {"residential", "living_street", "service", "unclassified"}
MODERATE = {"tertiary", "tertiary_link"}


@dataclass
class SafetyReport:
    score: int                      # 0-100, higher is safer
    miles_by_type: dict[str, float]
    busy_crossings: dict[str, int]  # signalized / marked / unmarked
    lit_share_known: float          # share of route with a `lit` tag at all
    daylight: str                   # "ok", "near_sunset", "dark", "unknown"
    sunset: str | None
    flags: list[str] = field(default_factory=list)
    passes: bool = True
    crossing_points: list[dict] = field(default_factory=list)   # where the route crosses busy roads
    busy_stretches: list[list] = field(default_factory=list)   # [[lat, lon], ...] stretches on busy roads


def _category(types: list[str]) -> str:
    if any(t in BUSY_ROADS for t in types):
        return "busy_road"
    if any(t in MODERATE for t in types):
        return "moderate_road"
    if any(t in QUIET for t in types):
        return "quiet_road"
    if any(t in PROTECTED for t in types):
        return "path_or_sidewalk"
    return "quiet_road"


def _route_edge(graph, u, v) -> dict:
    return min(graph.get_edge_data(u, v).values(), key=lambda d: d["length"])


def _busy_crossings(graph: nx.MultiGraph, nodes: list[int], points: list | None = None) -> dict[str, int]:
    """Count points where the route crosses (not runs along) a busy road; optionally record where."""
    counts = {"signalized": 0, "marked": 0, "unmarked": 0}
    seen = set()
    for i in range(1, len(nodes) - 1):
        node = nodes[i]
        if node in seen:
            continue
        own_busy = any(
            _category(highway_types(_route_edge(graph, a, b).get("highway"))) == "busy_road"
            for a, b in ((nodes[i - 1], node), (node, nodes[i + 1])))
        if own_busy:  # running along the busy road, not crossing it
            continue
        touches_busy = any(
            _category(highway_types(d.get("highway"))) == "busy_road"
            for _, _, d in graph.edges(node, data=True))
        if not touches_busy:
            continue
        seen.add(node)
        tag = str(graph.nodes[node].get("highway"))
        kind = "signalized" if tag == "traffic_signals" else "marked" if tag == "crossing" else "unmarked"
        counts[kind] += 1
        if points is not None:
            points.append({"lat": graph.nodes[node]["y"], "lon": graph.nodes[node]["x"], "kind": kind})
    return counts


def _daylight(start: datetime | None, duration_min: float,
              where: tuple[float, float, str] | None = None) -> tuple[str, str | None]:
    if start is None:
        return "unknown", None
    lat, lon, tzname = where or (NEW_HAVEN.latitude, NEW_HAVEN.longitude, NEW_HAVEN.timezone)
    tz = ZoneInfo(tzname)
    start = start if start.tzinfo else start.replace(tzinfo=tz)
    s = sun(LocationInfo("run", "", tzname, lat, lon).observer, date=start.date(), tzinfo=tz)
    finish = start + timedelta(minutes=duration_min)
    sunset = fmt(s["sunset"], "%-I:%M %p")
    if start < s["dawn"] or finish > s["dusk"]:
        return "dark", sunset
    if start < s["sunrise"] or finish > s["sunset"] - timedelta(minutes=15):
        return "near_sunset", sunset
    return "ok", sunset


def score_route(graph: nx.MultiGraph, route: RouteResult,
                start_time: datetime | None = None,
                slowest_pace_min_per_mile: float = 10.0, tz: str | None = None) -> SafetyReport:
    meters = {"path_or_sidewalk": 0.0, "quiet_road": 0.0, "moderate_road": 0.0, "busy_road": 0.0}
    lit_known = 0.0
    stretches: list[list] = []
    previous_busy = False
    for u, v in zip(route.nodes, route.nodes[1:]):
        d = _route_edge(graph, u, v)
        category = _category(highway_types(d.get("highway")))
        meters[category] += d["length"]
        if d.get("lit") is not None:
            lit_known += d["length"]
        if category == "busy_road":  # remember where, so the map can show it
            pts = [list(p) for p in edge_points(graph, u, v, d)]
            if previous_busy and stretches:
                stretches[-1].extend(pts[1:])
            else:
                stretches.append(pts)
        previous_busy = category == "busy_road"
    total = max(sum(meters.values()), 1.0)
    share = {k: v / total for k, v in meters.items()}
    crossing_points: list[dict] = []
    crossings = _busy_crossings(graph, route.nodes, crossing_points)
    first = graph.nodes[route.nodes[0]]
    where = (first["y"], first["x"], tz) if tz else None
    daylight, sunset = _daylight(start_time, route.miles * slowest_pace_min_per_mile, where)

    score = 100.0
    score -= 60 * share["busy_road"] + 20 * share["moderate_road"] + 5 * share["quiet_road"]
    score -= 6 * crossings["unmarked"] + 1.5 * crossings["marked"] + 0.5 * crossings["signalized"]
    score -= {"dark": 25, "near_sunset": 10}.get(daylight, 0)
    score = int(max(0, min(100, round(score))))

    flags = []
    if share["busy_road"] > 0.10:
        flags.append(f"{share['busy_road']:.0%} of the route is on busy roads "
                     f"(primary/secondary) without a separately mapped sidewalk.")
    if crossings["unmarked"]:
        flags.append(f"{crossings['unmarked']} unmarked crossing(s) of a busy road.")
    if daylight == "dark":
        flags.append(f"The slowest group would be running in the dark (sunset {sunset}).")
    elif daylight == "near_sunset":
        flags.append(f"The slowest group finishes close to sunset ({sunset}); bring lights.")
    if lit_known / total < 0.5:
        flags.append("Street lighting is mostly unknown in the map data.")

    passes = score >= 60 and share["busy_road"] <= 0.25 and daylight != "dark"
    return SafetyReport(
        score=score,
        miles_by_type={k: round(v / METERS_PER_MILE, 2) for k, v in meters.items()},
        busy_crossings=crossings,
        lit_share_known=round(lit_known / total, 2),
        daylight=daylight,
        sunset=sunset,
        flags=flags,
        passes=passes,
        crossing_points=crossing_points,
        busy_stretches=stretches,
    )
