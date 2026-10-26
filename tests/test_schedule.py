"""Best-time scheduler: calendar parsing, free slots, traffic and scoring. No network needed."""
from datetime import date, datetime, timedelta
from pathlib import Path

import route_captain.schedule as sched

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "sample_calendar.ics"
ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:a
DTSTART;TZID=America/New_York:{d}T090000
DTEND;TZID=America/New_York:{d}T100000
SUMMARY:Secret meeting title
END:VEVENT
BEGIN:VEVENT
UID:b
DTSTART;TZID=America/New_York:{d}T120000
DTEND;TZID=America/New_York:{d}T130000
SUMMARY:Optional
TRANSP:TRANSPARENT
END:VEVENT
BEGIN:VEVENT
UID:c
DTSTART;VALUE=DATE:{d}
SUMMARY:Holiday
END:VEVENT
BEGIN:VEVENT
UID:d
DTSTART;TZID=America/New_York:{d}T150000
DTEND;TZID=America/New_York:{d}T160000
SUMMARY:Weekly class
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
END:VCALENDAR
"""


def _ics(day: date) -> bytes:
    return ICS.format(d=day.strftime("%Y%m%d")).encode()


def test_busy_times_skips_free_all_day_and_expands_repeats():
    day = date.today() + timedelta(days=1)
    busy, n = sched.busy_times(_ics(day), day, 3)
    starts = [(b[0].date(), b[0].hour) for b in busy]
    assert (day, 9) in starts                       # normal event
    assert (day, 12) not in starts                  # marked free
    assert sum(1 for _, h in starts if h == 15) == 3  # daily class repeats 3 times
    assert n == 4


def test_sample_calendar_parses():
    busy, n = sched.busy_times(SAMPLE.read_bytes(), date.today(), 7)
    assert n >= 3 and all(b0 < b1 for b0, b1 in busy)


def test_free_slots_avoid_busy_time(monkeypatch):
    monkeypatch.setattr(sched, "forecast_for_run", lambda *a, **k: {"available": False})
    day = date.today() + timedelta(days=1)
    busy, _ = sched.busy_times(_ics(day), day, 1)
    slots = sched.best_slots(41.31, -72.93, 45, day, 1, busy, top=50)
    assert slots
    for s in slots:
        for b0, b1 in busy:
            assert s["end"] + timedelta(minutes=sched.BUFFER_MIN) <= b0 or s["start"] - timedelta(minutes=sched.BUFFER_MIN) >= b1


def test_typical_traffic():
    assert sched.typical_traffic(datetime(2026, 10, 26, 8, 0)) == "high"      # Monday rush hour
    assert sched.typical_traffic(datetime(2026, 10, 26, 14, 0)) == "low"
    assert sched.typical_traffic(datetime(2026, 10, 25, 8, 0)) == "low"       # Sunday morning


def test_scoring_prefers_good_weather_and_penalizes_storms():
    nice = {"available": True, "feels_like_f": 52, "rain_chance": 0, "wind_mph": 4, "heat_slowdown": 0,
            "uv_index": 2, "warnings": []}
    storm = dict(nice, rain_chance=90, warnings=["Thunderstorms are forecast"])
    assert sched.score_slot(nice, "low", "daylight")[0] > 95
    assert sched.score_slot(storm, "low", "daylight")[0] == 0
    assert sched.score_slot(nice, "high", "daylight")[0] < sched.score_slot(nice, "low", "daylight")[0]


def test_model_never_sees_event_titles(monkeypatch):
    monkeypatch.setattr(sched, "forecast_for_run", lambda *a, **k: {"available": False})
    day = date.today() + timedelta(days=1)
    busy, _ = sched.busy_times(_ics(day), day, 1)
    briefs = [sched.slot_brief(s) for s in sched.best_slots(41.31, -72.93, 45, day, 1, busy)]
    assert "Secret meeting title" not in str(briefs)
