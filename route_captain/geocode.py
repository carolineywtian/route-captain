"""Turns a typed location into map coordinates.

Tried in order, so most lookups work offline:
  1. coordinates ("41.3104, -72.9273")
  2. landmarks (Cross Campus, Yale Bowl, ...)
  3. intersections from the map ("Chapel St & York St")
  4. place names from the map snapshot ("Atticus")
  5. OpenStreetMap's Nominatim address search (online; results cached on disk).
     Nominatim's usage policy: identify the app, at most 1 request per second, cache results.

Suggestions while typing come from `suggest()`: local matches plus Photon (komoot's
OpenStreetMap search, built for search-as-you-type; Nominatim forbids that use).
"""
from __future__ import annotations

import difflib
import json
import re
import time
from functools import lru_cache

import requests

from .mapdata import DATA_DIR, load_graph
from .places import load_places

MAP_CENTER = (41.3111, -72.9267)
STATE_CODES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
               "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL",
               "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
               "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
               "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
               "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
               "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
               "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Puerto Rico": "PR", "Rhode Island": "RI",
               "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
               "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
               "Wyoming": "WY"}
GEOCODE_CACHE = DATA_DIR / ".geocode_cache.json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
USER_AGENT = "RouteCaptain/0.1 (Yale AI Foundations student project)"

LANDMARKS = {
    "Cross Campus": (41.3104, -72.9273),
    "Yale Bowl": (41.3133, -72.9603),
    "Payne Whitney Gym": (41.3133, -72.9310),
    "Science Hill": (41.3175, -72.9235),
    "Wooster Square": (41.3040, -72.9150),
    "East Rock Park (College Woods)": (41.3245, -72.9135),
    "East Rock Summit": (41.3283, -72.9068),
    "New Haven Green": (41.3082, -72.9251),
    "Union Station": (41.2973, -72.9265),
}

_SUFFIXES = {"street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd", "drive": "dr",
             "place": "pl", "lane": "ln", "court": "ct", "terrace": "ter", "parkway": "pkwy"}
_last_request = 0.0


class LocationError(ValueError):
    pass


def _norm(text: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    return " ".join(_SUFFIXES.get(w, w) for w in words)


def _result(label, lat, lon, source):
    from .area import in_us
    if not in_us(lat, lon):
        raise LocationError(f'"{label}" is outside the United States. Route Captain plans runs in the US.')
    return {"label": label, "lat": lat, "lon": lon, "source": source}


def _has_city(text: str) -> bool:
    """'300 Congress Ave, Austin TX' names its own city; '300 Whitney' means New Haven."""
    return "," in text or bool(re.search(r"\b\d{5}\b", text))


@lru_cache(maxsize=1)
def _street_nodes() -> dict[str, set[int]]:
    graph = load_graph()
    index: dict[str, set[int]] = {}
    for u, v, d in graph.edges(data=True):
        names = d.get("name")
        for name in names if isinstance(names, list) else [names]:
            if name and str(name) != "nan":
                index.setdefault(_norm(str(name)), set()).update((u, v))
    return index


def _intersection(a: str, b: str):
    index = _street_nodes()
    def nodes_for(street):
        key = _norm(street)
        if key in index:
            return index[key], key
        close = difflib.get_close_matches(key, index.keys(), n=1, cutoff=0.8)
        return (index[close[0]], close[0]) if close else (set(), None)
    na, ka = nodes_for(a)
    nb, kb = nodes_for(b)
    shared = na & nb
    if not shared:
        return None
    graph = load_graph()
    node = next(iter(sorted(shared)))
    return graph.nodes[node]["y"], graph.nodes[node]["x"], f"{ka.title()} & {kb.title()}"


def _place(text: str):
    places, _ = load_places(False)
    key = _norm(text)
    named = [p for p in places if p.get("name") and not p.get("unnamed")]
    exact = [p for p in named if _norm(p["name"]) == key]
    if exact:
        return exact[0]
    contains = [p for p in named if len(key) >= 4 and key in _norm(p["name"])]
    if len(contains) == 1:
        return contains[0]
    close = difflib.get_close_matches(key, [_norm(p["name"]) for p in named], n=1, cutoff=0.85)
    if close:
        return next(p for p in named if _norm(p["name"]) == close[0])
    return None


_UNIT_ID = r"(?:[\w-]*\d[\w-]*|[A-Za-z](?!\w))"   # "2", "4B", "12-C" or a single letter: not a street name
_UNIT = re.compile(r"(?:,\s*|\s+)(?:apt|apartment|unit|suite|ste|fl|floor|rm|room|bldg|building)\.?\s*#?\s*"
                   + _UNIT_ID + r"|(?:,\s*|\s+)#\s*" + _UNIT_ID, re.IGNORECASE)


def strip_unit(text: str) -> str:
    """'24 Clark St Apt 2, New Haven' -> '24 Clark St, New Haven': only the building matters for a run."""
    return re.sub(r"\s+,", ",", _UNIT.sub("", text)).strip(" ,")


def _nominatim(text: str):
    global _last_request
    cache = json.loads(GEOCODE_CACHE.read_text(encoding="utf-8")) if GEOCODE_CACHE.exists() else {}
    key = _norm(text)
    if key in cache:
        return cache[key]
    wait = 1.0 - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()
    searches = []
    if not _has_city(text):  # a short local address: New Haven first
        searches.append({"q": f"{text}, New Haven, CT", "viewbox": "-72.99,41.35,-72.86,41.27", "bounded": 1})
    # then worldwide, so "Toronto, Ontario" is recognized as outside the US (not Ontario, California)
    searches.append({"q": text})
    hits = []
    try:
        for extra in searches:
            resp = requests.get(NOMINATIM_URL, headers={"User-Agent": USER_AGENT}, timeout=8,
                                params={"format": "json", "limit": 1, "addressdetails": 1, **extra})
            resp.raise_for_status()
            hits = resp.json()
            if hits:
                break
            time.sleep(1.0)  # Nominatim allows one request per second
    except (requests.RequestException, ValueError) as exc:
        raise LocationError(f'Could not look up "{text}" online ({exc.__class__.__name__}). '
                            "Check your connection, or try a landmark or another address.") from exc
    found = None
    if hits:
        parts = hits[0].get("display_name", text).split(", ")
        addr = hits[0].get("address") or {}
        town = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet")
        name = (hits[0].get("name") or "").strip()
        if name and not name[0].isdigit() and name != addr.get("road"):   # a business or place: "Clark Point Cafe, Southwest Harbor"
            label = name + (f", {town}" if town else "")
        elif addr.get("road") and addr.get("house_number"):   # a street address: "24 Clark Street, New Haven"
            label = f"{addr['house_number']} {addr['road']}" + (f", {town}" if town else "")
        else:
            label = ", ".join(parts[:3])
        found = {"label": label, "lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"]),
                 "outside_us": (hits[0].get("address") or {}).get("country_code", "us") != "us"}
    cache[key] = found
    GEOCODE_CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return found


def locate(text: str, allow_online: bool = True) -> dict:
    """{label, lat, lon, source} for a typed location, or LocationError with a helpful message."""
    text = (text or "").strip()
    if not text:
        raise LocationError("Please enter a location.")

    m = re.fullmatch(r"(my location\s*\(|pin:?\s*)?(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\)?", text, re.IGNORECASE)
    if m:
        lat, lon = float(m.group(2)), float(m.group(3))
        label = "Your location" if (m.group(1) or "").lower().startswith("my location") else f"Pin ({lat:.4f}, {lon:.4f})"
        return _result(label, lat, lon, "coordinates")

    for name, (lat, lon) in LANDMARKS.items():
        if _norm(name) == _norm(text) or (len(_norm(text)) >= 4 and _norm(text) in _norm(name)):
            return _result(name, lat, lon, "landmark")

    parts = re.split(r"\s*(?:&|\band\b|\bat\b|/|\+)\s*", text, flags=re.IGNORECASE)
    if len(parts) == 2 and all(parts):
        hit = _intersection(*parts)
        if hit:
            return _result(hit[2], hit[0], hit[1], "intersection")

    place = _place(text)
    if place:
        return _result(place["name"], place["lat"], place["lon"], "map place")

    if allow_online:
        query = strip_unit(text)
        hit = _nominatim(query)
        if hit and hit.get("outside_us"):
            raise LocationError(f'"{hit["label"]}" is outside the United States. Route Captain plans runs in the US.')
        if hit:
            return _result(hit["label"], hit["lat"], hit["lon"], "address search")
        number = re.match(r"\s*(\d+)\s+\w", query)
        if number:  # a street address OpenStreetMap doesn't know: every US address is in the Census list
            census = _census(query)
            if census:
                c = census[0]
                return _result(f"{c['main']}, {c['secondary'].split(',')[0]}", c["lat"], c["lon"], "address search")
            # typo-tolerant search, trusted only if it found the same house number on a real street
            for item in _photon(query):
                if item["kind"] == "address" and item["main"].split()[0] == number.group(1):
                    town = item["secondary"].split(",")[0]
                    return _result(f"{item['main']}, {town}" if town else item["main"], item["lat"], item["lon"],
                                   "address search")
    raise LocationError(f'Could not find "{text}". Try a landmark, a street address, '
                        'or an intersection like "Chapel St & York St", and include the city if it\'s outside New Haven.')


# ---------------------------------------------------------------- suggestions while typing
#
# Like a maps search box: street addresses from the U.S. Census geocoder (free, no key,
# covers every U.S. address, even partial ones like "300 Whitney"), places and streets
# from Photon (OpenStreetMap search built for type-ahead), plus landmarks and map places.

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
CATEGORY_ICONS = {"bakery": "🥐", "cafe": "☕", "restaurant": "🍽", "park": "🌳", "water": "💧",
                  "restroom": "🚻", "track": "🏃", "viewpoint": "🌄", "artwork": "🎨", "museum": "🏛",
                  "attraction": "⭐", "historic": "🗿", "garden": "🌷", "ice_cream": "🍦", "bookstore": "📚",
                  "library": "📖", "waterfront": "🌊"}


def _item(main, secondary, lat, lon, kind, icon="📍", label=None):
    label = label or (f"{main}, {secondary}" if secondary else main)
    return {"main": main, "secondary": secondary, "label": label,
            "lat": lat, "lon": lon, "kind": kind, "icon": icon}


@lru_cache(maxsize=512)
def _census(text: str) -> tuple:
    query = text if _has_city(text) or re.search(r"new haven", text, re.IGNORECASE) else f"{text}, New Haven, CT"
    try:
        resp = requests.get(CENSUS_URL, timeout=6, headers={"User-Agent": USER_AGENT}, params={
            "address": query, "benchmark": "Public_AR_Current", "format": "json"})
        resp.raise_for_status()
        matches = resp.json()["result"]["addressMatches"]
    except (requests.RequestException, ValueError, KeyError):
        return ()
    out = []
    for m in matches[:4]:
        street, _, rest = m["matchedAddress"].partition(", ")
        out.append(_item(street.title(), rest.title().replace(", Ct,", ", CT"),
                         m["coordinates"]["y"], m["coordinates"]["x"], "address"))
    # the same street address can exist in two towns that share a mailing name (24 Clark St is in
    # New Haven 06511 and Hamden 06518): put the one nearest the map first
    out.sort(key=lambda i: (i["lat"] - MAP_CENTER[0]) ** 2 + (i["lon"] - MAP_CENTER[1]) ** 2)
    return tuple(out)


def _photon_item(f: dict):
    p = f.get("properties", {})
    lon, lat = f["geometry"]["coordinates"][:2]
    town = p.get("district") or p.get("city") or ""
    if p.get("city") and p.get("city") != "New Haven":
        town = p["city"]
    area = ", ".join(x for x in (town, STATE_CODES.get(p.get("state", ""), p.get("state", ""))) if x)
    if p.get("name"):
        street = f"{p['housenumber']} {p['street']}" if p.get("housenumber") and p.get("street") else p.get("street")
        secondary = ", ".join(x for x in (street, area) if x)
        kind = "street" if p.get("osm_key") == "highway" else "place"
        return _item(p["name"], secondary, lat, lon, kind, "🛣" if kind == "street" else "📍")
    if p.get("street"):
        main = f"{p['housenumber']} {p['street']}" if p.get("housenumber") else p["street"]
        return _item(main, area, lat, lon, "address")
    return None


@lru_cache(maxsize=512)
def _photon(text: str) -> tuple:
    try:
        resp = requests.get(PHOTON_URL, timeout=4, headers={"User-Agent": USER_AGENT}, params={
            "q": text, "limit": 10, "lat": MAP_CENTER[0], "lon": MAP_CENTER[1]})  # near New Haven first
        resp.raise_for_status()
        features = [f for f in resp.json().get("features", [])
                    if f.get("properties", {}).get("countrycode", "").upper() == "US"]  # United States only
    except (requests.RequestException, ValueError):
        return ()
    return tuple(i for i in (_photon_item(f) for f in features) if i)


def _local(text: str) -> list[dict]:
    key = _norm(text)
    out = [_item(name, "Landmark", lat, lon, "landmark", "★", label=name)
           for name, (lat, lon) in LANDMARKS.items() if key in _norm(name)]
    places, _ = load_places(False)
    for p in places:
        if p.get("name") and not p.get("unnamed") and key in _norm(p["name"]):
            out.append(_item(p["name"], p["category"].title(), p["lat"], p["lon"], p["category"],
                             CATEGORY_ICONS.get(p["category"], "📍"), label=p["name"]))
    return out


def suggest(text: str, limit: int = 6, allow_online: bool = True) -> list[dict]:
    """Suggestions for a partly typed location, nearest-to-useful first, inside the map area.

    Each item: {main, secondary, label, lat, lon, kind, icon}.
    """
    text = strip_unit((text or "").strip())
    if len(_norm(text)) < 3:
        return []
    looks_like_address = bool(re.match(r"^\d+\s+\w{2,}", text))
    online: list[dict] = []
    if allow_online:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            census = pool.submit(_census, text) if looks_like_address else None
            photon = pool.submit(_photon, text)
            online = list(census.result() if census else ()) + list(photon.result())
    local = _local(text)
    ordered = online + local if looks_like_address else local[:3] + online + local[3:]
    seen, out = set(), []
    for item in ordered:
        key = (_norm(item["main"]), round(item["lat"], 3), round(item["lon"], 3))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out
