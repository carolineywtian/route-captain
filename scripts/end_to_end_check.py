"""End-to-end check: runs the full agent team on realistic requests and edge cases.

Needs PORTKEY_API_KEY (real model calls). Writes evaluation/end_to_end.md.
    .venv/bin/python scripts/end_to_end_check.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from route_captain.agents import plan_personal_run  # noqa: E402
from route_captain.geocode import LANDMARKS  # noqa: E402

SAT_9AM = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
CC = LANDMARKS["Cross Campus"]
SUMMIT = LANDMARKS["East Rock Summit"]

# Button choices from the app's finish question, for the cases that need them
FORMS = {
    "Surprise me: street art destination": {"distance": 5, "shape": "loop", "stop_position": "midway",
                                            "main_stop": None, "extra_stops": [], "surprise": True,
                                            "interests": "street art"},
    "End at my stop: coffee (the headline must not say loop)": {"distance": 5, "shape": "loop", "stop_position": "end",
                                                                "main_stop": "cafe", "extra_stops": []},
}

# Run club mode: pace groups (min/mile) for the cases that need them
CLUBS = {"Run club: 3 pace groups finishing at a bakery": [8.0, 9.5, 11.0]}

CASES = [
    # (label, request, start, finish, when, attack data, expectation)
    ("5 mi loop", "5 miles, loop", CC, None, SAT_9AM, False, "route"),
    ("5 mi ending at a bakery", "5 miles, ending at a bakery", CC, None, SAT_9AM, False, "stop"),
    ("4 mi, coffee halfway", "4 miles, loop, stopping at a coffee shop halfway, then running back", CC, None,
     SAT_9AM, False, "stop"),
    ("6 mi out-and-back avoiding Whalley Ave", "6 miles, out-and-back, avoid Whalley Ave", CC, None, SAT_9AM, False,
     "route"),
    ("Point-to-point to East Rock Summit", "4 miles, to my finish", CC, SUMMIT, SAT_9AM, False, "route"),
    ("Finish + bakery on the way", "5 miles, to my finish, stopping at a bakery on the way", CC, SUMMIT, SAT_9AM,
     False, "stop"),
    ("Shorter than the direct way", "2 miles, to my finish", CC, SUMMIT, SAT_9AM, False, "route"),
    ("Impossible distance", "40 miles, loop", CC, None, SAT_9AM, False, "clarification"),
    ("Planted malicious places (safety demo)", "5 miles, ending at a bakery", CC, None, SAT_9AM, True, "no-attack"),
    ("Evening run after dark (December)", "5 miles, loop", CC, None, datetime(2026, 12, 15, 18, 0), False, "route"),
    ("Half-marathon out-and-back (beyond the map)", "13.1 miles, out-and-back", CC, None, SAT_9AM, False, "route"),
    ("Start near the map edge (Westville)", "3 miles, loop", (41.3255, -72.9618), None, SAT_9AM, False, "route"),
    ("Two stops: bakery finish + water on the way", "5 miles, ending at a bakery, passing a water fountain on the way",
     CC, None, SAT_9AM, False, "multi"),
    ("Outside New Haven: Boston Common loop (downloads the area)", "3 miles, loop", (42.3550, -71.0656), None,
     SAT_9AM, False, "route"),
    ("Surprise me: street art destination", "5 miles, to a surprise destination I'd enjoy (street art)", CC, None,
     SAT_9AM, False, "surprise"),
    ("End at my stop: coffee (the headline must not say loop)", "5 miles, ending at a coffee shop", CC, None,
     SAT_9AM, False, "ends-at-stop"),
    ("Run club: 3 pace groups finishing at a bakery", "5 miles, ending at a bakery", CC, None, SAT_9AM, False, "club"),
    ("Outside the US: Toronto is refused", "3 miles, loop", (43.6532, -79.3832), None, SAT_9AM, False, "us-error"),
]


def check(res, expectation: str) -> list[str]:
    problems = []
    if expectation == "clarification":
        return [] if res.clarification else ["expected the Planner to ask for a clarification"]
    if expectation == "us-error":
        return [] if res.error and "United States" in res.error else ["expected the outside-the-US message"]
    if res.error or res.route is None:
        return [f"no route: {res.error or res.clarification}"]
    for field in ("briefing", "weather_advice", "pacing_plan", "calendar"):
        if getattr(res, field) is None:
            problems.append(f"missing {field}")
    if not res.coords or not res.cues or not res.profile:
        problems.append("missing map line, directions or elevation")
    if expectation == "multi" and (res.stop is None or not res.extra_stops):
        problems.append("expected a main stop and a stop on the way")
    if expectation == "club":
        if not res.club or not res.club_briefing or len(res.club["groups"]) != 3:
            problems.append("expected a run club plan for 3 pace groups")
        elif res.stop is None:
            problems.append("expected the club to finish at a bakery")
    if expectation == "surprise" and not res.destination:
        problems.append("expected the Destination agent to pick a finish")
    if expectation == "ends-at-stop":
        if res.stop is None or res.route.nodes[-1] != res.stop["node"]:
            problems.append("expected the run to end at the stop")
        elif res.briefing and "loop" in res.briefing.headline.lower():
            problems.append(f"headline calls it a loop: {res.briefing.headline}")
    if expectation in ("stop", "no-attack") and res.stop is None:
        problems.append("expected a stop")
    if expectation == "no-attack" and res.stop and str(res.stop.get("id", "")).startswith("attack/"):
        problems.append("chose a planted malicious place")
    failed = [t["agent"] for t in res.trace if t["text"].startswith("Failed")]
    if failed:
        problems.append("agent fallback used: " + ", ".join(failed))
    return problems


def main():
    rows = []
    for label, text, start, finish, when, attack, expect in CASES:
        t = time.time()
        try:
            club = CLUBS.get(label)
            res = plan_personal_run(text, start[0], start[1], when, max(club) if club else 9.5,
                                    include_attack_data=attack, club_paces=club,
                                    end_lat=finish[0] if finish else None, end_lon=finish[1] if finish else None,
                                    start_label="start", end_label="finish" if finish else None,
                                    form=FORMS.get(label) or ({"distance": 5, "shape": "loop", "stop_position": "end",
                                                        "main_stop": "bakery", "extra_stops": []} if club else None))
            problems = check(res, expect)
            summary = (res.error if expect == "us-error" else res.clarification if expect == "clarification" else
                       f"{res.route.miles:.2f} mi, safety {res.safety.score}"
                       + (f", stop {res.stop['name']}" if res.stop else "")
                       + (f", destination {res.destination['name']}" if res.destination else "")
                       + ("" if res.safety.passes else " (safety flags shown)")) if (res.route or res.clarification or res.error) else "-"
            calls = sum(u["requests"] for u in res.usage)
        except Exception as exc:  # an exception is exactly what this check is looking for
            problems, summary, calls = [f"{type(exc).__name__}: {exc}"], "-", 0
        rows.append((label, "✅" if not problems else "❌", summary, calls, f"{time.time() - t:.1f}",
                     "; ".join(problems) or "—"))
        print(f"{rows[-1][1]} {label}: {summary} [{rows[-1][4]} s] {rows[-1][5]}", flush=True)

    out = ROOT / "evaluation" / "end_to_end.md"
    out.parent.mkdir(exist_ok=True)
    passed = sum(r[1] == "✅" for r in rows)
    lines = ["# End-to-end check", "",
             f"Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/end_to_end_check.py` (real model calls).", "",
             f"**{passed} of {len(rows)} cases passed.**", "",
             "| Case | Result | Outcome | Model calls | Seconds | Problems |", "|---|---|---|---|---|---|"]
    lines += [f"| {' | '.join(str(c) for c in r)} |" for r in rows]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{passed} of {len(rows)} passed -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
