"""Elevation profile and an even-effort pace for every 0.1 mile (code, not the LLM).

Elevation: node heights stored in the map file (Open-Meteo / Copernicus DEM, ~90 m),
interpolated along each street and lightly smoothed, since a 90 m grid is noisy over
short distances.

Pacing: running uphill costs more energy per meter and downhill a little less. Minetti
et al. (2002) measured that energy cost against grade; the target pace here scales the
runner's flat pace by the square root of that cost ratio, which dampens the lab curve to
match the grade-adjusted pace rules runners commonly use (about +15% at a 5% climb). The
downhill speed-up is capped at 10%: steep descents are where people get hurt.
"""
from __future__ import annotations

import math
import time

import networkx as nx
import requests

from .mapdata import edge_points, haversine_m
from .routing import METERS_PER_MILE

FEET_PER_METER = 3.28084
SAMPLE_MI = 0.05
SEGMENT_MI = 0.1
MAX_DOWNHILL_SPEEDUP = 0.10


ELEVATION_API = "https://api.opentopodata.org/v1/ned10m"  # USGS 10 m, free, 100 points per request, 1/second
_ELEV_CACHE: dict[tuple[float, float], float] = {}
_last_call = [0.0]


def fetch_elevations(points: list[tuple[float, float]]) -> list[float] | None:
    """Ground elevation (m) for points outside the built-in map; None if the service is unavailable."""
    keys = [(round(p[0], 5), round(p[1], 5)) for p in points]
    todo = [k for k in dict.fromkeys(keys) if k not in _ELEV_CACHE]
    try:
        for i in range(0, len(todo), 100):
            chunk = todo[i:i + 100]
            wait = 1.05 - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
            resp = requests.post(ELEVATION_API, timeout=20,
                                 data={"locations": "|".join(f"{a},{b}" for a, b in chunk)})
            resp.raise_for_status()
            for k, hit in zip(chunk, resp.json()["results"]):
                _ELEV_CACHE[k] = float(hit["elevation"]) if hit.get("elevation") is not None else math.nan
    except (requests.RequestException, ValueError, KeyError):
        return None
    values = [_ELEV_CACHE.get(k, math.nan) for k in keys]
    good = [v for v in values if not math.isnan(v)]
    return [v if not math.isnan(v) else (good[0] if good else 0.0) for v in values] if good else None


def _minetti(i: float) -> float:
    """Energy cost of running (J/kg/m) at grade i (rise/run), Minetti et al. 2002."""
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def grade_factor(grade: float) -> float:
    """Pace multiplier for the same effort on a grade (0.05 = 5% uphill)."""
    g = max(-0.20, min(0.30, grade))
    factor = math.sqrt(max(_minetti(g), 0.5) / _minetti(0.0))
    return max(factor, 1 - MAX_DOWNHILL_SPEEDUP)


def fmt_pace(minutes_per_mile: float) -> str:
    m, s = divmod(round(minutes_per_mile * 60), 60)
    return f"{m}:{s:02d}"


def _route_points(graph: nx.MultiGraph, nodes: list[int]):
    """(miles along route, elevation m, lat, lon) for every shape point on the route."""
    out, dist = [], 0.0
    for u, v in zip(nodes, nodes[1:]):
        data = min(graph.get_edge_data(u, v).values(), key=lambda d: d["length"])
        pts = edge_points(graph, u, v, data)
        eu = graph.nodes[u].get("elev")
        ev = graph.nodes[v].get("elev")
        eu = ev if eu is None else eu
        ev = eu if ev is None else ev
        legs = [haversine_m(*a, *b) for a, b in zip(pts, pts[1:])]
        total = sum(legs) or 1.0
        along = 0.0
        if not out:
            out.append((0.0, eu or 0.0, *pts[0]))
        for (lat, lon), leg in zip(pts[1:], legs):
            along += leg
            elev = (eu or 0.0) + ((ev or 0.0) - (eu or 0.0)) * (along / total)
            out.append(((dist + along) / METERS_PER_MILE, elev, lat, lon))
        dist += total
    return out


def _resample(points, step_mi: float):
    """Evenly spaced samples along the route, linearly interpolated."""
    if not points:
        return []
    total = points[-1][0]
    n = max(2, int(total / step_mi) + 1)
    marks = [min(total, k * step_mi) for k in range(n)] + ([total] if total % step_mi else [])
    out, j = [], 0
    for m in marks:
        while j < len(points) - 2 and points[j + 1][0] < m:
            j += 1
        a, b = points[j], points[min(j + 1, len(points) - 1)]
        f = 0.0 if b[0] == a[0] else (m - a[0]) / (b[0] - a[0])
        out.append([m] + [a[k] + (b[k] - a[k]) * f for k in (1, 2, 3)])
    return out


def _smooth(values, window: int = 3):
    half = window // 2
    return [sum(values[max(0, i - half):i + half + 1]) / len(values[max(0, i - half):i + half + 1])
            for i in range(len(values))]


def _classify(grade: float) -> str:
    if grade >= 0.04:
        return "climb"
    if grade >= 0.02:
        return "gentle climb"
    if grade <= -0.04:
        return "descent"
    if grade <= -0.02:
        return "gentle descent"
    return "flat"


def profile(graph: nx.MultiGraph, nodes: list[int], flat_pace: float, heat_slowdown: float = 0.0) -> dict:
    """Elevation samples, 0.1-mile pace segments, merged sections, the biggest hills and totals."""
    samples = _resample(_route_points(graph, nodes), SAMPLE_MI)
    if len(samples) < 2:
        return {}
    available = True
    if graph.nodes[nodes[0]].get("elev") is None:  # downloaded area: fetch heights for this route only
        fetched = fetch_elevations([(s[2], s[3]) for s in samples])
        if fetched is None:
            available = False
            fetched = [0.0] * len(samples)
        for smp, elev in zip(samples, fetched):
            smp[1] = elev
    elevs = _smooth([s[1] for s in samples])
    for s, e in zip(samples, elevs):
        s[1] = e
    per_seg = max(1, round(SEGMENT_MI / SAMPLE_MI))

    segments = []
    for i in range(0, len(samples) - 1, per_seg):
        a, b = samples[i], samples[min(i + per_seg, len(samples) - 1)]
        miles = b[0] - a[0]
        if miles <= 0:
            continue
        grade = (b[1] - a[1]) / (miles * METERS_PER_MILE)
        pace = flat_pace * grade_factor(grade) * (1 + heat_slowdown)
        segments.append({
            "start": round(a[0], 2), "end": round(b[0], 2), "grade": round(grade, 4),
            "change_ft": round((b[1] - a[1]) * FEET_PER_METER), "pace": pace, "pace_str": fmt_pace(pace),
            "kind": _classify(grade),
            "coords": [[s[2], s[3]] for s in samples[i:min(i + per_seg, len(samples) - 1) + 1]],
        })

    sections = []
    for seg in segments:
        if sections and sections[-1]["kind"] == seg["kind"]:
            sec = sections[-1]
            sec["end"] = seg["end"]
            sec["change_ft"] += seg["change_ft"]
            sec["minutes"] += (seg["end"] - seg["start"]) * seg["pace"]
        else:
            sections.append({"kind": seg["kind"], "start": seg["start"], "end": seg["end"],
                             "change_ft": seg["change_ft"],
                             "minutes": (seg["end"] - seg["start"]) * seg["pace"]})
    for sec in sections:
        miles = max(sec["end"] - sec["start"], 1e-6)
        sec["pace_str"] = fmt_pace(sec["minutes"] / miles)
        sec["grade"] = round(sec["change_ft"] / FEET_PER_METER / (miles * METERS_PER_MILE), 4)

    hills = sorted((s for s in sections if s["kind"] in ("climb", "gentle climb") and s["change_ft"] >= 15),
                   key=lambda s: -s["change_ft"])[:3]
    feet = [s[1] * FEET_PER_METER for s in samples]
    gain = sum(max(0.0, b - a) for a, b in zip(feet, feet[1:]))
    loss = sum(max(0.0, a - b) for a, b in zip(feet, feet[1:]))
    minutes = sum((s["end"] - s["start"]) * s["pace"] for s in segments)
    return {
        "samples": [{"mile": round(s[0], 3), "elev_ft": round(s[1] * FEET_PER_METER, 1)} for s in samples],
        "segments": segments,
        "sections": sections,
        "hills": [{"start": h["start"], "end": h["end"], "gain_ft": h["change_ft"],
                   "grade_pct": round(h["grade"] * 100, 1), "pace_str": h["pace_str"]} for h in hills],
        "gain_ft": round(gain), "loss_ft": round(loss),
        "min_ft": round(min(feet)), "max_ft": round(max(feet)),
        "minutes": minutes, "flat_pace_str": fmt_pace(flat_pace * (1 + heat_slowdown)),
        "heat_slowdown": heat_slowdown, "elevation_available": available,
    }
