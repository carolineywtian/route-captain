"""Regression tests for the issues found in the code review. No API key needed."""
from datetime import datetime

from route_captain.places import is_open_at


def test_overnight_hours_count_after_midnight():
    assert is_open_at("Fr 18:00-02:00", datetime(2026, 10, 24, 1, 0)) is True    # Saturday 1 am
    assert is_open_at("Fr 18:00-02:00", datetime(2026, 10, 24, 3, 0)) is not True


def test_hours_without_colons_do_not_crash():
    assert is_open_at("Mo-Fr 7-18", datetime(2026, 10, 26, 9, 0)) is True       # Monday 9 am
    assert is_open_at("garbage-hours", datetime(2026, 10, 26, 9, 0)) is None


def test_avoid_street_matches_whole_words_only():
    import networkx as nx

    from route_captain.agents import _avoid_graph
    g = nx.MultiGraph()
    g.add_edge(1, 2, name="Elm Street", cost=1.0)
    g.add_edge(2, 3, name="Elmwood Avenue", cost=1.0)
    out = _avoid_graph(g, ["Elm St"])
    assert out[1][2][0]["cost"] == 20.0
    assert out[2][3][0]["cost"] == 1.0


def test_ics_is_utc_and_lines_fit_75_bytes():
    from route_captain.export import to_ics
    plan = {"briefing": {"headline": "5 mi → East Rock – café ☕ " * 3, "summary": "s", "key_moments": [],
                         "safety_notes": []},
            "start": {"label": "Cross Campus"}, "miles": 5.0, "safety": {"score": 90},
            "gmaps": "https://www.google.com/maps/dir/?api=1"}
    ics = to_ics(plan, datetime(2026, 10, 24, 9, 0), 48, "Sat")
    assert "DTSTART:20261024T130000Z" in ics          # 9:00 am New York = 13:00 UTC in October
    assert "DTEND:20261024T134800Z" in ics
    assert max(len(line.encode()) for line in ics.split("\r\n")) <= 75


def test_map_clears_after_a_failed_plan():
    from app import _latest
    ok = {"role": "captain", "plan": {"coords": [[41.3, -72.9], [41.31, -72.91]]}}
    failed = {"role": "captain", "plan": {"error": "Could not find that place."}}
    assert _latest([{"role": "user"}, ok]) is ok
    assert _latest([{"role": "user"}, ok, {"role": "user"}, failed]) is None


def test_plan_card_survives_a_result_without_a_route():
    from app import render_history
    out = render_history([{"role": "user", "text": "5 miles"},
                          {"role": "captain", "plan": {"briefing": None, "safety": None, "coords": []}}])
    assert "Couldn't plan that" in str(out)
