"""Run club mode: one route, several pace groups, everyone meets at the finish (code, not the LLM).

Every group runs the same route at its own pace; nobody waits for anyone on the way. For each group
this works out its even-effort time (from the route's hills), its target pace on the biggest climbs
and when it reaches the finish, so the club knows who arrives when at the meetup.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .elevation import fmt_pace
from .timefmt import fmt

GROUP_NAMES = "ABCD"
MAX_GROUPS = 4
PACE_ERROR = ("Pace groups should look like 8:00, 9:30, 11:00 (minutes per mile): "
              "2 to 4 different paces between 4:00 and 20:00.")


def parse_paces(text: str | None) -> list[float]:
    """'8:00, 9:30, 11' -> [8.0, 9.5, 11.0], fastest first. Raises ValueError with a friendly message."""
    parts = [p for p in re.split(r"[,\s;/]+", (text or "").strip()) if p]
    paces = []
    for part in parts:
        m = re.fullmatch(r"(\d{1,2})(?::([0-5]\d))?", part)
        if not m:
            raise ValueError(PACE_ERROR)
        pace = int(m.group(1)) + int(m.group(2) or 0) / 60
        if not 4 <= pace <= 20:
            raise ValueError(PACE_ERROR)
        paces.append(pace)
    paces = sorted(set(round(p, 4) for p in paces))
    if not 2 <= len(paces) <= MAX_GROUPS:
        raise ValueError(PACE_ERROR)
    return paces


def plan_club(paces: list[float], route_miles: float, start_time: datetime, profile: dict | None,
              run_pace: float, finish_label: str) -> dict:
    """Each group's pace, time and arrival at the finish.

    `profile` is the route's even-effort profile computed at `run_pace`; hill effort scales with
    flat pace, so each group's time and hill paces scale by pace / run_pace.
    """
    profile = profile or {}
    base_minutes = profile.get("minutes") or route_miles * run_pace
    hills = profile.get("hills") or []
    groups, finishes = [], []
    for i, pace in enumerate(sorted(paces)):
        scale = pace / run_pace
        minutes = base_minutes * scale
        finishes.append(start_time + timedelta(minutes=minutes))
        groups.append({
            "name": f"Group {GROUP_NAMES[i]}", "pace": pace, "pace_str": fmt_pace(pace),
            "minutes": round(minutes), "finish": fmt(finishes[-1], "%-I:%M %p"),
            "hills": [{"start": h["start"], "end": h["end"], "gain_ft": h["gain_ft"],
                       "pace_str": fmt_pace(_pace_of(h["pace_str"]) * scale)} for h in hills],
        })
    return {"groups": groups, "route_miles": round(route_miles, 2), "finish_label": finish_label,
            "start": fmt(start_time, "%-I:%M %p"),
            # minutes between the first and last arrival, from the same clock times the runners see
            "spread_min": round((finishes[-1].replace(second=0, microsecond=0)
                                 - finishes[0].replace(second=0, microsecond=0)).total_seconds() / 60)}


def _pace_of(text: str) -> float:
    m, s = text.split(":")
    return int(m) + int(s) / 60


def club_brief(club: dict) -> dict:
    """What the Run Club Captain agent sees."""
    return {
        "route_miles": club["route_miles"], "start_time": club["start"], "meet_at": club["finish_label"],
        "minutes_between_first_and_last_group": club["spread_min"],
        "groups": [{"name": g["name"], "pace": g["pace_str"] + "/mi", "minutes": g["minutes"],
                    "arrives_at_finish": g["finish"],
                    "biggest_hill": ({"miles": f"{g['hills'][0]['start']:.1f}-{g['hills'][0]['end']:.1f}",
                                      "gain_ft": g["hills"][0]["gain_ft"], "target_pace": g["hills"][0]["pace_str"]}
                                     if g["hills"] else None)}
                   for g in club["groups"]],
    }
