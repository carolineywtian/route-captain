"""Places tools: load the cached places snapshot, check opening hours, find stops."""
from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache

import networkx as nx

from .guard import screen_place
from .mapdata import DATA_DIR, haversine_m, nearest_node
from .routing import METERS_PER_MILE

ATTACK_PLACES = DATA_DIR / "attack_places.json"

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
PLACES_OSM = DATA_DIR / "new_haven_places.json.gz"  # built by scripts/download_places.py


@lru_cache(maxsize=2)
def load_places(include_attacks: bool = False) -> tuple[list[dict], list[dict]]:
    """New Haven's (screened places, quarantined places). Attack data is the planted test set."""
    import gzip

    from .area import parse_places
    raw = parse_places(json.loads(gzip.decompress(PLACES_OSM.read_bytes())))
    if include_attacks and ATTACK_PLACES.exists():
        raw.extend(json.loads(ATTACK_PLACES.read_text(encoding="utf-8")))
    safe, quarantined = [], []
    for place in raw:
        cleaned, reason = screen_place(place)
        if cleaned:
            safe.append(cleaned)
        else:
            quarantined.append({**{k: place.get(k) for k in ("id", "name", "category")}, "reason": reason})
    return safe, quarantined


# ---------------------------------------------------------------- opening hours

def _day_set(spec: str) -> set[int]:
    days: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            if a in DAYS and b in DAYS:
                i, j = DAYS.index(a), DAYS.index(b)
                days |= set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
        elif part in DAYS:
            days.add(DAYS.index(part))
    return days


def _minutes(hhmm: str) -> int:
    """'07:30' or '7' -> minutes after midnight."""
    h, _, m = hhmm.strip().partition(":")
    return int(h) * 60 + int(m or 0)


def is_open_at(hours: str | None, when: datetime) -> bool | None:
    """True/False from an OSM opening_hours string, or None if unknown or too complex.

    Also counts the tail of the previous day's overnight hours (Fr 18:00-02:00 is open
    at 01:00 on Saturday)."""
    try:
        today = _is_open_on_day(hours, when.weekday(), when.hour * 60 + when.minute)
        if today is not True and hours:
            spill = _is_open_on_day(hours, (when.weekday() - 1) % 7, when.hour * 60 + when.minute + 24 * 60)
            if spill:
                return True
        return today
    except (ValueError, IndexError):
        return None


def _is_open_on_day(hours: str | None, day: int, minute: int) -> bool | None:
    if not hours:
        return None
    hours = hours.strip()
    if hours == "24/7":
        return True
    result = None
    # Rules are separated by ";" or by ", " before a new day group ("Mo-Fr 7:00-18:00, Sa 8:00-16:00").
    for rule in re.split(r";|,\s+(?=[A-Z][a-z])", hours):
        rule = rule.strip()
        if not rule or rule.startswith("PH"):
            continue
        m = re.fullmatch(r"((?:[A-Z][a-z](?:-[A-Z][a-z])?,?\s*)+)?\s*(off|closed|[\d:,\-\s+]+)", rule)
        if not m:
            return None
        day_spec, times = m.group(1), m.group(2).strip()
        days = _day_set(day_spec.replace(" ", "")) if day_spec else set(range(7))
        if day not in days:
            continue
        if times in ("off", "closed"):
            result = False  # later rules override earlier ones, as in OSM
            continue
        is_open = False
        for span in times.split(","):
            span = span.strip().rstrip("+")
            if "-" not in span:
                return None
            a, b = span.split("-")
            start, end = _minutes(a), _minutes(b)
            if end <= start:
                end += 24 * 60  # runs past midnight
            if start <= minute < end:
                is_open = True
        result = is_open
    return result if result is not None else False if any(c.isdigit() for c in hours) else None


# ---------------------------------------------------------------- stop search

def stop_candidates(graph: nx.MultiGraph, start: int, category: str, target_miles: float,
                    position: str, arrive: datetime | None, include_attacks: bool = False,
                    limit: int = 8, places: list[dict] | None = None) -> list[dict]:
    """Places of `category` that a route of `target_miles` can reach.

    position "end": the stop is the finish, so it must be at most ~85% of the
    distance away. "midway": it is the turnaround of a loop, so at most ~45%.
    """
    if places is None:
        places, _ = load_places(include_attacks)
    target_m = target_miles * METERS_PER_MILE
    reach = 0.85 if position == "end" else 0.45
    lengths = nx.single_source_dijkstra_path_length(graph, start, weight="length", cutoff=target_m * reach)
    s_lat, s_lon = graph.nodes[start]["y"], graph.nodes[start]["x"]
    out = []
    for place in places:
        if place["category"] != category:
            continue
        if haversine_m(s_lat, s_lon, place["lat"], place["lon"]) > target_m * reach:
            continue
        node = nearest_node(graph, place["lat"], place["lon"])
        if node not in lengths or node == start:
            continue
        open_now = is_open_at(place.get("opening_hours"), arrive) if arrive else None
        out.append({**place, "node": node,
                    "miles_from_start": round(lengths[node] / METERS_PER_MILE, 2),
                    "open_on_arrival": open_now})
    # open first, then unknown hours, then closed; then a comfortable distance away
    rank = {True: 0, None: 1, False: 2}
    ideal = target_miles * (0.6 if position == "end" else 0.4)
    out.sort(key=lambda p: (rank[p["open_on_arrival"]], abs(p["miles_from_start"] - ideal)))
    return out[:limit]


def amenities_along(graph: nx.MultiGraph, nodes: list[int], within_m: float = 60,
                    categories=("water", "restroom"), places: list[dict] | None = None) -> list[dict]:
    """Water fountains and restrooms close to the route."""
    if places is None:
        places, _ = load_places(False)
    pts = [(graph.nodes[n]["y"], graph.nodes[n]["x"]) for n in nodes[::3]]
    found = []
    for place in places:
        if place["category"] not in categories:
            continue
        if any(haversine_m(lat, lon, place["lat"], place["lon"]) <= within_m for lat, lon in pts):
            found.append(place)
    return found
