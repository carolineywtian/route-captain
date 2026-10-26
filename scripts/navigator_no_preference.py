"""With no stated route preference, does code pick the same route as the Navigator agent?

    .venv/bin/python scripts/navigator_no_preference.py     (needs a Portkey key)

The app skips the Navigator when the notes box is empty and lets code apply the Navigator's own
rule (among routes that pass the safety check: highest safety score, then closest to the requested
distance). To test that cut, this script makes the Navigator run anyway (a note that states no route
preference) and compares its pick with the code rule. Writes evaluation/navigator_no_preference.md.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import compose_request, finish_form  # noqa: E402
from route_captain.agents import plan_personal_run  # noqa: E402

STARTS = {"Cross Campus": (41.3083, -72.9279), "Wooster Square": (41.3040, -72.9180),
          "East Rock": (41.3260, -72.9200), "Westville": (41.3255, -72.9618), "Hill": (41.3020, -72.9350)}
DISTANCES = (3, 5, 8)
NOTE = "no route preference"   # makes the Navigator run without asking for parks, hills, flat, ...


def code_rule(options, miles):
    safe = [o for o in options if o["passes_safety"]] or options
    return max(safe, key=lambda o: (o["safety_score"], -abs(o["miles"] - miles)))


def main():
    rows, same, compared = [], 0, 0
    for name, (lat, lon) in STARTS.items():
        for miles in DISTANCES:
            res = plan_personal_run(compose_request(miles, "loop", ["none"], "", NOTE), lat, lon,
                                    datetime(2026, 10, 31, 9), 9.5, start_label=name,
                                    form={"distance": miles, **finish_form("loop", ["none"]), "notes": NOTE})
            opts = res.route_options
            if len(opts) < 2:
                rows.append(f"| {name}, {miles} mi | only one candidate route | – | – |")
                continue
            nav = next(o for o in opts if o.get("chosen"))
            code = code_rule(opts, miles)
            compared += 1
            same += nav["id"] == code["id"]
            rows.append(f"| {name}, {miles} mi | {nav['id']} ({nav['safety_score']}, {nav['miles']} mi) "
                        f"| {code['id']} ({code['safety_score']}, {code['miles']} mi) "
                        f"| {'same' if nav['id'] == code['id'] else '**different**'} |")
            print(rows[-1], flush=True)
    lines = ["# Navigator with no stated preference: does code pick the same route?", "",
             f"Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/navigator_no_preference.py` (real model calls). "
             f"Loops of {', '.join(map(str, DISTANCES))} mi from {len(STARTS)} starts; the note \"{NOTE}\" makes the "
             "Navigator run. Code rule: among routes that pass the safety check, highest safety score, then closest "
             "to the requested distance (the Navigator's own instruction when no preference is given).", "",
             f"**Code and the Navigator chose the same route in {same} of {compared} plans** "
             f"(plans with only one candidate route are listed but not compared).", "",
             "| Start, distance | Navigator pick (safety, miles) | Code pick | Same? |", "|---|---|---|---|", *rows]
    (ROOT / "evaluation" / "navigator_no_preference.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"same in {same} of {compared}")


if __name__ == "__main__":
    main()
