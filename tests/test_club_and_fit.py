"""Run club mode and fit-a-run-before-my-next-event (code only, no API key needed)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from route_captain.club import parse_paces, plan_club
from route_captain.schedule import fit_before_next_event

TZ = ZoneInfo("America/New_York")


def test_parse_paces_sorts_and_validates():
    assert parse_paces("11:00, 8:00 9:30") == [8.0, 9.5, 11.0]
    assert parse_paces("8, 10") == [8.0, 10.0]
    for bad in ("9:00", "", "8:00, fast", "3:00, 9:00", "8:00, 9:00, 10:00, 11:00, 12:00", "8:75, 9:00"):
        with pytest.raises(ValueError):
            parse_paces(bad)


def test_each_group_runs_its_own_pace_and_meets_at_the_finish():
    profile = {"minutes": 50.0, "hills": [{"start": 1.5, "end": 1.7, "gain_ft": 40, "pace_str": "11:00"}]}
    club = plan_club([8.0, 10.0], 5.0, datetime(2026, 10, 31, 9), profile, 10.0, "East Rock Bakery")
    fast, slow = club["groups"]
    assert (fast["name"], slow["name"]) == ("Group A", "Group B")
    assert slow["minutes"] == 50 and fast["minutes"] == 40          # time scales with pace, hills included
    assert (fast["finish"], slow["finish"]) == ("9:40 AM", "9:50 AM")
    assert fast["hills"][0]["pace_str"] == "8:48" and slow["hills"][0]["pace_str"] == "11:00"
    assert club["spread_min"] == 10 and club["finish_label"] == "East Rock Bakery"


def test_club_without_elevation_uses_flat_pace():
    club = plan_club([7.0, 9.0, 12.0], 4.0, datetime(2026, 10, 31, 18), None, 12.0, "Cross Campus")
    assert [g["minutes"] for g in club["groups"]] == [28, 36, 48]
    assert all(g["hills"] == [] for g in club["groups"])


def _busy(*spans):
    d = lambda h, m=0: datetime(2026, 10, 26, h, m, tzinfo=TZ)  # noqa: E731
    return [(d(*s), d(*e)) for s, e in spans]


def test_fit_sizes_the_run_to_the_gap():
    busy = _busy(((10,), (11,)), ((15,), (16,)))
    fit = fit_before_next_event(busy, datetime(2026, 10, 26, 13, 58, tzinfo=TZ), 9.5)
    assert fit["status"] == "fit" and fit["start"].strftime("%H:%M") == "14:00"
    assert fit["miles"] == 4.5
    assert (fit["event"] - fit["back_by"]).total_seconds() / 60 >= 15  # back with time to change


def test_fit_handles_busy_now_short_gaps_and_free_afternoons():
    busy = _busy(((10,), (11,)), ((15,), (16,)))
    short = fit_before_next_event(busy, datetime(2026, 10, 26, 14, 40, tzinfo=TZ), 9.5)
    assert short["status"] == "too_short"
    moved = fit_before_next_event(busy, datetime(2026, 10, 26, 10, 20, tzinfo=TZ), 9.5)
    assert moved["moved"] and moved["start"].strftime("%H:%M") == "11:00" and moved["status"] == "free"


def test_deadline_only_applies_to_its_own_run():
    from app import _deadline_for
    deadline = {"event": "2026-10-26T15:00", "buffer": 15}
    assert _deadline_for(deadline, datetime(2026, 10, 26, 14, 0)) == deadline
    assert _deadline_for(deadline, datetime(2026, 10, 31, 9, 0)) is None
