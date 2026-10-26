"""Weather, clothing and hill-pacing rules. No API key or network needed."""
import pytest

from route_captain.elevation import fmt_pace, grade_factor
from route_captain.weather import heat_slowdown, what_to_wear


def test_flat_is_unchanged():
    assert grade_factor(0.0) == pytest.approx(1.0)


def test_uphill_slower_and_steeper_is_slower_still():
    assert 1.10 < grade_factor(0.05) < 1.20      # about +15% at a 5% climb
    assert grade_factor(0.10) > grade_factor(0.05)


def test_downhill_speedup_is_capped():
    assert grade_factor(-0.03) < 1.0
    assert grade_factor(-0.15) >= 0.90           # never more than 10% faster


def test_fmt_pace():
    assert fmt_pace(9.5) == "9:30"
    assert fmt_pace(10.0) == "10:00"


@pytest.mark.parametrize("temp,dew,expected", [(50, 40, 0.0), (75, 60, 0.03), (85, 72, 0.06), (95, 90, 0.12)])
def test_heat_slowdown(temp, dew, expected):
    assert heat_slowdown(temp, dew) == expected


def test_cold_outfit_has_layers_and_gloves():
    items = " ".join(what_to_wear(15)).lower()
    assert "thermal" in items and "gloves" in items


def test_warm_outfit_is_light():
    items = " ".join(what_to_wear(65)).lower()
    assert "shorts" in items and "jacket" not in items


def test_adjustments_for_wind_rain_sun_and_dark():
    items = " ".join(what_to_wear(45, wind_mph=15, rain_chance=70, uv=7, dark=True)).lower()
    assert "windbreaker" in items and "shell" in items and "reflective" in items
    assert "sunscreen" not in items              # no sunscreen advice in the dark


def test_outlook_link_has_event_details():
    from datetime import datetime
    from urllib.parse import parse_qs, urlparse

    from route_captain.export import outlook_url
    plan = {"briefing": {"headline": "5 mi loop", "what_to_wear": ["Shorts"]}, "start": {"label": "Cross Campus"},
            "miles": 5.0, "gmaps": "https://www.google.com/maps/dir/?api=1"}
    url = outlook_url(plan, datetime(2026, 12, 5, 7, 30), 48, "Sat Dec 5")
    q = parse_qs(urlparse(url).query)
    assert urlparse(url).netloc == "outlook.office.com"
    assert q["subject"][0] == "Run: 5 mi loop"
    assert q["startdt"][0] == "2026-12-05T07:30:00-05:00"   # standard time in December
    assert q["enddt"][0] == "2026-12-05T08:18:00-05:00"
    assert "Shorts" in q["body"][0]
    assert "outlook.live.com" in outlook_url(plan, datetime(2026, 12, 5, 7, 30), 48, "", account="personal")


def test_calendar_agent_event_reaches_outlook_and_ics():
    from datetime import datetime
    from urllib.parse import parse_qs, urlparse

    from route_captain.export import outlook_url, to_ics
    plan = {"briefing": {"headline": "5 mi loop", "summary": "s", "key_moments": [], "safety_notes": []},
            "start": {"label": "Cross Campus"}, "miles": 5.0, "safety": {"score": 90},
            "gmaps": "https://www.google.com/maps/dir/?api=1",
            "calendar": {"title": "Run: 5 mi to the bakery", "notes": "Wear shorts.", "reminder_minutes": 60}}
    q = parse_qs(urlparse(outlook_url(plan, datetime(2026, 10, 24, 9), 48, "Sat")).query)
    assert q["subject"][0] == "Run: 5 mi to the bakery"
    assert q["body"][0].startswith("Wear shorts.")
    ics = to_ics(plan, datetime(2026, 10, 24, 9), 48, "Sat").replace("\r\n ", "")
    assert "SUMMARY:Run: 5 mi to the bakery" in ics
    assert "TRIGGER:-PT60M" in ics and "BEGIN:VALARM" in ics
