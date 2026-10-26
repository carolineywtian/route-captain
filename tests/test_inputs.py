"""User-input edge cases: pace, calendar files and links. No API key needed."""
from datetime import date

import pytest

from route_captain import schedule


@pytest.mark.parametrize("text,expected", [("9:30", 9.5), ("10:00", 10.0), ("8.5", 8.5), ("7:05", 7 + 5 / 60)])
def test_valid_paces(text, expected):
    from app import _pace
    assert _pace(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["abc", "", None, "9:75", "2:00", "45", "-9"])
def test_invalid_paces_are_rejected(text):
    from app import _pace
    assert _pace(text) is None


def test_non_calendar_file_gives_a_clear_error():
    with pytest.raises(ValueError, match="isn't a calendar"):
        schedule.busy_times(b"this is a photo, not a calendar", date.today(), 3)


def test_broken_calendar_link_gives_a_clear_error(monkeypatch):
    class Resp:
        status_code, ok, content = 404, False, b""
    monkeypatch.setattr(schedule.requests, "get", lambda *a, **k: Resp())
    with pytest.raises(ValueError, match="didn't work"):
        schedule.fetch_ics("https://example.com/cal.ics")


def test_private_calendar_link_explains_how_to_publish(monkeypatch):
    class Resp:
        status_code, ok, content = 403, False, b""
    monkeypatch.setattr(schedule.requests, "get", lambda *a, **k: Resp())
    with pytest.raises(ValueError, match="private"):
        schedule.fetch_ics("webcal://outlook.office365.com/owa/calendar/x/calendar.ics")


def test_link_that_is_not_a_calendar(monkeypatch):
    class Resp:
        status_code, ok, content = 200, True, b"<html>Sign in</html>"
    monkeypatch.setattr(schedule.requests, "get", lambda *a, **k: Resp())
    with pytest.raises(ValueError, match="didn't return a calendar"):
        schedule.fetch_ics("https://example.com/page")


def _answers(**kw):
    a = dict(start_text="Cross Campus", distance=5, mode="loop", finish_text="", when="2099-10-31T09:00",
             run_mode="solo", pace="9:30", club_paces="8:00, 9:30", suggested={})
    a.update(kw)
    return a


def test_each_question_flags_answers_that_cannot_work():
    from app import DISTANCE_ERROR, FINISH_MISSING, PAST_TIME_ERROR, _check_question
    assert _check_question("start", _answers(start_text="  "))[0] == "start-error"
    assert _check_question("start", _answers()) is None                      # a built-in landmark
    for bad in (0.5, 40, None, "abc"):
        assert _check_question("distance", _answers(distance=bad)) == ("distance-error", DISTANCE_ERROR)
    assert _check_question("distance", _answers(distance=26.2)) is None
    assert _check_question("finish", _answers(mode="place")) == ("finish-error", FINISH_MISSING)
    assert _check_question("finish", _answers(mode="loop")) is None          # no finish needed for a loop
    assert _check_question("when", _answers(when="2020-01-01T09:00")) == ("when-error", PAST_TIME_ERROR)
    assert _check_question("when", _answers(pace="fast"))[0] == "when-error"
    assert _check_question("when", _answers(run_mode="club", club_paces="9:00"))[0] == "when-error"
    assert _check_question("when", _answers(run_mode="club")) is None


def test_plan_another_run_clears_trip_answers_but_keeps_personal_ones():
    from app import fresh_answers
    stop, finish_mode, finish_text, interests, avoid, notes, pins, deadline, fit, clicks, demo, dist, when = fresh_answers(1)
    assert (stop, finish_mode, finish_text, pins, deadline, clicks, demo) == (["none"], "loop", "", {}, None, "off", [])
    from dash import no_update
    assert dist is no_update and when is no_update                 # an ordinary distance and time stay
    *_, dist, when = fresh_answers(1, {"event": "2026-10-26T15:00", "buffer": 15})
    assert dist == 5 and when                                      # a fitted distance leaves with its event


def test_only_real_map_pins_count_as_new():
    from app import _new_pins
    plan = {"plan": {"start": {"lat": 41.3083, "lon": -72.9279}, "finish": None,
                     "coords": [[41.3083, -72.9279], [41.3083, -72.9279]]}}
    pin = {"start": {"lat": 41.32, "lon": -72.93, "text": "41.32000, -72.93000"}}
    assert _new_pins(pin, plan, "41.32000, -72.93000") == pin       # dropped and still the start
    assert _new_pins(pin, plan, "Cross Campus") == {}              # the runner picked something else
    assert _new_pins(pin, None, "41.32000, -72.93000") == {}       # nothing to re-plan after a failed plan
    fin = {"finish": {"lat": 41.33, "lon": -72.91, "text": "41.33000, -72.91000"}}
    assert _new_pins(fin, plan, None, "41.33000, -72.91000", "loop") == {}   # finish pin only counts for "End at a place"


def test_plan_another_run_goes_back_to_the_solo_or_club_choice(monkeypatch):
    import app
    from app import back_to_questions, mode_pill
    monkeypatch.setattr(app, "ctx", type("C", (), {"triggered_id": "new-btn"})())  # stand-in for Dash's context
    assert back_to_questions(0, 1) == ("welcome", 0)
    assert "Run club" in str(mode_pill("club")) and "Solo run" in str(mode_pill("solo"))


def test_env_is_read_from_the_repo_then_the_folder_above(tmp_path, monkeypatch):
    from route_captain.llm import load_env_files
    submissions, repo = tmp_path, tmp_path / "route-captain"
    repo.mkdir()
    (submissions / ".env").write_text("PORTKEY_API_KEY=staff-key\nOPENAI_API_KEY=staff-openai\n")
    # a .env copied from .env.example but never edited must not hide the staff key above
    (repo / ".env").write_text("PORTKEY_API_KEY=your-portkey-key-here\n")
    monkeypatch.delenv("PORTKEY_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)   # restored after the test
    load_env_files((repo, submissions))
    import os
    assert os.environ["PORTKEY_API_KEY"] == "staff-key"
    # a real key in the repo's own .env comes first
    (repo / ".env").write_text("PORTKEY_API_KEY=my-key\n")
    monkeypatch.delenv("PORTKEY_API_KEY")
    load_env_files((repo, submissions))
    assert os.environ["PORTKEY_API_KEY"] == "my-key"


def test_apartment_and_unit_numbers_are_ignored_for_lookups():
    from route_captain.geocode import strip_unit
    assert strip_unit("24 Clark St Apt 2, New Haven, CT 06511") == "24 Clark St, New Haven, CT 06511"
    assert strip_unit("24 Clark Street #3 New Haven CT") == "24 Clark Street New Haven CT"
    assert strip_unit("10 Main St Unit 4B") == "10 Main St"
    assert strip_unit("12 Oak St Apt B") == "12 Oak St"
    # street and place names that only look like units are left alone
    assert strip_unit("1 Apt Rd") == "1 Apt Rd"
    assert strip_unit("Suite Spot Cafe") == "Suite Spot Cafe"
