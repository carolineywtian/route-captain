"""Best time to run: free slots from the runner's calendar, scored on weather, daylight
and typical traffic (code tools; the Scheduler agent picks among the top slots).

Privacy: the calendar is parsed here, inside the app. Event titles and details never
reach a model; only free time slots and their scores do.
Traffic: no free live traffic source is used. Scores use typical New Haven patterns
(weekday rush hours busiest), and the app labels them as typical, not live.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

from .safety import NEW_HAVEN
from .weather import forecast_for_run
from .timefmt import fmt

TZ = ZoneInfo("America/New_York")
DAY_START, DAY_END = time(6, 0), time(20, 30)
STEP_MIN = 30
BUFFER_MIN = 15          # time to change before and after the run
MAX_ICS_BYTES = 5_000_000


# ---------------------------------------------------------------- calendar

def fetch_ics(url: str) -> bytes:
    """Download a published calendar link (Outlook 'Publish a calendar' ICS link, webcal:// ok)."""
    url = url.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.startswith("https://"):
        raise ValueError("Paste the calendar's https:// (or webcal://) ICS link.")
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "RouteCaptain/0.1"})
    except requests.RequestException:
        raise ValueError("Couldn't reach that calendar link. Check your connection or the link.") from None
    if resp.status_code in (401, 403):
        raise ValueError("That calendar link is private. Publish it with “Can view when I'm busy”, "
                         "or upload the .ics file instead.")
    if not resp.ok:
        raise ValueError(f"That link didn't work (error {resp.status_code}). Check it, or upload the .ics file instead.")
    if len(resp.content) > MAX_ICS_BYTES or not is_calendar(resp.content):
        raise ValueError("That link didn't return a calendar (.ics) file.")
    return resp.content


def is_calendar(data: bytes) -> bool:
    """True if the bytes look like an iCalendar (.ics) file."""
    return b"BEGIN:VCALENDAR" in data[:4000].upper()


def busy_times(ics: bytes, start: date, days: int, tz: ZoneInfo = TZ) -> tuple[list[tuple[datetime, datetime]], int]:
    """(busy intervals in New Haven time, number of events) between start and start + days.

    Recurring events are expanded. Events marked free/transparent, cancelled events and
    all-day events (holidays, reminders) don't block time.
    """
    import icalendar
    import recurring_ical_events

    if not is_calendar(ics):
        raise ValueError("That file isn't a calendar (.ics) file.")
    try:
        cal = icalendar.Calendar.from_ical(ics)
    except ValueError:
        raise ValueError("That calendar file couldn't be read. Try exporting it again as .ics.") from None
    window_start = datetime.combine(start, time(0), tz)
    window_end = window_start + timedelta(days=days)
    busy = []
    for event in recurring_ical_events.of(cal).between(window_start, window_end):
        if str(event.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        dtstart, dtend = event.get("DTSTART"), event.get("DTEND")
        if dtstart is None or not isinstance(dtstart.dt, datetime):
            continue  # all-day event
        s = dtstart.dt if dtstart.dt.tzinfo else dtstart.dt.replace(tzinfo=tz)
        e = (dtend.dt if dtend is not None else s + event.get("DURATION").dt) if (dtend or event.get("DURATION")) else s
        e = e if e.tzinfo else e.replace(tzinfo=tz)
        busy.append((s.astimezone(tz), e.astimezone(tz)))
    busy.sort()
    return busy, len(busy)


# ---------------------------------------------------------------- scoring

def typical_traffic(when: datetime) -> str:
    """'high' / 'moderate' / 'low' from typical patterns (not live data)."""
    h = when.hour + when.minute / 60
    if when.weekday() < 5:
        if 7 <= h < 9.5 or 16 <= h < 18.5:
            return "high"
        if 11.5 <= h < 13.5:
            return "moderate"
        return "low"
    return "moderate" if 11 <= h < 17 else "low"


def _daylight(start: datetime, end: datetime, lat: float | None = None, lon: float | None = None) -> str:
    from astral import LocationInfo
    from astral.sun import sun
    observer = (LocationInfo("run", "", str(start.tzinfo), lat, lon).observer if lat is not None
                else NEW_HAVEN.observer)
    s = sun(observer, date=start.date(), tzinfo=start.tzinfo)
    if start < s["dawn"] or end > s["dusk"]:
        return "dark"
    if start < s["sunrise"] or end > s["sunset"]:
        return "twilight"
    return "daylight"


def score_slot(weather: dict, traffic: str, daylight: str) -> tuple[int, list[str]]:
    """0-100 (higher is better) and the reasons that cost points."""
    score, why = 100.0, []
    if weather.get("available"):
        feels = weather["feels_like_f"]
        if feels < 45:
            score -= min(35, (45 - feels) * 1.2); why.append(f"chilly (feels {feels}°F)")
        elif feels > 62:
            score -= min(45, (feels - 62) * 1.6); why.append(f"warm (feels {feels}°F)")
        if weather["rain_chance"] >= 20:
            score -= weather["rain_chance"] * 0.4; why.append(f"{weather['rain_chance']}% rain")
        if weather["wind_mph"] > 10:
            score -= (weather["wind_mph"] - 10) * 1.5; why.append(f"windy ({weather['wind_mph']} mph)")
        if weather["heat_slowdown"]:
            score -= weather["heat_slowdown"] * 300; why.append(f"humid (pace {weather['heat_slowdown']:.0%} slower)")
        if weather["uv_index"] >= 6 and daylight == "daylight":
            score -= 5; why.append(f"high UV ({weather['uv_index']})")
        if any("Thunderstorm" in w for w in weather["warnings"]):
            score -= 100; why.append("thunderstorms")
        score -= abs(feels - 52) * 0.15  # tie-breaker: closest to ideal running weather
    if daylight == "dark":
        score -= 30; why.append("dark")
    elif daylight == "twilight":
        score -= 8; why.append("near sunrise/sunset")
    if traffic == "high":
        score -= 15; why.append("rush hour (typical)")
    elif traffic == "moderate":
        score -= 6; why.append("moderate traffic (typical)")
    return max(0, round(score)), why


def best_slots(lat: float, lon: float, run_minutes: float, start_day: date, days: int,
               busy: list[tuple[datetime, datetime]] | None = None, top: int = 6, tz: ZoneInfo = TZ) -> list[dict]:
    """Free slots long enough for the run, scored, best first; at most one per 2-hour block."""
    busy = busy or []
    need = timedelta(minutes=run_minutes + BUFFER_MIN)
    now = datetime.now(tz)
    slots = []
    for d in range(days):
        day = start_day + timedelta(days=d)
        t = datetime.combine(day, DAY_START, tz)
        last = datetime.combine(day, DAY_END, tz)
        while t + need <= last:
            end = t + timedelta(minutes=run_minutes)
            free = t >= now + timedelta(minutes=30) and all(
                end + timedelta(minutes=BUFFER_MIN) <= b0 or t - timedelta(minutes=BUFFER_MIN) >= b1
                for b0, b1 in busy)
            if free:
                weather = forecast_for_run(lat, lon, t.replace(tzinfo=None), run_minutes)
                traffic, daylight = typical_traffic(t), _daylight(t, end, lat, lon)
                score, why = score_slot(weather, traffic, daylight)
                slots.append({"start": t, "end": end, "score": score, "why": why, "traffic": traffic,
                              "daylight": daylight, "weather": weather})
            t += timedelta(minutes=STEP_MIN)
    slots.sort(key=lambda s: -s["score"])
    chosen, blocks = [], set()
    for s in slots:
        block = (s["start"].date(), s["start"].hour // 2)
        if block in blocks:
            continue
        blocks.add(block)
        chosen.append(s)
        if len(chosen) >= top:
            break
    for i, s in enumerate(chosen):
        s["id"] = f"slot-{i + 1}"
    return chosen


def slot_brief(s: dict) -> dict:
    """What the Scheduler agent sees: no calendar details, just the slot and its scores."""
    w = s["weather"]
    return {"id": s["id"], "when": fmt(s["start"], "%a %b %-d, %-I:%M %p") + "–" + fmt(s["end"], "%-I:%M %p"),
            "score": s["score"], "drawbacks": s["why"], "typical_traffic": s["traffic"], "daylight": s["daylight"],
            "weather": ({k: w[k] for k in ("summary", "temp_f", "feels_like_f", "rain_chance", "wind_mph")}
                        if w.get("available") else "no forecast")}


# ---------------------------------------------------------------- fit a run before the next event

HILL_MARGIN = 1.05       # plan 5% slower than flat pace so hills don't make the runner late
MAX_FIT_GAP_MIN = 180    # with more free time than this, any distance fits


def fit_before_next_event(busy: list[tuple[datetime, datetime]], start: datetime, pace: float,
                          buffer_min: int = BUFFER_MIN) -> dict:
    """How far to run so the runner is back `buffer_min` before their next event.

    status: "fit" (miles set), "too_short" (not even a mile), or "free" (no event soon).
    If the start falls inside an event, the run starts when that event ends.
    """
    moved = False
    for b0, b1 in busy:
        if b0 <= start < b1:
            start, moved = b1, True
    start = start + timedelta(minutes=(-start.minute) % 5, seconds=-start.second, microseconds=-start.microsecond)
    nxt = next((b0 for b0, _ in busy if b0 >= start), None)
    out = {"start": start, "event": nxt, "moved": moved, "buffer_min": buffer_min, "miles": None}
    if nxt is None or nxt - start > timedelta(minutes=MAX_FIT_GAP_MIN):
        return {**out, "status": "free"}
    gap = (nxt - start).total_seconds() / 60
    miles = int((gap - buffer_min) / (pace * HILL_MARGIN) * 10) / 10
    out.update(gap_min=round(gap), back_by=start + timedelta(minutes=miles * pace * HILL_MARGIN))
    if miles < 1:
        return {**out, "status": "too_short"}
    return {**out, "status": "fit", "miles": min(miles, 26.2)}
