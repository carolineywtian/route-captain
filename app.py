"""Route Captain: personal run planner (Dash).

Run:  .venv/bin/python app.py   then open http://127.0.0.1:8050
Debug mode and the auto-reloader are off so one edit never spawns a second server.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):  # say so plainly instead of failing on an import later
    sys.exit(f"Route Captain needs Python 3.11 or newer (this is {sys.version.split()[0]}). "
             "Install a newer Python from python.org and create the virtual environment again.")

import html as html_text
import json
import math
from pathlib import Path
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta

import dash_leaflet as dl
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update

from route_captain.agents import find_best_time, plan_personal_run
from route_captain.club import parse_paces
from route_captain.budget import GUARD
from route_captain.llm import KEY_MISSING, has_key
from route_captain.export import google_maps_url, mailto_url, outlook_url, qr_data_uri, to_gpx, to_ics
from route_captain.geocode import CATEGORY_ICONS, LANDMARKS, LocationError, locate, suggest
from route_captain.mapdata import haversine_m, load_graph
from route_captain.places import ATTACK_PLACES, load_places
from route_captain.timefmt import fmt

NAVY = "#00356B"
BLUE = "#286DC0"
LIGHT_BLUE = "#63AAFF"
PALE = "#BDD5EA"
TERRAIN = {  # route color by terrain: harder = darker
    "climb": ("#00356B", 7, "Climb"), "gentle climb": ("#00356B", 6, "Climb"),
    "flat": ("#286DC0", 5, "Flat"),
    "gentle descent": ("#63AAFF", 5, "Downhill"), "descent": ("#63AAFF", 5, "Downhill"),
}
HOME = LANDMARKS["Cross Campus"]
# Light basemap so the navy route stands out. Stadia Maps serves it without an API key on
# localhost (fine for running and grading locally); a public deployment would need a free key.
TILES = "https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}{r}.png"
TILE_ATTRIBUTION = ('&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> '
                    '&copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> '
                    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors')
DISTANCES = [("3 mi", 3), ("5K", 3.1), ("5 mi", 5), ("10K", 6.2), ("Half", 13.1)]
STOPS = [("none", "None", "✕"), ("bakery", "Bakery", "🥐"), ("cafe", "Coffee", "☕"), ("restaurant", "Food", "🍽"),
         ("park", "Park", "🌳"), ("water", "Water", "💧"), ("restroom", "Restroom", "🚻")]
STOP_WORDS = {"bakery": "a bakery", "cafe": "a coffee shop", "restaurant": "a restaurant", "park": "a park",
              "water": "a water fountain", "restroom": "a restroom"}
LOGO = ("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        "<circle cx='7' cy='25' r='4' fill='%2363AAFF'/><circle cx='25' cy='7' r='4' fill='white'/>"
        "<path d='M7 25 C 7 14, 25 18, 25 7' stroke='white' stroke-width='2.5' fill='none' "
        "stroke-dasharray='3 3' stroke-linecap='round'/></svg>")

load_graph()  # warm the map cache before the first request

app = Dash(__name__, title="Route Captain", suppress_callback_exceptions=True)


# ------------------------------------------------------------------ map helpers

def _fit(bounds, width_px=560, height_px=520):
    """Center and zoom that fit `bounds` in a map of roughly this size (web-mercator math).
    Computed here because the map component mis-handles its own `bounds` property."""
    (s, w), (n, e) = bounds
    def merc(lat):
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    lon_zoom = math.log2(360 * width_px / (256 * max(e - w, 1e-6)))
    lat_zoom = math.log2(2 * math.pi * height_px / (256 * max(merc(n) - merc(s), 1e-9)))
    zoom = max(12, min(17, math.floor(min(lon_zoom, lat_zoom))))
    return ((s + n) / 2, (w + e) / 2), zoom


def _map(layers, center=None, bounds=None):
    """A fresh map per plan, already centered and zoomed on the route."""
    center, zoom = _fit(bounds) if bounds else (center, 14)
    # A new wrapper id makes React mount a brand-new Leaflet map instead of moving the old one,
    # which this map component does not redraw reliably.
    return html.Div(id=f"mapbox-{uuid.uuid4().hex[:8]}", style={"height": "100%"}, children=dl.Map(
        id="map", center=center, zoom=zoom, style={"height": "100%"},
        zoomAnimation=False, fadeAnimation=False, markerZoomAnimation=False,
        children=[dl.TileLayer(url=TILES, attribution=TILE_ATTRIBUTION, maxZoom=19), *layers]))


def _pin(lat, lon, kind: str, text: str, icon: str = ""):
    """A labeled map pin. Text is escaped: place names come from an editable public map."""
    safe = html_text.escape(text)
    return dl.DivMarker(position=(lat, lon), iconOptions={
        "className": f"pin-wrap {kind}",
        "html": f"<div class='pin {kind}'>{icon}<span>{safe}</span></div>",
        "iconSize": [0, 0], "iconAnchor": [0, 0]})


def _point(lat, lon, label, fill, radius=9, permanent=False):
    return dl.CircleMarker(center=(lat, lon), radius=radius, color="white", weight=3, fillColor=fill,
                           fillOpacity=1, children=dl.Tooltip(label, permanent=permanent, direction="top",
                                                              offset=[0, -8], className="map-label"))


def _next_saturday_9am() -> str:
    """The next Saturday 9:00 AM that's still ahead (today, if it's Saturday morning)."""
    now = datetime.now()
    run = (now + timedelta(days=(5 - now.weekday()) % 7)).replace(hour=9, minute=0, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=7)
    return run.strftime("%Y-%m-%dT%H:%M")


# ------------------------------------------------------------------ layout

QUESTIONS = ["start", "distance", "stop", "finish", "when", "extras"]


FINISH_CARDS = [("loop", "⟲", "Loop back", "A different way home"),
                ("out_and_back", "⇄", "Out & back", "Same way there and back"),
                ("at_stop", "🏁", "End at my stop", "Finish at your bakery, coffee, food or park stop"),
                ("place", "📍", "End at a place", "Type an address or drop a pin"),
                ("surprise", "✨", "Surprise me", "Find a cool destination for my interests")]


def _finish_options() -> list[dict]:
    """The finish choices; CSS hides 'End at my stop' until a main stop is picked."""
    return [{"label": html.Span(className="card-opt", children=[html.Span(icon, className="card-icon"),
                                                                html.Strong(title), html.Span(text)]),
             "value": value}
            for value, icon, title, text in FINISH_CARDS]


def _question(key, number, title, helper, *controls):
    """One Typeform-style screen: a big question, a helper line and its controls."""
    return html.Div(id=f"q-{key}", className="q", children=[
        html.Div(className="q-num", children=[html.Span(str(number)), " →"]),
        html.H2(title, className="q-title"),
        html.P(helper, className="q-help") if helper else None,
        html.Div(className="q-body", children=list(controls)),
    ])


app.layout = html.Div(className="page", children=[
    html.Header(className="topbar", children=[
        html.Div(className="brand-wrap", children=[
            html.Img(src=LOGO, className="logo", alt=""),
            html.Div([html.Div("Route Captain", className="brand"),
                      html.Div("Safe runs at the right distance, with a bakery at the end if you want one.",
                               className="tagline")]),
        ]),
        html.Div("United States", className="badge"),
    ]),

    # ---------- welcome
    html.Div(id="welcome", className="welcome-screen", children=[
        html.Div(className="welcome-inner", children=[
            html.Div(className="welcome-copy", children=[
                html.Span("AI run planner · anywhere in the US", className="welcome-eyebrow"),
                html.H1(["Your run, planned in ", html.Span("30 seconds.", className="accent")]),
                html.P("A safe route at your distance, dressed for the weather and paced for the hills.",
                       className="welcome-lead"),
                html.Div(className="welcome-cta", children=[
                    html.Button("Plan my run →", id="start-btn", className="nav-next plan", n_clicks=0),
                    html.Button("👟 Plan a run club run", id="club-start-btn", className="club-cta", n_clicks=0),
                ]),
                html.Div(className="enter-hint", children=["press ", html.Strong("Enter ↵"), " for a solo run"]),
                html.Ul(className="welcome-perks", children=[
                    html.Li([html.Span("👟", className="perk-icon"), "Run clubs: every pace, one meetup"]),
                    html.Li([html.Span("⏱", className="perk-icon"), "Fits before your next meeting"]),
                    html.Li([html.Span("🌤", className="perk-icon"), "What to wear"]),
                    html.Li([html.Span("⛰", className="perk-icon"), "Hill pacing"]),
                    html.Li([html.Span("🥐", className="perk-icon"), "Stops that are open"]),
                ]),
                html.Div("6 quick questions · about a minute", className="welcome-foot"),
            ]),
            html.Div(className="welcome-art", children=[
                html.Img(src="/assets/welcome_route.svg", className="welcome-map", alt=""),
                html.Div(className="art-chip chip-a", children=[html.Strong("5.1 mi"), " to the waterfront"]),
                html.Div(className="art-chip chip-b", children=["🌤 51°F · ", html.Strong("long sleeves")]),
                html.Div(className="art-chip chip-c", children=["⛰ ", html.Strong("390 ft"), " climb"]),
                html.Img(src="/assets/mascot.svg", className="welcome-mascot", alt="Route Captain's sneaker mascot"),
            ]),
        ]),
    ]),

    # ---------- the questions, one per screen
    html.Div(id="wizard", className="wizard", children=[
        html.Div(className="progress", children=html.Div(id="progress-fill", className="progress-fill")),
        html.Button(id="mode-pill", className="mode-pill", n_clicks=0, title="Switch between a solo run and a run club"),
        html.Div(className="wizard-inner", children=[
            _question("start", 1, "Where are you starting?",
                      "Any address or place in the United States. Outside New Haven? Include the city, "
                      "like “100 Congress Ave, Austin TX”.",
                      html.Div(className="field-wrap big", children=[
                          html.Label("Start location", htmlFor="start-text", className="sr-only"),
                          dcc.Input(id="start-text", type="text", value="Cross Campus", autoComplete="off",
                                    placeholder="e.g. 24 Clark St", className="big-input"),
                          html.Div(id="start-suggest", className="suggest-box"),
                      ]),
                      html.Div(className="quick-picks", children=[
                          html.Button("📍 Use my current location", id="gps-start", className="chip gps-chip",
                                      n_clicks=0),
                          *[html.Button(name, id={"type": "pick-start", "i": i}, className="chip", n_clicks=0)
                            for i, name in enumerate(list(LANDMARKS)[:6])]]),
                      html.Div(id="gps-status", className="hint"),
                      html.Div(id="start-error", className="field-error", role="alert")),
            _question("distance", 2, "How far do you want to run?", None,
                      html.Div(className="dist-row big", children=[
                          html.Div(className="dist-box", children=[
                              dcc.Input(id="distance", type="number", min=1, max=26.2, step=0.1, value=5,
                                        className="dist-input"),
                              html.Span("miles", className="dist-unit"),
                          ]),
                          html.Div(className="dist-chips", children=[
                              html.Button(label, id={"type": "dist", "i": i}, className="chip", n_clicks=0)
                              for i, (label, _) in enumerate(DISTANCES)]),
                      ]),
                      html.Div(id="distance-error", className="field-error", role="alert")),
            _question("stop", 3, "Want any stops along the way?",
                      "Pick as many as you like. Water and restrooms are passed on the way; places are checked "
                      "for opening hours when you arrive.",
                      dcc.Checklist(id="stop", className="stop-picker big", value=["none"], options=[
                          {"label": html.Span(className="stop-opt", children=[html.Span(icon, className="stop-icon"),
                                                                               html.Span(label)]),
                           "value": value} for value, label, icon in STOPS])),
            _question("finish", 4, "How do you want to finish?",
                      "Come back home, or finish somewhere worth running to.",
                      dcc.RadioItems(id="finish-mode", className="choice-cards finish-cards no-main-stop", value="loop",
                                     options=_finish_options()),
                      html.Div(id="finish-place-wrap", className="field-wrap big", style={"display": "none"}, children=[
                          html.Label("Finish location", htmlFor="finish-text", className="sr-only"),
                          dcc.Input(id="finish-text", type="text", value="", autoComplete="off",
                                    placeholder="Where do you want to finish? e.g. East Rock Summit",
                                    className="big-input"),
                          html.Div(id="finish-suggest", className="suggest-box"),
                      ]),
                      html.Div(id="finish-error", className="field-error", role="alert"),
                      html.Div(id="interests-wrap", style={"display": "none"}, children=[
                          html.Label("What are you into?", htmlFor="interests", className="mini-label"),
                          dcc.Input(id="interests", type="text", value="", autoComplete="off", className="big-input",
                                    placeholder="e.g. views, street art, history, ice cream"),
                      ]),
                      html.Div(id="finish-note", className="hint")),
            _question("when", 5, "When are you running?", "Used for the weather, daylight and opening hours.",
                      dcc.RadioItems(id="run-mode", className="segmented run-mode", value="solo", inline=True,
                                     options=[{"label": "🏃 Solo run", "value": "solo"},
                                              {"label": "👟 Run club", "value": "club"}]),
                      html.Div(className="row big", children=[
                          html.Div(className="field", children=[
                              html.Label("Day & time", htmlFor="when"),
                              dcc.Input(id="when", type="datetime-local", value=_next_saturday_9am())]),
                          html.Div(id="solo-pace-wrap", className="field", children=[
                              html.Label("Easy flat pace (min / mile)", htmlFor="pace"),
                              dcc.Input(id="pace", type="text", value="9:30")]),
                          html.Div(id="club-pace-wrap", className="field", style={"display": "none"}, children=[
                              html.Label("Pace groups, fastest to slowest", htmlFor="club-paces"),
                              dcc.Input(id="club-paces", type="text", value="8:00, 9:30, 11:00")]),
                      ]),
                      html.Div(id="club-note", className="hint"),
                      html.Div(id="when-error", className="field-error", role="alert"),
                      html.Div(className="fit-box", children=[
                          html.Button("⏱ Fit a run before my next event", id="fit-btn", className="fit-btn", n_clicks=0),
                          html.Span("Uses the calendar below. Starts at the Day & time above (or now, if that's "
                                    "passed) and sets the distance so you're back with 15 min to change.",
                                    className="hint"),
                          html.Div(id="fit-status", className="hint"),
                      ]),
                      html.Div(className="best-time", children=[
                          html.Div("✨ Not sure when? Find your best time", className="bt-title"),
                          html.P("Scores every free slot on the forecast, daylight and typical traffic. Add your "
                                 "Outlook calendar to only see times you're free. It's read inside the app; event "
                                 "titles are never sent to the AI.", className="hint"),
                          html.Div(className="bt-row", children=[
                              dcc.Upload(id="ics-upload", accept=".ics,text/calendar", className="upload-btn",
                                         children=html.Span("📅 Upload calendar (.ics)")),
                              dcc.Input(id="ics-url", type="text", className="text-input",
                                        placeholder="…or paste an ICS link"),
                          ]),
                          html.Div(id="ics-status", className="hint"),
                          html.Div(className="bt-row", children=[
                              dcc.RadioItems(id="bt-days", className="segmented small", value=3, inline=True,
                                             options=[{"label": "Today", "value": 1},
                                                      {"label": "Next 3 days", "value": 3},
                                                      {"label": "This week", "value": 7}]),
                              html.Button("Find my best time", id="bt-btn", className="nav-next small", n_clicks=0),
                          ]),
                          html.Div(className="bt-links", children=[
                              html.Button("Try a sample calendar", id="bt-sample", className="link-btn", n_clicks=0),
                              html.Details(className="bt-help", children=[
                                  html.Summary("How do I get my Outlook calendar?"),
                                  html.Ol([
                                      html.Li("Outlook on the web: Settings → Calendar → Shared calendars."),
                                      html.Li("Under “Publish a calendar”, pick your calendar and “Can view when I'm busy”, "
                                              "then Publish and copy the ICS link."),
                                      html.Li("Or in Outlook for Mac/Windows, save or export your calendar as an .ics file "
                                              "and upload it."),
                                      html.Li("If your school account doesn't allow publishing, use the file option."),
                                  ]),
                              ]),
                          ]),
                          dcc.Loading(type="dot", color=NAVY, children=html.Div(id="bt-results")),
                      ])),
            _question("extras", 6, "Anything else?", "Optional. Then press Plan my run.",
                      html.Div(className="answer-summary", children=[
                          html.Span("Your answers (click one to change it)", className="mini-label"),
                          html.Div(id="answer-chips", className="answer-chips"),
                      ]),
                      html.Label("Avoid streets", htmlFor="avoid", className="mini-label"),
                      dcc.Input(id="avoid", type="text", placeholder="e.g. Whalley Ave, Route 34",
                                className="text-input"),
                      html.Label("Notes for the planning agents", htmlFor="request", className="mini-label"),
                      dcc.Textarea(id="request", rows=2, className="text-input",
                                   placeholder="e.g. easy day, prefer parks"),
                      dcc.Checklist(id="attack-demo", className="demo-toggle",
                                    options=[{"label": "Safety demo: add planted malicious places", "value": "on"}],
                                    value=[])),
            html.Div(id="found-line", className="found"),
            html.Div(className="wizard-nav", children=[
                html.Button("← Back", id="back-btn", className="nav-back", n_clicks=0),
                html.Button("OK ✓", id="next-btn", className="nav-next", n_clicks=0),
                html.Button("Plan my run ✨", id="send", className="nav-next plan", n_clicks=0),
                html.Span(className="enter-hint", children=["press ", html.Strong("Enter ↵")]),
            ]),
        ]),
    ]),

    # ---------- planning screen
    html.Div(className="planning-screen", children=[
        html.Div(className="runner-scene", children=[
            html.Img(src=app.get_asset_url("runner.svg"), className="runner", alt=""),
            html.Div(className="runner-road"),
        ]),
        html.H2("Planning your run…"),
        html.Div(id="planning-feed", className="planning-feed", **{"aria-live": "polite"}),
        dcc.Interval(id="progress-tick", interval=700, disabled=True),
    ]),

    # ---------- results: plan + map
    html.Div(id="results", className="results", children=[
        html.Aside(className="side", children=[
            html.Div(className="results-bar", children=[
                html.Button("← Edit answers", id="edit-btn", className="nav-back", n_clicks=0),
                html.Button("＋ Plan another run", id="new-btn", className="nav-back", n_clicks=0),
            ]),
            html.Button("▶ Start run", id="start-run-btn", className="start-run", n_clicks=0),
            html.Div(id="history", className="history"),
            dcc.Loading(type="dot", color=NAVY, children=html.Div(id="working", className="working")),
        ]),
        html.Section(className="mapwrap", children=[
            html.Div(className="map-card", children=[
                html.Div(id="mapwrap", style={"height": "100%"}),
                html.Div(id="map-overlay"),
                html.Div(className="map-toggle", children=[
                    html.Span("Map clicks", className="mt-label"),
                    dcc.RadioItems(id="click-target", className="segmented small", value="off", inline=True,
                                   options=[{"label": "Off", "value": "off"},
                                            {"label": "Move start", "value": "start"},
                                            {"label": "Move finish", "value": "finish"}]),
                ]),
                html.Button(id="replan-btn", className="replan-btn", n_clicks=0, style={"display": "none"}),
                html.Button("⌖ Back to my route", id="recenter-btn", className="recenter-btn", n_clicks=0,
                            title="Zoom the map back to your whole route"),
            ]),
        ]),
    ]),

    # ---------- run mode: live GPS guidance
    html.Div(id="run", className="run-screen", children=[
        html.Div(id="run-map", className="run-map"),
        html.Div(className="run-top", children=[
            html.Div(id="run-arrow", className="run-arrow", children="↑"),
            html.Div(className="run-next-wrap", children=[
                html.Div(id="run-next", className="run-next", children="Getting your location…"),
                html.Div(id="run-next-dist", className="run-next-dist"),
            ]),
        ]),
        html.Div(className="run-bottom", children=[
            html.Div(className="run-progress", children=html.Div(id="run-progress-fill", className="run-progress-fill")),
            html.Div(className="run-stats", children=[
                html.Div([html.Span("0.00", id="run-done", className="rs-num"), html.Span("miles done", className="rs-lbl")]),
                html.Div([html.Span("0:00", id="run-elapsed", className="rs-num"), html.Span("time", className="rs-lbl")]),
                html.Div([html.Span("–", id="run-pace", className="rs-num"), html.Span("avg pace", className="rs-lbl")]),
                html.Div([html.Span("–", id="run-left", className="rs-num"), html.Span("miles left", className="rs-lbl")]),
            ]),
            html.Div(id="run-status", className="run-status"),
            html.Div(className="run-buttons", children=[
                html.Button("⏸ Pause", id="run-pause", className="run-btn", n_clicks=0),
                html.Button("🔊 Voice on", id="run-voice", className="run-btn", n_clicks=0),
                html.Button("■ End run", id="run-end", className="run-btn end", n_clicks=0),
            ]),
            html.Button("Demo: simulate this run", id="run-sim", className="link-btn run-sim", n_clicks=0),
        ]),
    ]),
    dcc.Store(id="run-route"),

    dcc.Store(id="step", data=0),
    dcc.Store(id="ics-data"),     # uploaded calendar (base64 data URL) or "sample"
    dcc.Store(id="bt-slots", data=[]),
    dcc.Store(id="view", data="welcome"),
    dcc.Store(id="pins", data={}),
    dcc.Store(id="deadline"),
    dcc.Store(id="suggested", data={}),  # picked label -> [lat, lon]
    dcc.Store(id="start-items", data=[]),
    dcc.Store(id="finish-items", data=[]),
    dcc.Store(id="chat", data=[]),
    dcc.Store(id="progress-key", storage_type="session"),
    dcc.Download(id="gpx-download"),
    dcc.Download(id="ics-download"),
])


# ------------------------------------------------------------------ inputs

@app.callback(Output("start-text", "value"), Output("finish-text", "value"), Output("pins", "data"),
              Output("finish-mode", "value", allow_duplicate=True), Output("click-target", "value"),
              Input("map", "clickData"), State("click-target", "value"), State("pins", "data"),
              prevent_initial_call=True)
def drop_pin(click, target, pins):
    if not click or target not in ("start", "finish"):  # map clicks are off: a stray click does nothing
        return no_update, no_update, no_update, no_update, no_update
    lat, lon = click["latlng"]["lat"], click["latlng"]["lng"]
    text = f"{lat:.5f}, {lon:.5f}"
    pins = {**(pins or {}), target: {"label": "Dropped pin", "lat": lat, "lon": lon, "text": text}}
    # one pin per choice: clicks switch back off so the next stray click can't move it again
    if target == "start":
        return text, no_update, pins, no_update, "off"
    return no_update, text, pins, "place", "off"


def _new_pins(pins: dict | None, msg: dict | None, start_text: str | None = None,
              finish_text: str | None = None, finish_mode: str | None = None) -> dict:
    """Map-click pins that are still the runner's answer and differ from the plan on screen.

    A pin stops counting once the runner types or picks something else for that answer (or, for
    the finish, chooses a way to finish other than "End at a place"), and none count after a failed plan."""
    if not msg:
        return {}
    still_chosen = {"start": start_text, "finish": finish_text if finish_mode == "place" else None}
    pins = {k: p for k, p in (pins or {}).items() if p.get("text") and still_chosen.get(k) == p["text"]}
    plan = msg["plan"]
    # the plan's own start/finish (exactly where the pin was) plus where the route meets the streets
    ends = [(p["lat"], p["lon"]) for p in (plan.get("start"), plan.get("finish")) if p and "lat" in p]
    ends += [plan["coords"][0], plan["coords"][-1]] if plan.get("coords") else []
    return {key: pin for key, pin in (pins or {}).items()
            if key in ("start", "finish") and not any(haversine_m(pin["lat"], pin["lon"], *pt) < 60 for pt in ends)}


@app.callback(Output("start-run-btn", "style"), Input("chat", "data"))
def show_start_run(chat):
    """Start run needs a route: hide it after a failed plan."""
    return {} if _latest(chat) else {"display": "none"}


@app.callback(Output("replan-btn", "children"), Output("replan-btn", "style"),
              Input("pins", "data"), Input("chat", "data"), Input("start-text", "value"),
              Input("finish-text", "value"), Input("finish-mode", "value"))
def show_replan(pins, chat, start_text, finish_text, finish_mode):
    new = _new_pins(pins, _latest(chat), start_text, finish_text, finish_mode)
    if not new:
        return no_update, {"display": "none"}
    what = "new start and finish" if len(new) == 2 else f"new {next(iter(new))}"
    return f"Re-plan with this {what} →", {}


app.clientside_callback(  # re-plan = the same Plan button at the end of the questions, with the pin filled in
    """function(n) { if (n) { var b = document.getElementById("send"); if (b) b.click(); } return dash_clientside.no_update; }""",
    Output("replan-btn", "title"), Input("replan-btn", "n_clicks"), prevent_initial_call=True)


def _suggestion_list(field: str, items: list[dict]):
    if not items:
        return []
    return [html.Button(className="s-item", id={"type": "sugg", "field": field, "i": i}, n_clicks=0, children=[
        html.Span(it["icon"], className="s-icon"),
        html.Div(className="s-text", children=[html.Div(it["main"], className="s-main"),
                                               html.Div(it["secondary"], className="s-sec")]),
    ]) for i, it in enumerate(items)]


def _typed(field):
    def handler(text, picked):
        """Live suggestions as the runner types, like a maps search box."""
        if not text or text in (picked or {}) or text in LANDMARKS:
            return [], []
        items = suggest(text)
        if not items and len(text.strip()) >= 3:
            return [html.Div("No US matches yet. Try adding the city and state.", className="s-empty")], []
        return _suggestion_list(field, items), items
    return handler


for _field in ("start", "finish"):
    app.callback(Output(f"{_field}-suggest", "children"), Output(f"{_field}-items", "data"),
                 Input(f"{_field}-text", "value"), State("suggested", "data"),
                 prevent_initial_call=True)(_typed(_field))


def _picked(field):
    def handler(clicks, items, picked):
        if not ctx.triggered or not ctx.triggered[0]["value"] or not items:
            return no_update, no_update, no_update
        item = items[ctx.triggered_id["i"]]
        picked = {**(picked or {}), item["label"]: [item["lat"], item["lon"]]}
        return item["label"], [], picked
    return handler


for _field in ("start", "finish"):
    app.callback(Output(f"{_field}-text", "value", allow_duplicate=True),
                 Output(f"{_field}-suggest", "children", allow_duplicate=True),
                 Output("suggested", "data", allow_duplicate=True),
                 Input({"type": "sugg", "field": _field, "i": ALL}, "n_clicks"),
                 State(f"{_field}-items", "data"), State("suggested", "data"),
                 prevent_initial_call=True)(_picked(_field))


@app.callback(Output("found-line", "children"),
              Input("start-text", "value"), Input("finish-text", "value"), Input("suggested", "data"),
              Input("finish-mode", "value"))
def found_line(start_text, finish_text, picked, mode):
    picked = picked or {}
    finish_text = finish_text if mode == "place" else None  # a typed finish only counts for "End at a place"
    return [html.Div(f"✓ {role}: {text}") for role, text in (("Start", start_text), ("Finish", finish_text))
            if text and (text in picked or text in LANDMARKS)]


FINISH_WORDS = {"loop": "Loop back", "out_and_back": "Out & back", "at_stop": "End at my stop",
                "place": "End at a place", "surprise": "Surprise me"}


@app.callback(Output("answer-chips", "children"),
              Input("start-text", "value"), Input("distance", "value"), Input("stop", "value"),
              Input("finish-mode", "value"), Input("finish-text", "value"), Input("interests", "value"),
              Input("when", "value"), Input("run-mode", "value"), Input("pace", "value"), Input("club-paces", "value"))
def answer_chips(start_text, distance, stop, mode, finish_text, interests, when, run_mode, pace, club_paces):
    """One chip per answer on the last question; clicking a chip jumps back to that question."""
    main, extras = _stops(stop)
    stops = [STOP_WORDS[c].split(" ", 1)[1] for c in ([main] if main else []) + extras if c in STOP_WORDS]
    finish = FINISH_WORDS.get(mode or "loop", "Loop back")
    if mode == "place" and (finish_text or "").strip():
        finish = f"End at {finish_text.strip()}"
    elif mode == "surprise" and (interests or "").strip():
        finish = f"Surprise me: {interests.strip()}"
    try:
        when_text = _when_text(when)
    except (TypeError, ValueError):
        when_text = "no time set"
    who = f"Run club {club_paces}" if run_mode == "club" else f"{pace or '?'}/mi"
    chips = [("start", f"📍 {start_text or 'no start'}"), ("distance", f"📏 {distance or '?'} mi"),
             ("stop", "🥐 " + (", ".join(stops) if stops else "No stops")), ("finish", f"🏁 {finish}"),
             ("when", f"🗓 {when_text} · {who}")]
    return [html.Button(text, id={"type": "jump", "q": key}, className="answer-chip", n_clicks=0)
            for key, text in chips]


@app.callback(Output("step", "data", allow_duplicate=True), Input({"type": "jump", "q": ALL}, "n_clicks"),
              prevent_initial_call=True)
def jump_to_question(clicks):
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        return no_update
    return QUESTIONS.index(ctx.triggered_id["q"])


@app.callback(Output("distance", "value"), Input({"type": "dist", "i": ALL}, "n_clicks"),
              prevent_initial_call=True)
def quick_distance(_clicks):
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        return no_update
    return DISTANCES[ctx.triggered_id["i"]][1]


DESTINATIONS = ["bakery", "cafe", "restaurant", "park"]  # stops you can finish at or turn around at


def _stops(value) -> tuple[str | None, list[str]]:
    """(main stop, extra stops passed on the way) from the multi-select answer."""
    chosen = [v for v in (value if isinstance(value, list) else [value]) if v and v != "none"]
    main = next((c for c in chosen if c in DESTINATIONS), None)
    return main, [c for c in chosen if c != main]


@app.callback(Output("stop", "value"), Input("stop", "value"), prevent_initial_call=True)
def normalize_stops(value):
    """'None' and real stops exclude each other; an empty answer means none."""
    value = list(value or [])
    if not value:
        return ["none"]
    if "none" in value and len(value) > 1:
        return ["none"] if value[-1] == "none" else [v for v in value if v != "none"]
    return no_update


@app.callback(Output("finish-mode", "className"), Output("finish-mode", "value", allow_duplicate=True),
              Output("finish-place-wrap", "style"), Output("interests-wrap", "style"), Output("finish-note", "children"),
              Input("stop", "value"), Input("finish-mode", "value"), prevent_initial_call="initial_duplicate")
def finish_options(stop, mode):
    """Show only what applies: the address box for 'End at a place', interests for 'Surprise me'."""
    main, extras = _stops(stop)
    hide = {"display": "none"}
    new_mode = mode if (mode != "at_stop" or main) else "loop"
    word = STOP_WORDS.get(main, "").replace("a ", "your ", 1) if main else ""
    notes = {
        "loop": f"Your {main.replace('cafe', 'coffee')} stop will be at the halfway point." if main else
                "You'll come back to your start a different way.",
        "out_and_back": f"You'll turn around at {word}." if main else "You'll turn around halfway and retrace your steps.",
        "at_stop": f"Your run finishes at {word}." if main else "",
        "place": "Your stops will be on the way to your finish." if (main or extras) else "",
        "surprise": "The Destination agent picks a place that fits your interests, at the right distance."
                    + (" Your stops will be on the way." if (main or extras) else ""),
    }
    return ("choice-cards finish-cards" + ("" if main else " no-main-stop"), new_mode if new_mode != mode else no_update,
            {} if new_mode == "place" else hide, {} if new_mode == "surprise" else hide, notes.get(new_mode, ""))


def _words(categories: list[str]) -> str:
    items = [STOP_WORDS[c] for c in categories]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def compose_request(distance, mode, stop, avoid, notes, interests: str = "") -> str:
    """The form's choices as one sentence for the Planner agent."""
    main, extras = _stops(stop)
    if mode == "at_stop" and not main:
        mode = "loop"
    parts = [f"{float(distance):g} miles"]
    if mode in ("place", "surprise"):
        parts.append("to my finish" if mode == "place" else
                     "to a surprise destination" + (f" I'd enjoy ({interests.strip()})" if (interests or "").strip() else ""))
        if main:
            parts.append(f"stopping at {STOP_WORDS[main]} on the way")
    elif mode == "at_stop":
        parts.append(f"ending at {STOP_WORDS[main]}")
    else:
        parts.append("out-and-back" if mode == "out_and_back" else "loop")
        if main:
            parts.append(f"stopping at {STOP_WORDS[main]} halfway, then running back")
    if extras:
        parts.append(f"passing {_words(extras)} on the way")
    if (avoid or "").strip():
        parts.append(f"avoid {avoid.strip()}")
    sentence = ", ".join(parts)
    return f"{sentence}. {notes.strip()}" if (notes or "").strip() else sentence


def finish_form(mode: str, stop, interests: str = "") -> dict:
    """The finish question's card as the fields the agents read (shape, stop position, surprise)."""
    main, extras = _stops(stop)
    if mode == "at_stop" and not main:
        mode = "loop"
    return {"shape": "out_and_back" if mode == "out_and_back" else "loop",
            "stop_position": "end" if mode == "at_stop" else "midway",
            "main_stop": main, "extra_stops": extras,
            "surprise": mode == "surprise", "interests": (interests or "").strip()}


# ------------------------------------------------------------------ question flow

FINISH_MISSING = "Type where you want to finish, or pick another way to finish."


ERROR_SLOTS = ["start-error", "distance-error", "finish-error", "when-error"]
DISTANCE_ERROR = "Pick a distance between 1 and 26.2 miles."
PAST_TIME_ERROR = "That time has already passed. Pick a time from now on (or press “Find my best time”)."


def _check_question(key: str, a: dict) -> tuple[str, str] | None:
    """(error slot, message) if this question's answer can't work, so it's flagged right here
    instead of after planning; None if it's fine. Place lookups that fail on a network hiccup pass:
    the plan tries again and explains."""
    def place_problem(text: str):
        try:
            _resolve(text, a["suggested"])
        except LocationError as exc:
            return None if "online (" in str(exc) else str(exc)  # "couldn't look up online" = network hiccup
        except Exception:  # lookup service hiccup: don't block the runner
            return None
        return None

    if key == "start":
        text = (a["start_text"] or "").strip()
        problem = "Type where you're starting, or pick a place below." if not text else place_problem(text)
        return ("start-error", problem) if problem else None
    if key == "distance":
        try:
            ok = 1 <= float(a["distance"]) <= 26.2
        except (TypeError, ValueError):
            ok = False
        return None if ok else ("distance-error", DISTANCE_ERROR)
    if key == "finish" and a["mode"] == "place":
        text = (a["finish_text"] or "").strip()
        problem = FINISH_MISSING if not text else place_problem(text)
        return ("finish-error", problem) if problem else None
    if key == "when":
        try:
            if datetime.strptime(a["when"], "%Y-%m-%dT%H:%M") < datetime.now() - timedelta(minutes=5):
                return "when-error", PAST_TIME_ERROR
        except (TypeError, ValueError):
            return "when-error", "Pick a day and time for your run."
        if a["run_mode"] == "club":
            try:
                parse_paces(a["club_paces"])
            except ValueError as exc:
                return "when-error", str(exc)
        elif _pace(a["pace"]) is None:
            return "when-error", PACE_ERROR
    return None


@app.callback(Output("step", "data"), [Output(slot, "children") for slot in ERROR_SLOTS],
              Output("finish-text", "className"),
              Input("next-btn", "n_clicks"), Input("back-btn", "n_clicks"),
              State("step", "data"), State("start-text", "value"), State("distance", "value"),
              State("finish-mode", "value"), State("finish-text", "value"), State("when", "value"),
              State("run-mode", "value"), State("pace", "value"), State("club-paces", "value"),
              State("suggested", "data"), prevent_initial_call=True)
def move(_next, _back, step, start_text, distance, mode, finish_text, when, run_mode, pace, club_paces, suggested):
    forward = ctx.triggered_id == "next-btn"
    if forward:
        problem = _check_question(QUESTIONS[step], dict(
            start_text=start_text, distance=distance, mode=mode, finish_text=finish_text, when=when,
            run_mode=run_mode, pace=pace, club_paces=club_paces, suggested=suggested))
        if problem:
            slot, message = problem
            return (no_update, *[message if s == slot else "" for s in ERROR_SLOTS],
                    "big-input invalid" if slot == "finish-error" else "big-input")
    step += 1 if forward else -1
    return max(0, min(len(QUESTIONS) - 1, step)), "", "", "", "", "big-input"


@app.callback(Output("start-error", "children", allow_duplicate=True), Input("start-text", "value"),
              prevent_initial_call=True)
def clear_start_error(_text):
    return ""


@app.callback(Output("distance-error", "children", allow_duplicate=True), Input("distance", "value"),
              prevent_initial_call=True)
def clear_distance_error(_value):
    return ""


@app.callback(Output("when-error", "children", allow_duplicate=True),
              Input("pace", "value"), Input("club-paces", "value"), Input("run-mode", "value"), Input("when", "value"),
              prevent_initial_call=True)
def clear_when_error(_pace_text, _club, _mode, _when):
    return ""


@app.callback(Output("finish-error", "children", allow_duplicate=True),
              Output("finish-text", "className", allow_duplicate=True),
              Input("finish-text", "value"), Input("finish-mode", "value"), prevent_initial_call=True)
def clear_finish_error(_text, _mode):
    """The message goes away as soon as they type or choose another way to finish."""
    return "", "big-input"


@app.callback([Output(f"q-{k}", "className") for k in QUESTIONS]
              + [Output("progress-fill", "style"), Output("back-btn", "style"), Output("next-btn", "style"),
                 Output("send", "style")],
              Input("step", "data"))
def show_question(step):
    classes = ["q active" if i == step else "q" for i in range(len(QUESTIONS))]
    last = step == len(QUESTIONS) - 1
    hide = {"display": "none"}
    return classes + [{"width": f"{(step + 1) / len(QUESTIONS) * 100:.0f}%"},
                      hide if step == 0 else {}, hide if last else {}, {} if last else hide]


@app.callback(Output("start-text", "value", allow_duplicate=True), Output("step", "data", allow_duplicate=True),
              Input({"type": "pick-start", "i": ALL}, "n_clicks"), State("step", "data"),
              prevent_initial_call=True)
def pick_start(_clicks, step):
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        return no_update, no_update
    return list(LANDMARKS)[ctx.triggered_id["i"]], step + 1


@app.callback(Output("view", "data", allow_duplicate=True), Output("step", "data", allow_duplicate=True),
              Input("edit-btn", "n_clicks"), Input("new-btn", "n_clicks"), prevent_initial_call=True)
def back_to_questions(_edit, _new):
    if ctx.triggered_id == "new-btn":
        return "welcome", 0  # a new run starts with the solo / run club choice
    return "wizard", len(QUESTIONS) - 1


@app.callback(Output("stop", "value", allow_duplicate=True), Output("finish-mode", "value", allow_duplicate=True),
              Output("finish-text", "value", allow_duplicate=True), Output("interests", "value"),
              Output("avoid", "value"), Output("request", "value"), Output("pins", "data", allow_duplicate=True),
              Output("deadline", "data", allow_duplicate=True), Output("fit-status", "children", allow_duplicate=True),
              Output("click-target", "value", allow_duplicate=True), Output("attack-demo", "value"),
              Output("distance", "value", allow_duplicate=True), Output("when", "value", allow_duplicate=True),
              Input("new-btn", "n_clicks"), State("deadline", "data"), prevent_initial_call=True)
def fresh_answers(n, deadline=None):
    """'Plan another run' starts from clean answers. Where you start, your pace and your club's paces
    stay; solo or run club is chosen again on the welcome page."""
    if not n:
        return (no_update,) * 13
    # a distance and time set by "Fit a run before my next event" only made sense with that event
    distance, when = (5, _next_saturday_9am()) if deadline else (no_update, no_update)
    return ["none"], "loop", "", "", "", "", {}, None, "", "off", [], distance, when


@app.callback(Output("welcome", "style"), Output("wizard", "style"), Output("results", "style"),
              Output("run", "style"), Input("view", "data"))
def switch_view(view):
    hide = {"display": "none"}
    # Map screens are never display:none. While hidden they keep their full size off-screen,
    # so their maps are always built at the real size and draw the route line correctly.
    parked = {"position": "absolute", "left": "-10000px", "top": "0", "width": "100vw",
              "height": "calc(100vh - 62px)", "visibility": "hidden"}
    return ({} if view == "welcome" else hide, {} if view == "wizard" else hide,
            {} if view == "results" else parked, {} if view == "run" else parked)


@app.callback(Output("view", "data", allow_duplicate=True), Output("step", "data", allow_duplicate=True),
              Output("when", "value", allow_duplicate=True), Output("run-mode", "value"),
              Input("start-btn", "n_clicks"), Input("club-start-btn", "n_clicks"), State("when", "value"),
              prevent_initial_call=True)
def start_planning(n, n_club, when):
    if not (n or n_club):
        return no_update, no_update, no_update, no_update
    try:  # the page may have been open a while: never default to a time in the past
        stale = datetime.strptime(when, "%Y-%m-%dT%H:%M") < datetime.now()
    except (TypeError, ValueError):
        stale = True
    return ("wizard", 0, (_next_saturday_9am() if stale else no_update),
            "club" if ctx.triggered_id == "club-start-btn" else "solo")


@app.callback(Output("mode-pill", "children"), Input("run-mode", "value"))
def mode_pill(mode):
    """Always visible on the questions, so nobody plans a club run thinking it's a solo run."""
    if mode == "club":
        return [html.Strong("👟 Run club"), html.Span(" · switch to solo")]
    return [html.Strong("🏃 Solo run"), html.Span(" · switch to run club")]


@app.callback(Output("run-mode", "value", allow_duplicate=True), Input("mode-pill", "n_clicks"),
              State("run-mode", "value"), prevent_initial_call=True)
def switch_mode(n, mode):
    if not n:
        return no_update
    return "solo" if mode == "club" else "club"


@app.callback(Output("solo-pace-wrap", "style"), Output("club-pace-wrap", "style"), Output("club-note", "children"),
              Input("run-mode", "value"))
def show_run_mode(mode):
    if mode != "club":
        return {}, {"display": "none"}, ""
    return {"display": "none"}, {}, ("Run club: one route for everyone. Each group runs its own pace and gets its own "
                                     "pace plan and arrival time; the club meets at the finish.")


def _run_pace(mode: str | None, pace: str | None, club_paces: str | None) -> float | None:
    """The pace that sets the clock: the runner's pace, or the slowest club group's."""
    if mode == "club":
        try:
            return max(parse_paces(club_paces))
        except ValueError:
            return None
    return _pace(pace)


# ------------------------------------------------------------------ best time to run

SAMPLE_ICS = Path(__file__).resolve().parent / "sample_data" / "sample_calendar.ics"


@app.callback(Output("ics-data", "data"), Output("ics-status", "children"),
              Input("ics-upload", "contents"), Input("bt-sample", "n_clicks"), State("ics-upload", "filename"),
              prevent_initial_call=True)
def load_calendar(contents, _sample, filename):
    if ctx.triggered_id == "bt-sample":
        return "sample", "✓ Using a sample student calendar (classes, meetings, brunch)."
    if contents:
        import base64
        from route_captain.schedule import is_calendar
        try:
            ok = is_calendar(base64.b64decode(contents.split(",", 1)[1]))
        except (ValueError, IndexError):
            ok = False
        if not ok:
            return None, f"✗ {filename} isn't a calendar (.ics) file. In Outlook, save or export your calendar as .ics."
        return contents, f"✓ Calendar loaded: {filename}"
    return no_update, no_update


def _calendar_bytes(ics_data, ics_url) -> bytes | None:
    import base64
    from route_captain.schedule import fetch_ics
    if ics_data == "sample":
        return SAMPLE_ICS.read_bytes()
    if ics_data:
        return base64.b64decode(ics_data.split(",", 1)[1])
    if (ics_url or "").strip():
        return fetch_ics(ics_url)
    return None


def _slot_card(slot: dict, i: int, best: bool, alt: bool):
    w = slot["weather"]
    weather = (f"{w['temp_f']}°F (feels {w['feels_like_f']}°F) · {w['summary']} · rain {w['rain_chance']}%"
               if w.get("available") else "no forecast yet")
    return html.Div(className="slot" + (" best" if best else ""), children=[
        html.Div(className="slot-head", children=[
            html.Strong(("★ Best · " if best else "Good option · " if alt else "")
                        + fmt(slot["start"], "%a %b %-d, %-I:%M %p")),
            html.Span(f"{slot['score']}", className="slot-score"),
        ]),
        html.Div(weather, className="slot-line"),
        html.Div(f"Traffic: {slot['traffic']} (typical) · {slot['daylight']}"
                 + (" · " + ", ".join(slot["why"]) if slot["why"] else ""), className="slot-line muted"),
        html.Button("Use this time", id={"type": "use-slot", "i": i}, className="chip small", n_clicks=0),
    ])


@app.callback(Output("bt-results", "children"), Output("bt-slots", "data"),
              Input("bt-btn", "n_clicks"),
              State("ics-data", "data"), State("ics-url", "value"), State("bt-days", "value"),
              State("start-text", "value"), State("suggested", "data"), State("distance", "value"),
              State("pace", "value"), State("request", "value"), State("run-mode", "value"),
              State("club-paces", "value"), State("progress-key", "data"), prevent_initial_call=True)
def best_time(_n, ics_data, ics_url, days, start_text, suggested, distance, pace, notes, run_mode, club_paces,
              key=None):
    try:
        ics = _calendar_bytes(ics_data, ics_url)
        start = _resolve(start_text or "Cross Campus", suggested)
    except Exception as exc:
        return html.Div(str(exc), className="slot-error"), []
    run_pace = _run_pace(run_mode, pace, club_paces)
    if run_pace is None:
        return html.Div(PACE_ERROR, className="slot-error"), []
    minutes = float(distance or 5) * run_pace
    blocked = GUARD.check(key or "anon")
    if blocked:
        return html.Div(blocked, className="slot-error"), []
    try:
        result = find_best_time(start["lat"], start["lon"], minutes, datetime.now().date(), int(days or 3),
                                ics, notes or "")
    except ValueError as exc:  # unreadable calendar file
        return html.Div(str(exc), className="slot-error"), []
    GUARD.record((result.get("usage") or {}).get("cost_usd"))
    slots = result["slots"]
    if not slots:
        return html.Div("No free slot long enough for this run in that window. Try a longer window or a shorter run.",
                        className="slot-error"), []
    order = [result["choice"]] + result["alternatives"]
    by_id = {s["id"]: s for s in slots}
    shown = [by_id[i] for i in order if i in by_id] + [s for s in slots if s["id"] not in order][:1]
    header = (f"Checked {result['events_considered']} calendar event(s)." if result["used_calendar"]
              else "No calendar added: showing the best times by weather, daylight and traffic only.")
    return html.Div([
        html.Div(header, className="hint"),
        html.P(result["reason"], className="agent-line") if result["reason"] else None,
        *[_slot_card(s, k, s["id"] == result["choice"], s["id"] in result["alternatives"]) for k, s in enumerate(shown)],
        html.Div("Picked by the Scheduler agent from slots scored by code. Traffic uses typical rush hours, not live data.",
                 className="credit"),
    ]), [s["start"].strftime("%Y-%m-%dT%H:%M") for s in shown]


@app.callback(Output("distance", "value", allow_duplicate=True), Output("when", "value", allow_duplicate=True),
              Output("fit-status", "children"), Output("deadline", "data"),
              Input("fit-btn", "n_clicks"),
              State("ics-data", "data"), State("ics-url", "value"), State("when", "value"),
              State("start-text", "value"), State("suggested", "data"), State("pace", "value"),
              State("run-mode", "value"), State("club-paces", "value"), prevent_initial_call=True)
def fit_before_meeting(_n, ics_data, ics_url, when, start_text, suggested, pace, run_mode, club_paces):
    """'I have 45 minutes before my 3pm': size the run to the gap before the next calendar event."""
    from zoneinfo import ZoneInfo

    from route_captain.area import local_timezone
    from route_captain.schedule import busy_times, fit_before_next_event
    none = no_update, no_update
    run_pace = _run_pace(run_mode, pace, club_paces)
    if run_pace is None:
        return *none, html.Div(PACE_ERROR, className="slot-error"), no_update
    try:
        ics = _calendar_bytes(ics_data, ics_url)
        start = _resolve(start_text or "Cross Campus", suggested)
    except Exception as exc:  # unreachable calendar link or unknown start
        return *none, html.Div(str(exc), className="slot-error"), no_update
    if ics is None:
        return *none, html.Div("Add your calendar below first (or press “Try a sample calendar”).",
                               className="slot-error"), no_update
    tz = ZoneInfo(local_timezone(start["lat"], start["lon"]))
    now = datetime.now(tz)
    try:  # start at the Day & time above, or now if that has already passed
        chosen = datetime.strptime(when, "%Y-%m-%dT%H:%M").replace(tzinfo=tz)
        begin = chosen if chosen > now else now
    except (TypeError, ValueError):
        begin = now
    try:
        busy, _ = busy_times(ics, begin.date(), 2, tz)
    except ValueError as exc:
        return *none, html.Div(str(exc), className="slot-error"), no_update
    fit = fit_before_next_event(busy, begin, run_pace)
    start_value = fit["start"].strftime("%Y-%m-%dT%H:%M")
    moved = f"You're busy until {fmt(fit['start'], '%-I:%M %p')}, so the run starts then. " if fit["moved"] else ""
    if fit["status"] == "free":
        when_text = f"at {fmt(fit['event'], '%-I:%M %p')}" if fit["event"] else "in the next two days"
        return no_update, start_value, html.Div(
            f"{moved}Your next event is {when_text}: over 3 hours away, so any distance fits. "
            f"Start time set to {fmt(fit['start'], '%a %-I:%M %p')}.", className="fit-ok"), None
    if fit["status"] == "too_short":
        return no_update, no_update, html.Div(
            f"{moved}Only {fit['gap_min']} min before your next event at {fmt(fit['event'], '%-I:%M %p')}: not enough for "
            f"a mile with {fit['buffer_min']} min to change. Try “Find my best time” for a later slot.",
            className="slot-error"), None
    return fit["miles"], start_value, html.Div(
        f"✓ {moved}Your next event starts at {fmt(fit['event'], '%-I:%M %p')}. Starting {fmt(fit['start'], '%-I:%M %p')} gives you "
        f"{fit['gap_min']} min: distance set to {fit['miles']:g} mi, back by about {fmt(fit['back_by'], '%-I:%M %p')} with "
        f"{fit['buffer_min']} min to change. (Event details stay private; only the time is used.)",
        className="fit-ok"), {"event": fit["event"].strftime("%Y-%m-%dT%H:%M"), "buffer": fit["buffer_min"]}


@app.callback(Output("when", "value"), Output("ics-status", "children", allow_duplicate=True),
              Input({"type": "use-slot", "i": ALL}, "n_clicks"), State("bt-slots", "data"),
              prevent_initial_call=True)
def use_slot(_clicks, slots):
    if not ctx.triggered or not ctx.triggered[0]["value"] or not slots:
        return no_update, no_update
    when = slots[ctx.triggered_id["i"]]
    return when, f"✓ Run time set to {fmt(datetime.strptime(when, '%Y-%m-%dT%H:%M'), '%a %b %-d, %-I:%M %p')}."


# ------------------------------------------------------------------ run mode (live GPS guidance)

def _run_payload(plan: dict, pace: float = 9.5) -> dict:
    """What the in-browser guide needs: the route line with miles along it, and the turn cues."""
    coords = plan["coords"]
    miles = [0.0]
    for a, b in zip(coords, coords[1:]):
        miles.append(miles[-1] + float(haversine_m(*a, *b)) / 1609.344)
    scale = plan["miles"] / miles[-1] if miles[-1] else 1.0  # match the route's official length
    stops = ([plan["stop"]] if plan.get("stop") else []) + plan.get("extra_stops", [])
    return {"coords": coords, "miles": [round(m * scale, 4) for m in miles], "total": plan["miles"],
            "pace": pace,
            "cues": plan.get("cues", []), "stops": [{"name": st["name"], "lat": st["lat"], "lon": st["lon"]} for st in stops],
            "headline": plan["briefing"]["headline"]}


@app.callback(Output("view", "data", allow_duplicate=True), Output("run-route", "data"),
              Input("start-run-btn", "n_clicks"), State("chat", "data"), prevent_initial_call=True)
def start_run(n, chat):
    msg = _latest(chat)
    if not n or not msg:
        return no_update, no_update
    return "run", _run_payload(msg["plan"], msg.get("pace", 9.5))


@app.callback(Output("view", "data", allow_duplicate=True), Input("run-end", "n_clicks"), prevent_initial_call=True)
def end_run(n):
    return "results" if n else no_update


@app.callback(Output("run-map", "children"), Input("run-route", "data"))
def run_map(route):
    if not route:
        return None
    coords = route["coords"]
    layers = [dl.Polyline(positions=coords, color="white", weight=12, opacity=0.95),
              dl.Polyline(positions=coords, color=NAVY, weight=7, opacity=1),
              _pin(*coords[0], "start", "Start", "<i></i>"),
              *[_pin(st["lat"], st["lon"], "stop extra", st["name"], "📍") for st in route["stops"]],
              dl.LayerGroup(id="run-me")]
    lats, lons = [c[0] for c in coords], [c[1] for c in coords]
    center, zoom = _fit([[min(lats), min(lons)], [max(lats), max(lons)]], 700, 600)
    return dl.Map(id="run-leaflet", center=center, zoom=zoom, style={"height": "100%"}, zoomControl=False,
                  zoomAnimation=False, fadeAnimation=False, markerZoomAnimation=False,
                  children=[dl.TileLayer(url=TILES, attribution=TILE_ATTRIBUTION, maxZoom=19), *layers])


# The browser does the live guidance (assets/run_mode.js); this hands it the route.
app.clientside_callback(
    """function (route) { if (route && window.RouteCaptainRun) { window.RouteCaptainRun.load(route); }
                          return window.dash_clientside.no_update; }""",
    Output("run-route", "id"), Input("run-route", "data"))

# "Use my current location" for the start: the browser asks for permission, then fills the box.
app.clientside_callback(
    """async function (n) {
        const nu = window.dash_clientside.no_update, say = window.dash_clientside.set_props;
        if (!n) { return [nu, nu, nu]; }
        if (!navigator.geolocation) {
            return [nu, "This browser can't share its location. Type an address instead.", "field-error"];
        }
        say("gps-status", {children: "Finding your location… (your browser may ask for permission)", className: "hint"});
        try {
            const pos = await new Promise((ok, fail) => navigator.geolocation.getCurrentPosition(ok, fail,
                {enableHighAccuracy: true, timeout: 12000, maximumAge: 30000}));
            const lat = pos.coords.latitude.toFixed(5), lon = pos.coords.longitude.toFixed(5);
            return ["My location (" + lat + ", " + lon + ")", "📍 Using your current location (accurate to about " +
                    Math.round(pos.coords.accuracy) + " m).", "hint"];
        } catch (e) {
            const mac = /Mac/.test(navigator.platform || navigator.userAgent);
            const macTip = mac ? " On a Mac, also check System Settings → Privacy & Security → Location Services: " +
                                 "it must be on, and on for your browser." : "";
            const why = !e || e.code === 1
                ? "Your browser blocked location for this page. Click the icon at the left of the address bar, " +
                  "set Location to Allow, then press the button again." + macTip
                : e.code === 3
                ? "Finding your location took too long. Press the button again, or type an address."
                : "Your computer couldn't work out where it is." + (macTip || " Check that location is on.") +
                  " Or just type an address.";
            return [nu, why, "field-error"];
        }
    }""",
    Output("start-text", "value", allow_duplicate=True), Output("gps-status", "children"),
    Output("gps-status", "className"), Input("gps-start", "n_clicks"), prevent_initial_call=True)


@app.callback(Output("gps-status", "children", allow_duplicate=True),
              Output("gps-status", "className", allow_duplicate=True),
              Input("start-text", "value"), prevent_initial_call=True)
def clear_gps_status(text):
    """A location message only belongs to a location: typing or picking a place clears it."""
    if (text or "").startswith("My location ("):
        return no_update, no_update
    return "", "hint"


# ------------------------------------------------------------------ planning

def _pace(text: str) -> float | None:
    """Minutes per mile from '9:30' or '9.5'; None if it isn't a sensible running pace."""
    try:
        text = str(text or "").strip()
        if ":" in text:
            m, sec = text.split(":")
            value = int(m) + int(sec) / 60 if 0 <= int(sec) < 60 else None
        else:
            value = float(text)
    except ValueError:
        return None
    return value if value is not None and 4 <= value <= 20 else None


PACE_ERROR = "Your pace should look like 9:30 (minutes per mile), between 4:00 and 20:00."


def _serialize(res, start: dict, finish: dict | None) -> dict:
    dest = res.destination
    if dest and not finish:  # "Surprise me": the Destination agent's pick is the finish
        finish = {"label": dest["name"], "lat": dest["lat"], "lon": dest["lon"], "source": "destination"}
    return {
        "destination": {k: dest.get(k) for k in ("name", "category", "lat", "lon", "reason")} if dest else None,
        "start": start,
        "finish": finish,
        "ends_at_stop": bool(res.stop and res.route and res.route.nodes[-1] == res.stop["node"]),
        "gmaps": google_maps_url(res.coords) if res.coords else None,
        "gmaps_qr": google_maps_url(res.coords, waypoints=5, decimals=5) if res.coords else None,
        "coords": res.coords,
        "stop": {k: res.stop[k] for k in ("name", "category", "lat", "lon", "open_on_arrival")} if res.stop else None,
        "amenities": [{k: a[k] for k in ("name", "category", "lat", "lon")} for a in res.amenities],
        "miles": round(res.route.miles, 2) if res.route else None,
        "safety": asdict(res.safety) if res.safety else None,
        "briefing": res.briefing.model_dump() if res.briefing else None,
        "cues": res.cues,
        "trace": res.trace,
        "usage": res.usage,
        "quarantined": res.quarantined,
        "clarification": res.clarification,
        "error": res.error,
        "seconds": round(res.seconds, 1),
        "weather": res.weather,
        "profile": res.profile,
        "weather_advice": res.weather_advice.model_dump() if res.weather_advice else None,
        "pacing_plan": res.pacing_plan.model_dump() if res.pacing_plan else None,
        "calendar": res.calendar.model_dump() if res.calendar else None,
        "route_options": res.route_options,
        "extra_stops": [{k: e[k] for k in ("name", "category", "lat", "lon")} for e in res.extra_stops],
        "navigator_reason": next((t["text"] for t in res.trace if t["agent"] == "Navigator"), None),
        "club": res.club,
        "club_briefing": res.club_briefing.model_dump() if res.club_briefing else None,
    }


def _deadline_for(deadline: dict | None, when_dt: datetime) -> dict | None:
    """Keep the 'next event' from Fit-before-my-meeting only if it belongs to this run (within 4 hours)."""
    if not deadline or not deadline.get("event"):
        return None
    event = datetime.strptime(deadline["event"], "%Y-%m-%dT%H:%M")
    return deadline if timedelta(0) < event - when_dt <= timedelta(hours=4) else None


# Live progress: the plan callback appends each agent's log line here while it works, and a
# fast Interval on the planning screen reads it back (the server is threaded, so both run).
PROGRESS: dict[str, list[dict]] = {}
AGENT_ICONS = {"Locator": "📍", "Map": "🗺️", "Planner": "🧭", "Guard": "🛡️", "Destination agent": "✨",
               "Stops Scout": "🥐", "Route Builder": "🛤️", "Safety Inspector": "🦺", "Navigator": "🧭",
               "Weather": "🌤️", "Forecast": "🌤️", "Elevation": "⛰️", "Pacing": "⏱️", "Calendar": "📅",
               "Briefing Writer": "✍️", "Writers": "✍️", "Run Club Captain": "👟"}


def _progress_list(key: str | None, n) -> list[dict]:
    """A fresh message list for this browser tab's n-th plan (older runs are dropped)."""
    prefix = f"{key or 'anon'}:"
    for old in [k for k in PROGRESS if k.startswith(prefix)]:
        del PROGRESS[old]
    PROGRESS[f"{prefix}{n}"] = []
    return PROGRESS[f"{prefix}{n}"]


app.clientside_callback(
    """function(_ts, key) {
        return key ? dash_clientside.no_update : Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    }""",
    Output("progress-key", "data"), Input("progress-key", "modified_timestamp"), State("progress-key", "data"))


app.clientside_callback(  # poll while the planning screen is up: start on Plan...
    # (kept apart from the stop below: a callback that also listened to "chat" would wait for the
    # plan itself to finish before running, so the feed would never update while planning)
    """function(n, ticks) {
        var t = dash_clientside.callback_context.triggered.map(function (x) { return x.prop_id; });
        var by = t.length ? t[0] : "";
        if (by.indexOf("send") === 0) return false;
        // safety stop: if the plan never comes back, stop once the planning screen is gone
        return document.body.classList.contains("planning") ? dash_clientside.no_update : true;
    }""",
    Output("progress-tick", "disabled"), Input("send", "n_clicks"),
    Input("progress-tick", "n_intervals"), prevent_initial_call=True)

app.clientside_callback(  # ...and stop when the plan arrives
    "function(chat) { return true; }",
    Output("progress-tick", "disabled", allow_duplicate=True), Input("chat", "data"), prevent_initial_call=True)


@app.callback(Output("planning-feed", "children"), Input("progress-tick", "n_intervals"),
              Input("send", "n_clicks"), State("progress-key", "data"))
def show_progress(_tick, n, key):
    if ctx.triggered_id == "send" or not n:
        return [_progress_line({"agent": "Locator", "text": "Reading your answers…"})]
    lines = PROGRESS.get(f"{key or 'anon'}:{n}") or [{"agent": "Locator", "text": "Reading your answers…"}]
    recent = lines[-6:]
    return [_progress_line(item, last=i == len(recent) - 1) for i, item in enumerate(recent)]


def _usage_table(usage: list[dict]):
    """The Agents tab's measured AI use: tokens and cost for each agent call in this plan."""
    if not usage:
        return None
    cost = sum(u.get("cost_usd") or 0 for u in usage)
    num = {"className": "num"}
    return html.Div(className="usage", children=[
        html.Div("AI used for this plan (measured)", className="usage-title"),
        html.Table(className="usage-table", children=[
            html.Thead(html.Tr([html.Th("Agent"), html.Th("Tokens read", **num), html.Th("Written", **num),
                                html.Th("Cost", **num)])),
            html.Tbody([html.Tr([html.Td(u["agent"]), html.Td(f"{u['input_tokens']:,}", **num),
                                 html.Td(f"{u['output_tokens']:,}", **num),
                                 html.Td(f"${u['cost_usd']:.5f}" if u.get("cost_usd") is not None else "–", **num)])
                        for u in usage]
                       + [html.Tr(className="total", children=[
                           html.Td("Total"), html.Td(f"{sum(u['input_tokens'] for u in usage):,}", **num),
                           html.Td(f"{sum(u['output_tokens'] for u in usage):,}", **num),
                           html.Td(f"${cost:.5f}", **num)])]),
        ]),
    ])


def _agent_icon(agent: str) -> str:
    """'Weather agent' and 'Forecast tool' share their base name's icon."""
    return AGENT_ICONS.get(agent) or AGENT_ICONS.get(agent.removesuffix(" agent").removesuffix(" tool"), "•")


def _progress_line(item: dict, last: bool = False):
    text = item["text"] if len(item["text"]) <= 170 else item["text"][:167].rstrip() + "…"
    return html.Div(className="feed-line" + (" current" if last else ""), key=f"{item['agent']}-{hash(text)}",
                    children=[html.Span(_agent_icon(item["agent"]), className="feed-icon"),
                              html.Div([html.Strong(item["agent"]), html.Span(text)])])


@app.callback(Output("chat", "data"), Output("working", "children"), Output("view", "data"),
              Input("send", "n_clicks"),
              State("distance", "value"), State("finish-mode", "value"), State("stop", "value"),
              State("interests", "value"), State("avoid", "value"), State("request", "value"),
              State("chat", "data"), State("start-text", "value"), State("finish-text", "value"),
              State("when", "value"), State("pace", "value"), State("attack-demo", "value"),
              State("suggested", "data"), State("progress-key", "data"),
              State("run-mode", "value"), State("club-paces", "value"), State("deadline", "data"),
              prevent_initial_call=True)
def plan(n, distance, mode, stop, interests, avoid, notes, chat, start_text, finish_text,
         when, pace, attack, suggested, key, run_mode, club_paces, deadline):
    chat = list(chat or [])
    mode = mode or "loop"
    progress = _progress_list(key, n)
    if mode == "place" and not (finish_text or "").strip():
        chat.append({"role": "captain", "plan": {"error": "You chose to end at a place you pick: type a finish "
                                                          "location, or choose another way to finish."}})
        return chat, "", "results"
    has_finish = mode == "place"
    if not distance or not 1 <= float(distance) <= 26.2:
        chat.append({"role": "captain", "plan": {"error": "Pick a distance between 1 and 26.2 miles."}})
        return chat, "", "results"
    club = None
    if run_mode == "club":
        try:
            club = parse_paces(club_paces)
        except ValueError as exc:
            chat.append({"role": "captain", "plan": {"error": str(exc)}})
            return chat, "", "results"
    elif _pace(pace) is None:
        chat.append({"role": "captain", "plan": {"error": PACE_ERROR}})
        return chat, "", "results"
    run_pace = max(club) if club else _pace(pace)
    blocked = GUARD.check(key or "anon")  # per-session rate limit and the app's daily AI budget
    if blocked:
        chat.append({"role": "captain", "plan": {"error": blocked}})
        return chat, "", "results"
    text = compose_request(distance, mode, stop, avoid, notes, interests)
    where = f"from {start_text or 'Cross Campus'}" + (f" to {finish_text}" if has_finish else "")
    chat.append({"role": "user", "text": text, "where": where})
    progress.append({"agent": "Locator", "text": "Finding your start" + (" and finish" if has_finish else "") + " on the map."})
    try:
        start = _resolve(start_text or "Cross Campus", suggested)
        finish = _resolve(finish_text, suggested) if has_finish else None
    except LocationError as exc:
        chat.append({"role": "captain", "plan": {"error": str(exc)}})
        return chat, "", "results"
    except Exception:  # lookup service hiccup: say so instead of failing silently
        chat.append({"role": "captain", "plan": {"error": "Couldn't look up that location right now. "
                                                          "Try a landmark or click the map."}})
        return chat, "", "results"
    moved_from = None
    if when and datetime.strptime(when, "%Y-%m-%dT%H:%M") < datetime.now() - timedelta(minutes=5):
        # only reachable when re-planning from the results page (the When question blocks past times):
        # the time passed while the runner was reading, so start now rather than fail
        moved_from = datetime.strptime(when, "%Y-%m-%dT%H:%M")
        now = datetime.now()
        when = (now + timedelta(minutes=(-now.minute) % 5)).strftime("%Y-%m-%dT%H:%M")
    try:
        when_dt = datetime.strptime(when, "%Y-%m-%dT%H:%M") if when else datetime.now()
        res = plan_personal_run(
            text, start["lat"], start["lon"], when_dt, run_pace,
            include_attack_data="on" in (attack or []),
            end_lat=finish["lat"] if finish else None, end_lon=finish["lon"] if finish else None,
            start_label=start["label"], end_label=finish["label"] if finish else None,
            form={"distance": distance, **finish_form(mode, stop, interests), "notes": (notes or "").strip()},
            progress=progress.append,
            club_paces=club)
        GUARD.record(sum(u.get("cost_usd") or 0 for u in res.usage))
        chat.append({"role": "captain", "plan": _serialize(res, start, finish), "pace": run_pace,
                     "deadline": _deadline_for(deadline, when_dt),
                     "moved_from": fmt(moved_from, "%-I:%M %p") if moved_from else None,
                     "plan_id": f"{len(chat)}-{uuid.uuid4().hex[:6]}",
                     "when": when_dt.strftime("%Y-%m-%dT%H:%M")})
    except Exception as exc:  # show the problem instead of a blank screen
        chat.append({"role": "captain", "plan": {"error": _friendly_error(exc)}})
    return chat, "", "results"


def _friendly_error(exc: Exception) -> str:
    """Plain words for setup problems a first-time user can fix; the raw error otherwise."""
    text = f"{type(exc).__name__}: {exc}"
    if KEY_MISSING in text:
        return KEY_MISSING
    if "Authentication" in text or "401" in text or "invalid api key" in text.lower():
        return "Portkey rejected the key in .env. Check that it's copied exactly (no spaces or quotes), then restart."
    if "UsageLimitExceeded" in text:
        return ("One AI step used far more than a normal plan and was stopped to protect the budget. "
                "Please try again; if it keeps happening, simplify your notes.")
    if "Connection" in text or "Timeout" in text:
        return "Couldn't reach the AI service. Check your internet connection and try again."
    return text


def _resolve(text: str, suggested: dict | None) -> dict:
    """A picked suggestion already has coordinates; anything else goes through the locator."""
    if suggested and text in suggested:
        lat, lon = suggested[text]
        return {"label": text, "lat": lat, "lon": lon, "source": "suggestion"}
    return locate(text)


def _latest(chat):
    """The most recent result if it has a route; None after a failed plan, so the map clears."""
    last = next((m for m in reversed(chat or []) if m["role"] == "captain"), None)
    return last if last and last["plan"].get("coords") else None


@app.callback(Output("gpx-download", "data"), Input("gpx-btn", "n_clicks"), State("chat", "data"),
              prevent_initial_call=True)
def download_gpx(n, chat):
    msg = _latest(chat)
    if not n or not msg:
        return no_update
    plan = msg["plan"]
    return dcc.send_string(to_gpx(plan["coords"], plan["briefing"]["headline"], plan.get("cues")),
                           "route-captain-run.gpx")


@app.callback(Output("ics-download", "data"), Input("ics-btn", "n_clicks"), State("chat", "data"),
              prevent_initial_call=True)
def download_ics(n, chat):
    msg = _latest(chat)
    if not n or not msg:
        return no_update
    start = datetime.strptime(msg["when"], "%Y-%m-%dT%H:%M")
    minutes = (msg["plan"].get("profile") or {}).get("minutes") or msg["plan"]["miles"] * msg.get("pace", 9.5)
    return dcc.send_string(to_ics(msg["plan"], start, minutes, _when_text(msg["when"])), "route-captain-run.ics")


# ------------------------------------------------------------------ rendering

def _fmt_minutes(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def _when_text(when: str | None) -> str:
    if not when:
        return ""
    return fmt(datetime.strptime(when, "%Y-%m-%dT%H:%M"), "%a %b %-d · %-I:%M %p")


def _outlook(msg: dict, account: str) -> str:
    start = datetime.strptime(msg["when"], "%Y-%m-%dT%H:%M")
    minutes = (msg["plan"].get("profile") or {}).get("minutes") or msg["plan"]["miles"] * msg.get("pace", 9.5)
    return outlook_url(msg["plan"], start, minutes, _when_text(msg["when"]), account)


def _navigator_section(plan: dict):
    options = plan.get("route_options") or []
    if len(options) < 2:
        return None
    reason = (plan.get("navigator_reason") or "").split(". ", 1)[-1]
    return _section(f"Navigator compared {len(options)} routes", html.Table(className="pace-table nav-table", children=[
        html.Thead(html.Tr([html.Th(""), html.Th("Miles"), html.Th("Safety"), html.Th("On paths"),
                            html.Th("Climb")])),
        html.Tbody([html.Tr(className="chosen" if o.get("chosen") else "", children=[
            html.Td("✓ chosen" if o.get("chosen") else f"Option {o['id'].split('-')[1]}"),
            html.Td(f"{o['miles']:.2f}"), html.Td(f"{o['safety_score']}"),
            html.Td(f"{o['path_or_sidewalk_miles']:.1f} mi"), html.Td(f"{o['climb_ft']} ft"),
        ]) for o in options]),
    ]), html.P(reason, className="agent-line") if reason else None)


def _section(title, *children):
    return html.Div(className="section", children=[html.H4(title), *children])


WEATHER_ICONS = {"clear": "☀️", "mostly clear": "🌤", "partly cloudy": "⛅", "overcast": "☁️", "fog": "🌫",
                 "thunderstorm": "⛈"}


def _weather_section(w: dict, advice: dict | None):
    advice = advice or {}
    wear = advice.get("what_to_wear") or []
    if not w.get("available"):
        return _section("Weather & what to wear", html.Div(advice.get("summary") or w.get("reason", "Forecast unavailable."),
                                                           className="hint"),
                        html.Ul(className="wear", children=[html.Li(x) for x in wear]) if wear else None)
    icon = next((v for k, v in WEATHER_ICONS.items() if w["summary"].startswith(k)),
                "🌧" if "rain" in w["summary"] or "drizzle" in w["summary"] else
                "🌨" if "snow" in w["summary"] else "🌡")
    temps = f"{w['temp_f']}°F" + (f" → {w['temp_end_f']}°F" if w["temp_end_f"] != w["temp_f"] else "")
    return _section("Weather & what to wear", html.Div(className="weather", children=[
        html.Div(className="weather-main", children=[
            html.Span(icon, className="weather-icon"),
            html.Div([html.Div(f"{temps} · {w['summary']}", className="weather-temp"),
                      html.Div(f"Feels like {w['feels_like_f']}°F · wind {w['wind_mph']} mph"
                               f" · rain {w['rain_chance']}% · UV {w['uv_index']}", className="weather-sub")]),
        ]),
        html.P(advice.get("summary"), className="agent-line") if advice.get("summary") else None,
        html.H4("What to wear", className="sub-h"),
        html.Ul(className="wear", children=[html.Li(x) for x in (wear or w["what_to_wear"])]),
        html.Div(advice.get("heat_advice") or f"Heat and humidity slow your target pace by {w['heat_slowdown']:.1%}.",
                 className="hint") if (advice.get("heat_advice") or w.get("heat_slowdown")) else None,
        html.Div("Written by the Weather agent from the Open-Meteo forecast.", className="credit"),
    ]))


def _elevation_chart(prof: dict):
    miles = [p["mile"] for p in prof["samples"]]
    feet = [p["elev_ft"] for p in prof["samples"]]
    seg_x, seg_pace, seg_text = [], [], []
    for seg in prof["segments"]:
        seg_x += [seg["start"], seg["end"]]
        seg_pace += [seg["pace"], seg["pace"]]
        seg_text += [f"{seg['kind']} {seg['grade'] * 100:+.1f}% · aim {seg['pace_str']}/mi"] * 2
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=miles, y=feet, mode="lines", line={"color": NAVY, "width": 2},
                             fill="tozeroy", fillcolor="rgba(99,170,255,0.25)", name="Elevation",
                             hovertemplate="Mile %{x:.2f}<br>%{y:.0f} ft<extra></extra>"))
    fig.add_trace(go.Scatter(x=seg_x, y=seg_pace, mode="lines", yaxis="y2", name="Target pace",
                             line={"color": BLUE, "width": 1.2, "dash": "dot", "shape": "hv"}, opacity=0.55,
                             text=seg_text,
                             hovertemplate="Mile %{x:.1f}: %{text}<extra></extra>"))
    lo, hi = min(feet), max(feet)
    fig.update_layout(
        height=170, margin={"l": 38, "r": 38, "t": 6, "b": 26}, showlegend=False,
        paper_bgcolor="white", plot_bgcolor="white", hovermode="x unified",
        font={"family": "-apple-system, Segoe UI, sans-serif", "size": 11, "color": "#5d6f86"},
        xaxis={"title": None, "ticksuffix": " mi", "gridcolor": "#EEF4FB", "zeroline": False},
        yaxis={"range": [max(0, lo - (hi - lo) * 0.3 - 10), hi + (hi - lo) * 0.15 + 5], "ticksuffix": " ft",
               "gridcolor": "#EEF4FB", "zeroline": False},
        yaxis2={"overlaying": "y", "side": "right", "autorange": "reversed", "showgrid": False,
                "tickvals": sorted({round(p, 1) for p in seg_pace})[::max(1, len(set(seg_pace)) // 3)],
                "ticktext": [f"{int(v)}:{round((v % 1) * 60):02d}" for v in
                             sorted({round(p, 1) for p in seg_pace})[::max(1, len(set(seg_pace)) // 3)]]},
    )
    return dcc.Graph(figure=fig, config={"displayModeBar": False}, className="elev-chart")


def _pacing_section(prof: dict, plan_out: dict | None):
    if not prof:
        return None
    plan_out = plan_out or {}
    tips = plan_out.get("hill_tips") or []
    hills = prof.get("hills", [])
    return _section("Hills & pacing", html.Div(className="pacing", children=[
        html.Div(className="hint", children=(
            f"Climb {prof['gain_ft']} ft · descend {prof['loss_ft']} ft · "
            f"{prof['min_ft']}–{prof['max_ft']} ft above sea level · flat pace {prof['flat_pace_str']}/mi")),
        _elevation_chart(prof),
        html.Div(className="hills", children=[
            html.Div(className="hill", children=[
                html.Span(f"⛰ {k + 1}", className="hill-icon"),
                html.Div([html.Strong(f"Miles {h['start']:.1f}–{h['end']:.1f}: +{h['gain_ft']} ft"),
                          html.Span(f" ({h['grade_pct']}% avg) · aim {h['pace_str']}/mi")]),
            ]) for k, h in enumerate(hills)
        ] or [html.Div("No notable hills: a flat, steady route.", className="hint")]),
        html.P(plan_out.get("strategy"), className="agent-line") if plan_out.get("strategy") else None,
        html.Ul(className="notes", children=[html.Li(t) for t in tips]) if tips else None,
        html.Div("Strategy and tips by the Pacing agent; paces computed from USGS elevation data.",
                 className="credit"),
        html.Details(className="more inner", children=[
            html.Summary("Pace for every 0.1 mile"),
            html.Table(className="pace-table", children=[
                html.Thead(html.Tr([html.Th("Mile"), html.Th("Terrain"), html.Th("Grade"), html.Th("Aim")])),
                html.Tbody([html.Tr(className=seg["kind"].replace(" ", "-"), children=[
                    html.Td(f"{seg['start']:.1f}–{seg['end']:.1f}"), html.Td(seg["kind"]),
                    html.Td(f"{seg['grade'] * 100:+.1f}%"), html.Td(f"{seg['pace_str']}/mi"),
                ]) for seg in prof["segments"]]),
            ]),
        ]),
    ]))


GROUP_COLORS = ["#286DC0", "#63AAFF", "#7FA7D6", "#002650"]


def _club_summary(club: dict):
    """One line in the Overview: every group's pace and when it reaches the meetup."""
    return html.Div(className="club-card", children=[
        html.Span("👟", className="surprise-icon"),
        html.Div([html.Strong(f"Run club: everyone meets at {club['finish_label']}"),
                  html.Span("Same route, each group at its own pace."),
                  html.Span(" · ".join(f"{g['name']} ({g['pace_str']}) arrives {g['finish']}" for g in club["groups"]),
                            className="club-times")]),
    ])


def _club_tab(club: dict, brief: dict | None):
    notes = {n["group"]: n["note"] for n in (brief or {}).get("group_notes", [])}
    group_cards = []
    for i, g in enumerate(club["groups"]):
        hill = g["hills"][0] if g["hills"] else None
        detail = (f"{g['pace_str']}/mi the whole way"
                  + (f"; ease to about {hill['pace_str']}/mi on the climb at miles {hill['start']:.1f}-{hill['end']:.1f}"
                     if hill else "") + ".")
        group_cards.append(html.Div(className="group-card", style={"borderLeftColor": GROUP_COLORS[i % 4]}, children=[
            html.Div(className="group-head", children=[
                html.Strong(g["name"]), html.Span(f"{g['pace_str']}/mi · about {_fmt_minutes(g['minutes'])}"),
                html.Span(f"arrives {g['finish']}", className="group-miles")]),
            html.P(notes.get(g["name"]) or detail),
        ]))
    first, last = club["groups"][0], club["groups"][-1]
    return html.Div(className="tab-body", children=[
        html.P(brief["summary"], className="summary") if brief else None,
        _section("Pace groups", *group_cards),
        _section(f"Meet at {club['finish_label']}", html.Ol(className="timeline", children=[
            html.Li(f"{club['start']} · everyone starts together")]
            + [html.Li(f"{g['finish']} · {g['name']} arrives ({g['pace_str']}/mi)") for g in club["groups"]])),
        _section("Tips", html.Ul(className="notes", children=[html.Li(t) for t in brief["meetup_tips"]])) if brief else None,
        html.Div(f"Want everyone to arrive at the same moment instead? {last['name']} could start about "
                 f"{club['spread_min']} min before {first['name']}.", className="hint") if club["spread_min"] >= 5 else None,
        html.Div("Times use each group's flat pace adjusted for this route's hills (even effort). "
                 "The Run Club Captain agent wrote the notes.", className="credit"),
    ])


def _back_by_chip(msg: dict, prof: dict, pace: float):
    """'Back by 2:41 PM · 19 min before your next event' for Fit-before-my-meeting runs."""
    deadline = msg.get("deadline")
    if not deadline or not msg.get("when"):
        return None
    start = datetime.strptime(msg["when"], "%Y-%m-%dT%H:%M")
    back = start + timedelta(minutes=prof.get("minutes") or msg["plan"]["miles"] * pace)
    event = datetime.strptime(deadline["event"], "%Y-%m-%dT%H:%M")
    spare = round((event - back).total_seconds() / 60)
    if spare >= 0:
        return html.Span(f"⏱ Back by {fmt(back, '%-I:%M %p')} · {spare} min before your next event", className="chip-status ok")
    return html.Span(f"⏱ Ends {-spare} min after your next event starts: shorten the run", className="chip-status warn")


def _plan_card(msg: dict, latest: bool):
    plan = msg["plan"]
    if plan.get("error"):
        return html.Div(className="msg captain notice", children=[html.Strong("Couldn't plan that. "), plan["error"]])
    if plan.get("clarification"):
        return html.Div(className="msg captain notice", children=plan["clarification"])
    if not plan.get("briefing") or not plan.get("safety"):
        return html.Div(className="msg captain notice", children=[
            html.Strong("Couldn't plan that. "), "Try a shorter distance, a different start or no stop."])
    b, s = plan["briefing"], plan["safety"]
    if not latest:
        return html.Div(className="msg captain past", children=[html.Span("Earlier plan · "), b["headline"]])

    pace = msg.get("pace", 9.5)
    prof = plan.get("profile") or {}
    when_text = _when_text(msg.get("when"))
    stop = plan.get("stop")
    finish = plan.get("finish") or ({"label": stop["name"]} if stop and plan.get("ends_at_stop") else plan["start"])
    chips = [html.Span("✓ Passed safety check" if s["passes"] else "! Read the safety notes",
                       className="chip-status " + ("ok" if s["passes"] else "warn"))]
    if stop:
        hours = {True: "open when you arrive", False: "closed when you arrive",
                 None: "hours unknown, check first"}[stop["open_on_arrival"]]
        chips.append(html.Span(f"{stop['name']} · {hours}", className="chip-status stop"))
    for e in plan.get("extra_stops", []):
        chips.append(html.Span(f"{CATEGORY_ICONS.get(e['category'], '📍')} {e['name']} on the way",
                               className="chip-status stop"))
    club = plan.get("club")
    if club:
        chips.insert(1, html.Span(f"👟 Run club · {len(club['groups'])} pace groups", className="chip-status club"))
    back_by = _back_by_chip(msg, prof, pace)
    if back_by:
        chips.insert(1, back_by)
    if msg.get("moved_from"):
        chips.insert(1, html.Span(f"⏱ Start moved to {fmt(datetime.strptime(msg['when'], '%Y-%m-%dT%H:%M'), '%-I:%M %p')}: "
                                  f"{msg['moved_from']} had passed", className="chip-status warn"))
    dest = plan.get("destination")
    surprise = html.Div(className="surprise-card", children=[
        html.Span(CATEGORY_ICONS.get(dest["category"], "✨"), className="surprise-icon"),
        html.Div([html.Strong(f"Surprise finish: {dest['name']}"), html.Span(dest.get("reason") or "")]),
    ]) if dest else None

    share = html.Div(className="tab-body", children=[
        html.Div(className="takeaway", children=[
            html.Div(className="qr", children=[
                html.Img(src=qr_data_uri(plan.get("gmaps_qr") or plan["gmaps"]),
                         alt="QR code that opens the route in Google Maps"),
                html.Div("Scan with your phone camera", className="qr-caption"),
            ]),
            html.Div(className="actions", children=[
                html.A([html.Span("🧭", className="act-icon"), "Open in Google Maps"], href=plan["gmaps"],
                       target="_blank", rel="noopener", className="action"),
                html.A([html.Span("✉️", className="act-icon"), "Email it to me"],
                       href=mailto_url(plan, when_text), className="action"),
                html.A([html.Span("🗓", className="act-icon"), "Add to Outlook"],
                       href=_outlook(msg, "work"), target="_blank", rel="noopener", className="action"),
                html.Button([html.Span("📅", className="act-icon"), "Other calendars (.ics)"],
                            id="ics-btn", className="action", n_clicks=0),
                html.Button([html.Span("⌚", className="act-icon"), "GPX for your watch"], id="gpx-btn",
                            className="action", n_clicks=0),
            ]),
        ]),
        html.Div(className="hint", children=[
            "“Add to Outlook” opens Outlook on the web with the run filled in, including the agent's outfit "
            "and hill notes; press Save to add it. Using a personal Outlook.com account? ",
            html.A("Open it there instead", href=_outlook(msg, "personal"), target="_blank", rel="noopener"),
            ". Google Maps works on any iPhone or Android and talks you through the turns; the GPX file "
            "matches the route exactly (Apple Watch: open it in an app like WorkOutDoors)."]),
    ])
    overview = html.Div(className="tab-body", children=[
        html.Div(className="chips", children=chips),
        _club_summary(club) if club else None,
        surprise,
        html.P(b["summary"], className="summary"),
        _section("Key moments", html.Ol(className="timeline", children=[html.Li(k) for k in b["key_moments"]])),
        _section("Safety notes", html.Ul(className="notes", children=[html.Li(n) for n in b["safety_notes"]])),
        _navigator_section(plan),
        html.Details(className="more", children=[
            html.Summary("Safety breakdown"),
            html.Ul(className="breakdown", children=[
                *[html.Li([html.Span(k.replace("_", " ").capitalize()), html.Strong(f"{v} mi")])
                  for k, v in s["miles_by_type"].items()],
                html.Li([html.Span("Busy-road crossings"), html.Strong(
                    f"{s['busy_crossings']['signalized']} signals · {s['busy_crossings']['marked']} marked · "
                    f"{s['busy_crossings']['unmarked']} unmarked")]),
                html.Li([html.Span("Daylight"), html.Strong(
                    s["daylight"].replace("_", " ") + (f" (sunset {s['sunset']})" if s["sunset"] else ""))]),
            ]),
        ]),
    ])
    directions = html.Div(className="tab-body", children=[
        html.Ol(className="cues", children=[
            html.Li([html.Span(f"{c['mile']:.1f}", className="mile"), c["text"]]) for c in plan["cues"]]),
    ])
    agents = html.Div(className="tab-body", children=[
        html.Ul(className="trace", children=[
            html.Li([html.Span(t["agent"], className="agent"), t["text"]]) for t in plan["trace"]]),
        html.Ul(className="trace", children=[
            html.Li([html.Span("Quarantined", className="agent q"), f"{q['name']}: {q['reason']}"])
            for q in plan.get("quarantined", [])]),
        _usage_table(plan["usage"]),
        html.Div(className="hint", children=(
            f"{sum(u['input_tokens'] for u in plan['usage']):,} input + "
            f"{sum(u['output_tokens'] for u in plan['usage']):,} output tokens · "
            f"{sum(u['requests'] for u in plan['usage'])} model calls · {plan['seconds']} s")),
    ])

    def tab(label, body):
        return dcc.Tab(label=label, children=body, className="ptab", selected_className="ptab-on")

    return html.Div(className="msg captain plan", **{"data-plan": msg.get("plan_id", "")}, children=[
        html.Div(className="plan-head", children=[
            html.Div(className="plan-route", children=[
                html.Span(className="dot start sm"), plan["start"]["label"],
                html.Span("→", className="arrow"),
                html.Span(className="dot finish sm"), finish["label"],
            ]),
            html.H3(b["headline"]),
            html.Div(className="plan-meta", children=[
                html.Span(f"🗓 {when_text}"),
                html.Span(f"“{msg['asked']}”", className="plan-asked") if msg.get("asked") else None,
            ]),
        ]),
        html.Div(className="stats four", children=[
            html.Div(className="stat", children=[html.Span("📏", className="stat-icon"),
                                                 html.Span(f"{plan['miles']:.2f}", className="num"),
                                                 html.Span("miles", className="lbl")]),
            html.Div(className="stat", children=[html.Span("⏱", className="stat-icon"),
                                                 html.Span(_fmt_minutes(prof.get("minutes", plan["miles"] * pace)),
                                                           className="num"),
                                                 html.Span("slowest group" if plan.get("club") else "est. time",
                                                           className="lbl")]),
            html.Div(className="stat", children=[html.Span("⛰", className="stat-icon"),
                                                 html.Span(f"{prof.get('gain_ft', 0)} ft", className="num"),
                                                 html.Span("climb", className="lbl")]),
            html.Div(className="stat", children=[
                html.Span("🛡", className="stat-icon"),
                html.Span(f"{s['score']}", className="num"), html.Span("safety", className="lbl"),
                html.Div(className="meter", children=html.Div(className="meter-fill",
                                                              style={"width": f"{s['score']}%"}))]),
        ]),
        dcc.Tabs(className="plan-tabs", parent_className="plan-tabs-wrap", children=[
            tab("📋 Overview", overview),
            *([tab("👟 Club", _club_tab(club, plan.get("club_briefing")))] if club else []),
            tab("⛰ Pacing", html.Div(className="tab-body", children=_pacing_section(prof, plan.get("pacing_plan"))
                                   or html.Div("No elevation data.", className="hint"))),
            tab("⛅ Weather", html.Div(className="tab-body", children=_weather_section(
                plan.get("weather") or {}, plan.get("weather_advice")))),
            tab("🧭 Turns", directions),
            tab("📤 Share", share),
            tab("🤖 Agents", agents),
        ]),
    ])


@app.callback(Output("history", "children"), Input("chat", "data"))
def render_history(chat):
    if not chat:
        return html.Div(className="welcome", children=[
            html.H3("Where are we running today?"),
            html.P("Answer a few questions and press Plan my run."),
            html.Ul([
                html.Li([html.Strong("Right distance. "), "Every route is within 5% of what you ask for."]),
                html.Li([html.Strong("Safety checked. "), "Busy roads, crossings and sunset, scored for you."]),
                html.Li([html.Strong("Take it anywhere. "),
                         "Scan to open on your phone, email it, or add it to your calendar."]),
            ]),
        ])
    last = max((i for i, m in enumerate(chat) if m["role"] == "captain"), default=-1)
    if last < 0:
        return []
    asked = next((m["text"] for m in reversed(chat[:last]) if m["role"] == "user"), "")
    return [_plan_card({**chat[last], "asked": asked}, True)]


def _safety_line(safety: dict, hills: int) -> str:
    c = safety.get("busy_crossings") or {}
    parts = [f"🛡 {safety.get('score', '–')}/100"]
    if c.get("unmarked"):
        parts.append(f"⚠ {c['unmarked']} unmarked crossing{'s' if c['unmarked'] > 1 else ''}")
    marked = (c.get("marked") or 0) + (c.get("signalized") or 0)
    if marked:
        parts.append(f"{marked} marked/signal crossing{'s' if marked > 1 else ''}")
    busy = (safety.get("miles_by_type") or {}).get("busy_road") or 0
    if busy >= 0.05:
        parts.append(f"{busy:.1f} mi on busy roads")
    if hills:
        parts.append(f"⛰ {hills} hill{'s' if hills > 1 else ''}")
    return " · ".join(parts)


def _elevation_strip(prof: dict):
    miles = [p["mile"] for p in prof["samples"]]
    feet = [p["elev_ft"] for p in prof["samples"]]
    def pace_at(mile):
        seg = next((sg for sg in prof["segments"] if sg["start"] <= mile < sg["end"]), None)
        return seg["pace_str"] if seg else (prof["segments"][-1]["pace_str"] if prof["segments"]
                                            else prof["flat_pace_str"])
    labels = [f"{f:.0f} ft · aim {pace_at(m)}/mi" for m, f in zip(miles, feet)]
    lo, hi = min(feet), max(feet)
    fig = go.Figure(go.Scatter(x=miles, y=feet, mode="lines", line={"color": NAVY, "width": 2}, fill="tozeroy",
                               fillcolor="rgba(99,170,255,0.28)", text=labels,
                               hovertemplate="Mile %{x:.1f} · %{text}<extra></extra>"))
    fig.update_layout(height=78, margin={"l": 6, "r": 6, "t": 2, "b": 18}, showlegend=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font={"family": "-apple-system, Segoe UI, sans-serif", "size": 12, "color": "#5d6f86"},
                      xaxis={"ticksuffix": " mi", "showgrid": False, "zeroline": False, "fixedrange": True},
                      yaxis={"visible": False, "range": [max(0, lo - (hi - lo) * 0.6 - 10), hi + 5],
                             "fixedrange": True})
    return dcc.Graph(figure=fig, config={"displayModeBar": False}, className="strip-chart")


@app.callback(Output("map-overlay", "children"), Input("chat", "data"))
def map_overlay(chat):
    msg = _latest(chat)
    if not msg:
        return None
    plan, prof = msg["plan"], msg["plan"].get("profile") or {}
    stop = plan.get("stop")
    finish = (plan.get("finish") or {}).get("label") or (stop["name"] if stop and plan.get("ends_at_stop")
                                                          else plan["start"]["label"])
    minutes = prof.get("minutes", plan["miles"] * msg.get("pace", 9.5))
    w = plan.get("weather") or {}
    return [
        html.Div(className="map-summary", children=[
            html.Div(className="ms-route", children=[html.Span(className="dot start sm"), plan["start"]["label"],
                                                     html.Span("→", className="arrow"),
                                                     html.Span(className="dot finish sm"), finish]),
            html.Div(className="ms-stats", children=[
                html.Span([html.Strong(f"{plan['miles']:.2f}"), " mi"]),
                html.Span([html.Strong(_fmt_minutes(minutes))]),
                html.Span([html.Strong(f"{prof.get('gain_ft', 0)}"), " ft climb"]),
                html.Span([html.Strong(f"{w['temp_f']}°F")]) if w.get("available") else None,
            ]),
            html.Div(className="ms-safety", children=_safety_line(plan.get("safety") or {}, len(prof.get("hills") or []))),
        ]),
        html.Div(className="map-strip", children=[
            html.Div(className="strip-head", children=[
                html.Span("Elevation", className="strip-title"),
                html.Div(className="strip-legend", children=[
                    *[html.Span([html.I(className="ln", style={"background": c}), label.capitalize()])
                      for _, c, _, label in GRADE_STYLES],
                    html.Span("⛰ hill"), html.Span("⚠ unmarked crossing"), html.Span("🚸 🚦 crossings"),
                ]),
            ]),
            _elevation_strip(prof) if prof.get("samples") else html.Div("No elevation data.", className="hint"),
        ]),
    ]


GRADE_STYLES = [  # (minimum grade, color, line width, label): darker = steeper
    (0.06, "#001A35", 9, "steep climb"), (0.03, "#00356B", 8, "climb"), (0.015, "#286DC0", 6, "gentle climb"),
    (-0.015, "#63AAFF", 5, "flat"), (-1.0, "#BDD5EA", 5, "downhill")]


def _grade_style(grade: float):
    return next((c, w, label) for g, c, w, label in GRADE_STYLES if grade >= g)


def _hill_coords(segments: list[dict], hill: dict) -> list:
    pts = []
    for seg in segments:
        if seg["start"] >= hill["start"] - 1e-6 and seg["end"] <= hill["end"] + 1e-6:
            pts.extend(seg["coords"] if not pts else seg["coords"][1:])
    return pts


def _sunset_spot(msg: dict, segments: list[dict], safety: dict):
    """Where on the route the runner will be at sunset, if the run crosses it."""
    if not segments or not safety.get("sunset") or safety.get("daylight") not in ("near_sunset", "dark"):
        return None
    try:
        start = datetime.strptime(msg["when"], "%Y-%m-%dT%H:%M")
        sunset = datetime.combine(start.date(), datetime.strptime(safety["sunset"], "%I:%M %p").time())
    except (KeyError, TypeError, ValueError):
        return None
    elapsed = 0.0
    for seg in segments:
        elapsed += (seg["end"] - seg["start"]) * seg["pace"]
        if start + timedelta(minutes=elapsed) >= sunset:
            return seg["coords"][0], safety["sunset"]
    return None


@app.callback(Output("mapwrap", "children"),
              Input("chat", "data"), Input("pins", "data"), Input("attack-demo", "value"), Input("view", "data"),
              Input("recenter-btn", "n_clicks"), State("start-text", "value"), State("finish-text", "value"),
              State("finish-mode", "value"))
def render_map(chat, pins, attack, view, _recenter, start_text, finish_text, finish_mode):
    if view != "results":
        return no_update  # the map only exists on the results screen
    layers = []
    msg = _latest(chat)
    if "on" in (attack or []):
        _, quarantined = load_places(True)
        planted = {p["id"]: p for p in json.loads(ATTACK_PLACES.read_text(encoding="utf-8"))}
        for q in quarantined:
            p = planted.get(q["id"])
            if p:
                layers.append(dl.CircleMarker(center=(p["lat"], p["lon"]), radius=7, color="#8a97a8",
                                              weight=2, fillColor="white", fillOpacity=0.7,
                                              children=dl.Tooltip(f"Quarantined by Guard: {p['name']}")))
    bounds, center = None, HOME
    if msg:
        plan = msg["plan"]
        coords = plan["coords"]
        prof = plan.get("profile") or {}
        segments = prof.get("segments") or []
        safety = plan.get("safety") or {}
        base = [dl.Polyline(positions=coords, color="white", weight=12, opacity=0.95)]

        # hills: a soft highlight under each big climb, then the route colored by steepness
        hills = prof.get("hills") or []
        for h in hills:
            band = _hill_coords(segments, h)
            if band:
                base.append(dl.Polyline(positions=band, color=LIGHT_BLUE, weight=22, opacity=0.35))
        if segments:
            for seg in segments:
                color, weight, label = _grade_style(seg["grade"])
                tip = (f"Mile {seg['start']:.1f}–{seg['end']:.1f} · {label} {seg['grade'] * 100:+.1f}% "
                       f"({seg['change_ft']:+d} ft) · aim {seg['pace_str']}/mi")
                base.append(dl.Polyline(positions=seg["coords"], color=color, weight=weight, opacity=1,
                                        children=dl.Tooltip(tip, sticky=True, className="map-label")))
        else:
            base.append(dl.Polyline(positions=coords, color=NAVY, weight=5, opacity=1))
        hill_badges = [
            dl.DivMarker(position=_hill_coords(segments, h)[0], iconOptions={
                "className": "hill-badge-wrap", "html": f"<div class='hill-badge'>⛰ {i + 1}</div>",
                "iconSize": [0, 0], "iconAnchor": [0, 0]},
                children=dl.Tooltip(f"Hill {i + 1}: miles {h['start']:.1f}–{h['end']:.1f} · +{h['gain_ft']} ft "
                                    f"({h['grade_pct']}% avg) · aim {h['pace_str']}/mi", className="map-label"))
            for i, h in enumerate(hills) if _hill_coords(segments, h)]

        # safety: busy-road stretches and crossings, as the Safety Inspector saw them
        safety_layer = [dl.Polyline(positions=st, color="#001A35", weight=3, opacity=1, dashArray="1 8",
                                    lineCap="round", children=dl.Tooltip("On a busy road (no separate sidewalk mapped)",
                                                                         sticky=True, className="map-label"))
                        for st in safety.get("busy_stretches", []) if len(st) > 1]
        crossing_icons = {"unmarked": ("⚠", "Unmarked crossing of a busy road: cross with care"),
                          "signalized": ("🚦", "Crossing with traffic signals"),
                          "marked": ("🚸", "Marked crosswalk over a busy road")}
        for c in safety.get("crossing_points", []):
            icon, tip = crossing_icons[c["kind"]]
            safety_layer.append(dl.DivMarker(position=(c["lat"], c["lon"]), iconOptions={
                "className": "x-badge-wrap", "html": f"<div class='x-badge {c['kind']}'>{icon}</div>",
                "iconSize": [0, 0], "iconAnchor": [0, 0]}, children=dl.Tooltip(tip, className="map-label")))
        sunset_spot = _sunset_spot(msg, segments, safety)
        if sunset_spot:
            safety_layer.append(_pin(*sunset_spot[0], "sunset", f"Sunset ~{sunset_spot[1]}", "🌇"))

        # stops and water/restrooms
        stops_layer = []
        for a in plan.get("amenities", []):
            stops_layer.append(dl.CircleMarker(center=(a["lat"], a["lon"]), radius=6, color=BLUE, weight=2,
                                               fillColor=PALE, fillOpacity=1,
                                               children=dl.Tooltip(f"{a['category'].title()}: {a['name']}")))
        if plan.get("stop"):
            st = plan["stop"]
            hours = {True: " · open", False: " · closed", None: ""}[st.get("open_on_arrival")]
            club = plan.get("club")
            meet = (f" · meet {club['groups'][0]['finish']}–{club['groups'][-1]['finish']}"
                    if club and plan.get("ends_at_stop") else "")
            stops_layer.append(_pin(st["lat"], st["lon"], "stop" + (" finish" if plan.get("ends_at_stop") else ""),
                                    st["name"] + hours + meet, CATEGORY_ICONS.get(st["category"], "📍")))
        for e in plan.get("extra_stops", []):
            stops_layer.append(_pin(e["lat"], e["lon"], "stop extra", e["name"], CATEGORY_ICONS.get(e["category"], "📍")))

        miles_layer = []
        for n in range(1, int(plan["miles"]) + 1):
            seg = next((sg for sg in segments if sg["start"] <= n <= sg["end"]), None)
            if seg:
                miles_layer.append(dl.DivMarker(position=seg["coords"][-1] if seg["end"] - n < 0.05 else seg["coords"][0],
                                                iconOptions={"className": "mile-icon", "html": str(n),
                                                             "iconSize": [22, 22], "iconAnchor": [11, 11]}))

        start_pt, end_pt = coords[0], coords[-1]
        loop = haversine_m(*start_pt, *end_pt) < 60
        club = plan.get("club")
        meet = f" · meet {club['groups'][0]['finish']}–{club['groups'][-1]['finish']}" if club else ""
        base.append(_pin(*start_pt, "start", ("Start & finish" + meet) if loop else "Start", "<i></i>"))
        if not loop and not plan.get("ends_at_stop"):
            dest = plan.get("destination")
            base.append(_pin(*end_pt, "finish", (f"Finish: {dest['name']}" if dest else "Finish") + meet,
                             CATEGORY_ICONS.get(dest["category"], "🏁") if dest else "🏁"))

        layers = base + layers + [dl.LayersControl(position="topright", collapsed=False, children=[
            dl.Overlay(dl.LayerGroup(hill_badges), name="⛰ Hills", checked=True),
            dl.Overlay(dl.LayerGroup(safety_layer), name="🛡 Safety", checked=True),
            dl.Overlay(dl.LayerGroup(stops_layer), name="📍 Stops", checked=True),
            dl.Overlay(dl.LayerGroup(miles_layer), name="① Mile markers", checked=True),
        ])]
        lats, lons = [c[0] for c in coords], [c[1] for c in coords]
        bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]
    # dropped pins for the next plan sit on top of the route (the route stays in view)
    new = _new_pins(pins, msg, start_text, finish_text, finish_mode)
    for key, fill in (("start", NAVY), ("finish", LIGHT_BLUE)):
        pin = new.get(key)
        if not pin:
            continue
        layers.append(_point(pin["lat"], pin["lon"], f"New {key} · dropped pin", fill, permanent=True))
        center = (pin["lat"], pin["lon"])
        if bounds:
            bounds = [[min(bounds[0][0], pin["lat"]), min(bounds[0][1], pin["lon"])],
                      [max(bounds[1][0], pin["lat"]), max(bounds[1][1], pin["lon"])]]
    return _map(layers, center=center, bounds=bounds)


def _lan_ip() -> str:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))
    if not has_key():
        print(f"\n  WARNING: {KEY_MISSING}\n     The app will open, but planning needs the key.\n")
    if "--phone" in sys.argv:
        # Phone mode: phones only share GPS with https pages, so serve over https on your Wi-Fi.
        # The certificate is made on the spot, so the phone shows a one-time "not private" warning.
        try:
            import cryptography  # noqa: F401  (needed for the on-the-spot certificate)
        except ImportError:
            sys.exit("Phone mode needs the 'cryptography' package: pip install -r requirements.txt")
        print(f"\n  On your phone (same Wi-Fi), open:  https://{_lan_ip()}:{port}\n"
              "  Tap 'Show details' / 'Advanced' -> 'visit this website' on the warning, then allow location.\n")
        app.run(debug=False, use_reloader=False, host="0.0.0.0", port=port, ssl_context="adhoc")
    else:
        app.run(debug=False, use_reloader=False, port=port)
