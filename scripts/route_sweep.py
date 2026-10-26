"""Route sweep: builds routes across the whole map and checks every one loads properly.

No API key needed (tools only, no LLM). Writes evaluation/route_sweep.md.
    .venv/bin/python scripts/route_sweep.py
"""
from __future__ import annotations

import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from route_captain.directions import route_cues  # noqa: E402
from route_captain.elevation import profile  # noqa: E402
from route_captain.geocode import LANDMARKS  # noqa: E402
from route_captain.mapdata import haversine_m, load_graph, nearest_node  # noqa: E402
from route_captain.places import stop_candidates  # noqa: E402
from route_captain.routing import DISTANCE_TOLERANCE, plan_route, route_candidates, route_coords  # noqa: E402
from route_captain.safety import score_route  # noqa: E402

EXTRA_STARTS = {  # spread across the map, including near its edges
    "Westville (Edgewood Park)": (41.3255, -72.9618),
    "Fair Haven (Front St)": (41.3065, -72.8955),
    "Wooster Sq": (41.3040, -72.9150),
    "Hill neighborhood": (41.2960, -72.9380),
    "Prospect Hill": (41.3290, -72.9280),
    "Long Wharf": (41.2910, -72.9190),
}
DISTANCES = [3, 5, 6.2, 10, 13.1]
WHEN = datetime(2026, 10, 24, 9, 0)


def check(graph, route, target, start, end=None):
    problems = []
    if route.nodes[0] != start:
        problems.append("wrong start")
    if end is not None and route.nodes[-1] != end:
        problems.append("wrong finish")
    if abs(route.miles - target) > DISTANCE_TOLERANCE * target and not route.notes:
        problems.append(f"distance {route.miles:.2f} vs {target}")
    coords = route_coords(graph, route.nodes)
    drawn = sum(haversine_m(*a, *b) for a, b in zip(coords, coords[1:]))
    if abs(drawn - route.length_m) > 0.03 * route.length_m:
        problems.append("map line does not match route")
    rep = score_route(graph, route, WHEN)
    prof = profile(graph, route.nodes, 9.5)
    if not prof or any(math.isnan(s["elev_ft"]) for s in prof["samples"]):
        problems.append("no elevation profile")
    elif not 0 <= prof["min_ft"] <= prof["max_ft"] <= 600:
        problems.append(f"odd elevation {prof['min_ft']}-{prof['max_ft']} ft")
    if not route_cues(graph, route.nodes):
        problems.append("no directions")
    return problems, rep, prof


def main():
    graph = load_graph()
    starts = {**LANDMARKS, **EXTRA_STARTS}
    rows, failures, timings, errors = [], [], [], []

    def record(label, fn, target, start, end=None):
        t = time.time()
        try:
            routes = fn()
        except Exception as exc:  # a route that cannot be built is a failure to report
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            return
        timings.append(time.time() - t)
        routes = routes if isinstance(routes, list) else [routes]
        for k, r in enumerate(routes):
            problems, rep, prof = check(graph, r, target, start, end)
            rows.append({"case": label, "option": k + 1, "miles": r.miles, "target": target,
                         "error_pct": abs(r.miles - target) / target * 100, "safety": rep.score,
                         "passes": rep.passes, "climb": prof.get("gain_ft", 0), "notes": "; ".join(r.notes)})
            if problems:
                failures.append(f"{label} option {k + 1}: {', '.join(problems)}")

    for name, (lat, lon) in starts.items():
        s = nearest_node(graph, lat, lon)
        for d in DISTANCES:
            record(f"{name} · {d} mi loop", lambda s=s, d=d: route_candidates(graph, s, d), d, s)
            record(f"{name} · {d} mi out & back",
                   lambda s=s, d=d: route_candidates(graph, s, d, prefer="out_and_back"), d, s)
        bakeries = stop_candidates(graph, s, "bakery", 5, "end", WHEN)
        if bakeries:
            b = bakeries[0]
            record(f"{name} · 5 mi ending at {b['name']}",
                   lambda s=s, b=b: route_candidates(graph, s, 5, end=b["node"]), 5, s, b["node"])
        cafes = stop_candidates(graph, s, "cafe", 4, "midway", WHEN)
        if cafes:
            c = cafes[0]
            record(f"{name} · 4 mi with {c['name']} halfway",
                   lambda s=s, c=c: [plan_route(graph, s, 2, end=c["node"])], 2, s, c["node"])
    finish = nearest_node(graph, *LANDMARKS["East Rock Summit"])
    for name in ("Cross Campus", "Yale Bowl", "Union Station"):
        s = nearest_node(graph, *LANDMARKS[name])
        record(f"{name} → East Rock Summit · 5 mi", lambda s=s: route_candidates(graph, s, 5, end=finish), 5, s, finish)

    out = ROOT / "evaluation" / "route_sweep.md"
    out.parent.mkdir(exist_ok=True)
    within = sum(r["error_pct"] <= DISTANCE_TOLERANCE * 100 for r in rows)
    lines = [
        "# Route sweep", "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/route_sweep.py` (tools only, no LLM).", "",
        "| Metric | Result |", "|---|---|",
        f"| Requests built | {len(timings)} of {len(timings) + len(errors)} |",
        f"| Routes checked (incl. alternative options) | {len(rows)} |",
        f"| Within ±5% of requested distance | {within} of {len(rows)} ({within / max(len(rows), 1):.0%}) |",
        f"| Median distance error | {statistics.median(r['error_pct'] for r in rows):.1f}% |",
        f"| Pass the safety check | {sum(r['passes'] for r in rows)} of {len(rows)} |",
        f"| Median safety score | {statistics.median(r['safety'] for r in rows):.0f}/100 |",
        f"| Routes with problems (wrong start/finish, broken map line, no elevation, no directions) | {len(failures)} |",
        f"| Build time per request (median / max) | {statistics.median(timings):.1f} s / {max(timings):.1f} s |",
        "",
    ]
    if errors:
        lines += ["## Requests that could not be built", ""] + [f"- {e}" for e in errors] + [""]
    if failures:
        lines += ["## Problems", ""] + [f"- {f}" for f in failures] + [""]
    off = [r for r in rows if r["error_pct"] > DISTANCE_TOLERANCE * 100]
    if off:
        lines += ["## Outside ±5% (with the reason the app gives the runner)", "",
                  "| Case | Option | Miles | Target | Note |", "|---|---|---|---|---|"]
        lines += [f"| {r['case']} | {r['option']} | {r['miles']:.2f} | {r['target']} | {r['notes'] or '—'} |"
                  for r in off] + [""]
    lines += ["## All routes", "", "| Case | Option | Miles | Safety | Passes | Climb (ft) |",
              "|---|---|---|---|---|---|"]
    lines += [f"| {r['case']} | {r['option']} | {r['miles']:.2f} | {r['safety']} | {'yes' if r['passes'] else 'no'} "
              f"| {r['climb']} |" for r in rows]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[4:14]))
    if errors:
        print("ERRORS:", *errors, sep="\n  ")
    if failures:
        print("PROBLEMS:", *failures[:20], sep="\n  ")


if __name__ == "__main__":
    main()
