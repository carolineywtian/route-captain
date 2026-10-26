"""Hand the route to devices that give directions while running.

GPX: load onto a watch (Garmin, Coros, Apple Watch apps) for turn alerts on the run.
Google Maps: opens on a phone with spoken walking directions. Google picks its own
path between the waypoints, so the link is an approximation of the planned route.
"""
from __future__ import annotations

from urllib.parse import urlencode
from xml.sax.saxutils import escape

MAX_WAYPOINTS = 9  # Google Maps URLs accept at most 9 intermediate stops


def to_gpx(coords: list[tuple[float, float]], name: str, cues: list[dict] | None = None) -> str:
    points = "\n".join(f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}"/>' for lat, lon in coords)
    desc = "\n".join(f"Mile {c['mile']}: {c['text']}" for c in cues or [])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Route Captain" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>{escape(name)}</name><desc>{escape(desc)}</desc></metadata>
  <trk>
    <name>{escape(name)}</name>
    <trkseg>
{points}
    </trkseg>
  </trk>
</gpx>
"""


def google_maps_url(coords: list[tuple[float, float]], waypoints: int = MAX_WAYPOINTS,
                    decimals: int = 6) -> str:
    """Walking directions from start to finish through evenly spaced points on the route.
    Fewer waypoints and decimals give a shorter link, which makes a QR code easier to scan."""
    def fmt(p):
        return f"{p[0]:.{decimals}f},{p[1]:.{decimals}f}"
    inner = coords[1:-1]
    step = max(1, len(inner) // (waypoints + 1))
    waypoints = inner[step::step][:waypoints]
    params = {"api": 1, "origin": fmt(coords[0]), "destination": fmt(coords[-1]),
              "travelmode": "walking", "waypoints": "|".join(fmt(p) for p in waypoints)}
    return "https://www.google.com/maps/dir/?" + urlencode(params)


# ---------------------------------------------------------------- send to any phone

def qr_data_uri(url: str) -> str:
    """QR code (SVG, Yale navy) that opens `url` when scanned with any phone camera."""
    import base64
    import io

    import qrcode
    import qrcode.image.svg

    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode().replace('fill="#000000"', 'fill="#00356B"')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def _finish_label(plan: dict) -> str:
    if plan.get("finish"):
        return plan["finish"]["label"]
    if plan.get("stop") and plan.get("ends_at_stop"):
        return plan["stop"]["name"]
    return plan["start"]["label"]


def plan_text(plan: dict, when_text: str) -> str:
    """Plain-text version of a plan, used for the email and the calendar event."""
    b = plan["briefing"]
    lines = [b["headline"], "",
             f"{plan['start']['label']} -> {_finish_label(plan)}",
             f"{plan['miles']:.2f} mi, safety {plan['safety']['score']}/100, {when_text}", "",
             b["summary"], "", "Key moments:"]
    lines += [f"- {k}" for k in b["key_moments"]]
    lines += ["", "Safety notes:"] + [f"- {n}" for n in b["safety_notes"]]
    wear = (plan.get("weather_advice") or {}).get("what_to_wear") or []
    if wear:
        lines += ["", "What to wear:"] + [f"- {x}" for x in wear]
    tips = (plan.get("pacing_plan") or {}).get("hill_tips") or []
    if tips:
        lines += ["", "Pacing:"] + [f"- {t}" for t in tips]
    lines += ["", "Directions on your phone (Google Maps):", plan["gmaps"], "", "Planned with Route Captain."]
    return "\n".join(lines)


def mailto_url(plan: dict, when_text: str) -> str:
    """Opens the user's own mail app with the plan filled in; the user sends it themselves."""
    from urllib.parse import quote
    subject = (plan.get("calendar") or {}).get("title") or "Run plan: " + plan["briefing"]["headline"]
    return (f"mailto:?subject={quote(subject)}"
            f"&body={quote(plan_text(plan, when_text))}")


def to_ics(plan: dict, start, minutes: float, when_text: str) -> str:
    """Calendar event for the run (works with Apple, Google and Outlook calendars)."""
    from datetime import datetime, timedelta, timezone

    def esc(text: str) -> str:
        for raw, escaped in (("\\", "\\\\"), (";", "\\;"), (",", "\\,"), ("\n", "\\n")):
            text = text.replace(raw, escaped)
        return text

    def fold(line: str) -> str:
        """Calendar files wrap lines at 75 bytes (UTF-8); continuation lines start with a space."""
        parts, current = [], ""
        for ch in line:
            limit = 75 if not parts else 74  # continuation lines lose one byte to the leading space
            if len((current + ch).encode()) > limit:
                parts.append(current)
                current = ""
            current += ch
        parts.append(current)
        return "\r\n ".join(parts)

    from zoneinfo import ZoneInfo

    cal = plan.get("calendar") or {}
    local = start.replace(tzinfo=ZoneInfo(plan.get("tz") or "America/New_York")) if start.tzinfo is None else start
    begin_utc = local.astimezone(timezone.utc)  # UTC, so phones in any time zone show the right time
    end_utc = begin_utc + timedelta(minutes=minutes)
    fmt = "%Y%m%dT%H%M%S"
    return "\r\n".join(fold(line) for line in [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Route Captain//EN", "BEGIN:VEVENT",
        f"UID:{begin_utc.strftime(fmt)}-{abs(hash(plan['gmaps'])) % 10**8}@routecaptain",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}Z",
        f"DTSTART:{begin_utc.strftime(fmt)}Z", f"DTEND:{end_utc.strftime(fmt)}Z",
        f"SUMMARY:{esc(cal.get('title') or 'Run: ' + plan['briefing']['headline'])}",
        f"LOCATION:{esc(plan['start']['label'])}",
        f"DESCRIPTION:{esc(outlook_event_text(plan, when_text) if cal else plan_text(plan, when_text))}",
        f"URL:{plan['gmaps']}",
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(cal.get('title') or 'Run')}",
        f"TRIGGER:-PT{int(cal.get('reminder_minutes', 30))}M", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", ""])


# ---------------------------------------------------------------- Outlook

OUTLOOK_HOSTS = {"work": "https://outlook.office.com", "personal": "https://outlook.live.com"}


def outlook_event_text(plan: dict, when_text: str) -> str:
    """Short event notes (calendar links have a length limit). Uses the Calendar agent's notes
    when present, otherwise builds them from the plan; always ends with the phone link."""
    cal = plan.get("calendar")
    if cal:
        return (cal["notes"] + "\n\nDirections on your phone: " + (plan.get("gmaps_qr") or plan["gmaps"])
                + "\nPlanned with Route Captain")[:1400]
    b, prof, w = plan["briefing"], plan.get("profile") or {}, plan.get("weather") or {}
    lines = [b["headline"], f"{plan['start']['label']} -> {_finish_label(plan)} · {when_text}", ""]
    if w.get("available"):
        lines.append(f"Weather: {w['temp_f']}°F, {w['summary']} (feels {w['feels_like_f']}°F)")
    wear = b.get("what_to_wear") or w.get("what_to_wear") or []
    if wear:
        lines.append("Wear: " + "; ".join(wear[:4]))
    for h in (prof.get("hills") or [])[:2]:
        lines.append(f"Hill: miles {h['start']:.1f}-{h['end']:.1f}, +{h['gain_ft']} ft, aim {h['pace_str']}/mi")
    lines += ["", "Directions on your phone: " + (plan.get("gmaps_qr") or plan["gmaps"]), "Planned with Route Captain"]
    return "\n".join(lines)[:1400]


def outlook_url(plan: dict, start, minutes: float, when_text: str, account: str = "work") -> str:
    """Opens Outlook on the web with the run filled in as a new event; the user clicks Save.

    "work" is Microsoft 365 (school and work accounts, e.g. Yale); "personal" is Outlook.com.
    """
    from datetime import timedelta
    from urllib.parse import urlencode
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(plan.get("tz") or "America/New_York")
    begin = start.replace(tzinfo=tz) if start.tzinfo is None else start
    end = begin + timedelta(minutes=round(minutes))
    params = {
        "path": "/calendar/action/compose", "rru": "addevent",
        "subject": (plan.get("calendar") or {}).get("title") or "Run: " + plan["briefing"]["headline"],
        "startdt": begin.isoformat(timespec="seconds"), "enddt": end.isoformat(timespec="seconds"),
        "location": plan["start"]["label"],
        "body": outlook_event_text(plan, when_text),
    }
    return f"{OUTLOOK_HOSTS[account]}/calendar/0/deeplink/compose?" + urlencode(params)
