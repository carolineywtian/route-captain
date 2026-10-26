"""Map layers (steepness colors, hills, sunset, safety line) and form choices. No API key needed."""
from route_captain.models import RunRequest


def test_steeper_is_darker_and_thicker():
    from app import _grade_style
    steep, climb, flat, down = (_grade_style(g) for g in (0.08, 0.04, 0.0, -0.05))
    assert steep[2] == "steep climb" and climb[2] == "climb" and flat[2] == "flat" and down[2] == "downhill"
    assert steep[1] > climb[1] > flat[1]


def test_hill_coords_cover_only_the_hill():
    from app import _hill_coords
    segs = [{"start": 0.0, "end": 0.1, "coords": [[1, 1], [1, 2]]},
            {"start": 0.1, "end": 0.2, "coords": [[1, 2], [1, 3]]},
            {"start": 0.2, "end": 0.3, "coords": [[1, 3], [1, 4]]}]
    assert _hill_coords(segs, {"start": 0.1, "end": 0.3}) == [[1, 2], [1, 3], [1, 4]]


def test_sunset_spot_is_where_the_runner_is_at_sunset():
    from app import _sunset_spot
    segs = [{"start": i / 10, "end": (i + 1) / 10, "pace": 10.0, "coords": [[i, 0], [i + 1, 0]]} for i in range(30)]
    msg = {"when": "2026-10-25T17:30"}
    spot = _sunset_spot(msg, segs, {"sunset": "5:50 PM", "daylight": "near_sunset"})
    assert spot == ([19, 0], "5:50 PM")      # 20 minutes in at 10 min/mile -> mile 2.0
    assert _sunset_spot(msg, segs, {"sunset": "5:50 PM", "daylight": "ok"}) is None


def test_safety_line_summarizes_what_the_map_shows():
    from app import _safety_line
    line = _safety_line({"score": 72, "busy_crossings": {"unmarked": 2, "marked": 3, "signalized": 1},
                         "miles_by_type": {"busy_road": 0.4}}, 2)
    assert line == "🛡 72/100 · ⚠ 2 unmarked crossings · 4 marked/signal crossings · 0.4 mi on busy roads · ⛰ 2 hills"


def test_form_choices_override_the_planner():
    from route_captain.agents import _apply_form
    req = RunRequest(distance_miles=4, shape="end_at_stop", stop_category="park", stop_position="end")
    changed = _apply_form(req, {"distance": 4, "shape": "loop", "main_stop": None, "extra_stops": [],
                                "stop_position": "end"}, has_finish=True)
    assert req.stop_category == "none" and req.stop_position == "none" and req.shape == "loop"
    assert changed


def test_safety_report_records_crossing_locations():
    from datetime import datetime

    from route_captain.mapdata import load_graph, nearest_node
    from route_captain.routing import plan_route
    from route_captain.safety import score_route
    g = load_graph()
    rep = score_route(g, plan_route(g, nearest_node(g, 41.3104, -72.9273), 5), datetime(2026, 10, 24, 9))
    assert len(rep.crossing_points) == sum(rep.busy_crossings.values())
