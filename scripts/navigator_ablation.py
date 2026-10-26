"""Would cutting the Navigator agent (letting code take the first safe candidate) make routes worse?

    .venv/bin/python scripts/navigator_ablation.py     (needs a Portkey key)

For requests with a stated preference, the route builder makes up to 3 candidates. We compare the
Navigator's pick and the code-only pick (the first candidate that passes the safety check) with the
candidate that best fits the preference among the safe ones. Writes evaluation/navigator_ablation.md.
"""
from __future__ import annotations

import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import compose_request, finish_form  # noqa: E402
from route_captain.agents import plan_personal_run  # noqa: E402
from route_captain.llm import price  # noqa: E402

STARTS = {"Cross Campus": (41.3083, -72.9279), "Wooster Square": (41.3040, -72.9180),
          "East Rock": (41.3260, -72.9200), "Westville": (41.3255, -72.9618)}
# preference text -> (metric, best is max?)
PREFERENCES = {
    "prefer parks and paths": ("path_or_sidewalk_miles", True),
    "I want hills today": ("climb_ft", True),
    "flat and easy please": ("climb_ft", False),
    "new streets, no retracing": ("retraces_itself_pct", False),
}


def main():
    rows, nav_hits, code_hits, compared, nav_tokens = [], 0, 0, 0, []
    for start_name, (lat, lon) in STARTS.items():
        for pref, (metric, high) in PREFERENCES.items():
            res = plan_personal_run(compose_request(5, "loop", ["none"], "", pref), lat, lon, datetime(2026, 10, 31, 9),
                                    9.5, start_label=start_name, form={"distance": 5, **finish_form("loop", ["none"]), "notes": pref})
            opts = [o for o in res.route_options if o["passes_safety"]]
            if len(opts) < 2:
                rows.append(f"| {start_name} | {pref} | only {len(opts)} safe candidate(s) | – | – |")
                continue
            values = [o[metric] for o in opts]
            best = max(values) if high else min(values)
            nav = next(o for o in res.route_options if o.get("chosen"))
            code = opts[0]
            nav_ok, code_ok = nav[metric] == best, code[metric] == best
            compared += 1
            nav_hits += nav_ok
            code_hits += code_ok
            nav_tokens += [u["input_tokens"] + u["output_tokens"] for u in res.usage if u["agent"] == "Navigator"]
            rows.append(f"| {start_name} | {pref} | {metric}: " + ", ".join(f"{v:g}" for v in values)
                        + f" | {nav[metric]:g} {'✅' if nav_ok else '❌'} | {code[metric]:g} {'✅' if code_ok else '❌'} |")
            print(rows[-1], flush=True)
    nav_cost = statistics.mean(price({"input_tokens": t, "cached_input_tokens": 0, "output_tokens": 0}) for t in nav_tokens) if nav_tokens else 0
    lines = ["# Navigator ablation: is the Navigator agent worth its cost?", "",
             f"Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/navigator_ablation.py` (real model calls).", "",
             f"Requests where at least 2 safe candidates existed: **{compared}**.", "",
             f"- Navigator (AI) picked the candidate that best fits the stated preference: **{nav_hits} of {compared}**",
             f"- Code only (first safe candidate) did: **{code_hits} of {compared}**",
             f"- The Navigator uses about {statistics.mean(nav_tokens):,.0f} tokens per plan (≈ ${nav_cost:.5f} at input "
             "prices; output is a short reason)." if nav_tokens else "", "",
             "| Start | Preference | Safe candidates' values | Navigator's pick | Code-only pick |", "|---|---|---|---|---|", *rows]
    (ROOT / "evaluation" / "navigator_ablation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[4:8]))


if __name__ == "__main__":
    main()
