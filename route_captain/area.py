"""The map area a run is planned on.

New Haven ships with the app (instant, works offline). Anywhere else in the United States,
the walking network and places around the run are downloaded once from OpenStreetMap
(Overpass API, free, no key), simplified into the same kind of graph, and cached on disk.

Map data © OpenStreetMap contributors (ODbL).
"""
from __future__ import annotations

import gzip
import json
import math
import time
from dataclasses import dataclass, field
from functools import lru_cache

import networkx as nx
import requests

from .guard import screen_place
from .mapdata import DATA_DIR, edge_penalty, haversine_m, load_graph

NEW_HAVEN_CENTER = (41.3111, -72.9267)
NEW_HAVEN_RADIUS_M = 4800         # stay a little inside the 5 km snapshot
AREA_CACHE = DATA_DIR / ".areas"
OVERPASS_MIRRORS = ["https://overpass-api.de/api/interpreter",
                    "https://overpass.private.coffee/api/interpreter",
                    "https://overpass.kumi.systems/api/interpreter"]
USER_AGENT = "RouteCaptain/0.1 (Yale AI Foundations student project)"
MILE = 1609.344
MAX_RADIUS_MI = 4.0               # bigger areas get slow to download and route

# walking network, like osmnx's "walk" filter: no motorways, private ways or foot=no
WAY_FILTER = ('["highway"]["highway"!~"motorway|motorway_link|bus_guideway|escape|raceway|proposed|construction|'
              'abandoned|platform|corridor"]["foot"!~"no"]["access"!~"private|no"]["service"!~"private"]')
PLACE_TAGS = {("shop", "bakery"): "bakery", ("amenity", "cafe"): "cafe", ("shop", "coffee"): "cafe",
              ("amenity", "drinking_water"): "water", ("amenity", "toilets"): "restroom",
              ("leisure", "park"): "park", ("leisure", "track"): "track",
              ("amenity", "restaurant"): "restaurant", ("amenity", "fast_food"): "restaurant",
              # destinations for "surprise me"
              ("tourism", "viewpoint"): "viewpoint", ("tourism", "artwork"): "artwork",
              ("tourism", "museum"): "museum", ("tourism", "attraction"): "attraction",
              ("historic", "monument"): "historic", ("historic", "castle"): "historic",
              ("leisure", "garden"): "garden", ("amenity", "ice_cream"): "ice_cream",
              ("shop", "books"): "bookstore", ("amenity", "library"): "library",
              ("natural", "beach"): "waterfront", ("leisure", "marina"): "waterfront"}


class AreaError(ValueError):
    pass


@dataclass
class Area:
    graph: nx.MultiGraph
    places: list[dict]
    quarantined: list[dict] = field(default_factory=list)
    source: str = "New Haven (built in)"
    has_elevation: bool = True


# ---------------------------------------------------------------- which area

def radius_for(miles: float, out_and_back: bool = False) -> float:
    """How far from the start (miles) a run of this length can reach."""
    reach = miles / 2 if out_and_back else miles * 0.42
    return max(1.0, min(MAX_RADIUS_MI, reach + 0.3))


DOWNLOAD_MARGIN_MI = 0.75  # extra ground per download, so nearby follow-up runs reuse it


def in_new_haven(lat: float, lon: float, radius_mi: float = 0.0) -> bool:
    d = float(haversine_m(NEW_HAVEN_CENTER[0], NEW_HAVEN_CENTER[1], lat, lon))
    return d + min(radius_mi * MILE * 0.5, 1500) <= NEW_HAVEN_RADIUS_M


@lru_cache(maxsize=1)
def new_haven_area() -> Area:
    from .places import load_places
    places, quarantined = load_places(False)
    return Area(load_graph(), places, quarantined)


def get_area(lat: float, lon: float, miles: float, end: tuple[float, float] | None = None,
             out_and_back: bool = False, include_attacks: bool = False) -> Area:
    """The area for a run starting at (lat, lon), optionally finishing at `end`."""
    radius = radius_for(miles, out_and_back)
    points = [(lat, lon)] + ([end] if end else [])
    if all(in_new_haven(a, b, radius) for a, b in points):
        if include_attacks:
            from .places import load_places
            places, quarantined = load_places(True)
            base = new_haven_area()
            return Area(base.graph, places, quarantined, base.source)
        return new_haven_area()
    return download_area(points, radius)


def _download_bbox(points, radius_mi):
    return _bbox(points, radius_mi + DOWNLOAD_MARGIN_MI)


# ---------------------------------------------------------------- download and build

def _bbox(points, radius_mi: float) -> tuple[float, float, float, float]:
    lats, lons = [p[0] for p in points], [p[1] for p in points]
    dlat = radius_mi * MILE / 111_320
    dlon = radius_mi * MILE / (111_320 * math.cos(math.radians(sum(lats) / len(lats))))
    return min(lats) - dlat, min(lons) - dlon, max(lats) + dlat, max(lons) + dlon


def _overpass(query: str) -> dict:
    last = None
    for url in OVERPASS_MIRRORS:
        for attempt in range(2):
            try:
                resp = requests.post(url, data={"data": query}, timeout=120, headers={"User-Agent": USER_AGENT})
                if resp.status_code == 200:
                    return resp.json()
                last = f"HTTP {resp.status_code}"
            except (requests.RequestException, ValueError) as exc:
                last = type(exc).__name__
            time.sleep(2 * (attempt + 1))
    raise AreaError(f"Couldn't download the streets for this area right now ({last}). "
                    "The free OpenStreetMap service may be busy; try again in a minute.")


_LOADED: dict[str, Area] = {}


def _covering_cache(s: float, w: float, n: float, e: float):
    """A saved area whose box already contains this one (so nothing new is downloaded)."""
    if not AREA_CACHE.exists():
        return None
    for path in AREA_CACHE.glob("*.json.gz"):
        try:
            cs, cw, cn, ce = (float(x) for x in path.name[:-len(".json.gz")].split("_"))
        except ValueError:
            continue
        if cs <= s and cw <= w and cn >= n and ce >= e:
            return path
    return None


def download_area(points, radius_mi: float) -> Area:
    s, w, n, e = _bbox(points, radius_mi)
    AREA_CACHE.mkdir(parents=True, exist_ok=True)
    cache = _covering_cache(s, w, n, e)
    if cache is not None and cache.name in _LOADED:
        return _LOADED[cache.name]
    if cache is not None:
        data = json.loads(gzip.decompress(cache.read_bytes()))
    else:
        s, w, n, e = _download_bbox(points, min(radius_mi, MAX_RADIUS_MI))
        cache = AREA_CACHE / f"{s:.4f}_{w:.4f}_{n:.4f}_{e:.4f}.json.gz"
        bbox = f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}"
        streets = _overpass(f"[out:json][timeout:110];way{WAY_FILTER}({bbox});(._;>;);out body;")
        place_query = "".join(f'nwr["{k}"="{v}"]({bbox});' for (k, v) in PLACE_TAGS)
        places = _overpass(f"[out:json][timeout:60];({place_query});out center tags;")
        data = {"streets": streets, "places": places}
        cache.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 6))
    graph = build_graph(data["streets"])
    if graph.number_of_nodes() < 50:
        raise AreaError("There aren't enough mapped streets or paths here to plan a run.")
    safe, quarantined = [], []
    for p in parse_places(data["places"]):
        cleaned, reason = screen_place(p)
        if cleaned:
            safe.append(cleaned)
        else:
            quarantined.append({**{k: p.get(k) for k in ("id", "name", "category")}, "reason": reason})
    area = Area(graph, safe, quarantined, "OpenStreetMap (downloaded for this area)", has_elevation=False)
    _LOADED[cache.name] = area
    return area


def build_graph(osm: dict) -> nx.MultiGraph:
    """Simplified, undirected walking graph from raw Overpass JSON: chains of untagged
    nodes become one edge whose `points` keep the street's shape."""
    nodes = {el["id"]: el for el in osm.get("elements", []) if el["type"] == "node"}
    ways = [el for el in osm.get("elements", []) if el["type"] == "way" and len(el.get("nodes", [])) > 1]
    use = {}
    for way in ways:
        for nid in way["nodes"]:
            use[nid] = use.get(nid, 0) + 1
    graph = nx.MultiGraph()
    for way in ways:
        refs = [r for r in way["nodes"] if r in nodes]
        if len(refs) < 2:
            continue
        tags = way.get("tags", {})
        attrs = {k: tags[k] for k in ("highway", "name", "lit", "maxspeed", "surface") if k in tags}
        # keep: ends of the way, shared nodes (intersections) and tagged nodes (crossings, signals)
        keep = {refs[0], refs[-1]} | {r for r in refs if use.get(r, 0) > 1 or nodes[r].get("tags", {}).get("highway")}
        start = 0
        for i in range(1, len(refs)):
            if refs[i] not in keep:
                continue
            seg = refs[start:i + 1]
            pts = [[round(nodes[r]["lat"], 6), round(nodes[r]["lon"], 6)] for r in seg]
            length = sum(float(haversine_m(*a, *b)) for a, b in zip(pts, pts[1:]))
            if length > 0:
                for r in (seg[0], seg[-1]):
                    if r not in graph:
                        tag = nodes[r].get("tags", {}).get("highway")
                        graph.add_node(r, y=nodes[r]["lat"], x=nodes[r]["lon"], **({"highway": tag} if tag else {}))
                edge = dict(attrs, length=round(length, 2), points=pts)
                edge["cost"] = edge["length"] * edge_penalty(edge)
                graph.add_edge(seg[0], seg[-1], **edge)
            start = i
    if graph.number_of_nodes() == 0:
        return graph
    return graph.subgraph(max(nx.connected_components(graph), key=len)).copy()


def parse_places(osm: dict) -> list[dict]:
    out = []
    for el in osm.get("elements", []):
        tags = el.get("tags", {})
        category = next((c for (k, v), c in PLACE_TAGS.items() if tags.get(k) == v), None)
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if category and lat is not None:
            out.append({"id": f"{el['type']}/{el['id']}", "name": tags.get("name"), "category": category,
                        "lat": lat, "lon": lon, "opening_hours": tags.get("opening_hours"),
                        "description": tags.get("description"), "website": tags.get("website")})
    return out


# ---------------------------------------------------------------- United States only

US_BOXES = [  # rough fallback if the Census check is unavailable
    (24.4, -125.0, 49.5, -66.9),     # lower 48
    (51.0, -180.0, 71.6, -129.9),    # Alaska
    (18.8, -160.5, 22.4, -154.7),    # Hawaii
    (17.8, -67.4, 18.6, -65.2),      # Puerto Rico
]


@lru_cache(maxsize=256)
def in_us(lat: float, lon: float) -> bool:
    """Is this point in the United States? Census lookup first (retried), then OpenStreetMap's
    reverse lookup, and only if both are unreachable the rough boxes (which also catch some of
    southern Canada and northern Mexico)."""
    if in_new_haven(lat, lon):
        return True
    for _ in range(2):
        try:
            resp = requests.get("https://geocoding.geo.census.gov/geocoder/geographies/coordinates", timeout=8,
                                headers={"User-Agent": USER_AGENT},
                                params={"x": lon, "y": lat, "benchmark": "Public_AR_Current",
                                        "vintage": "Current_Current", "layers": "States", "format": "json"})
            if resp.status_code == 200 and "result" in resp.json():
                return bool(resp.json()["result"]["geographies"].get("States"))
        except (requests.RequestException, ValueError, KeyError):
            pass
        time.sleep(1)
    try:
        resp = requests.get("https://nominatim.openstreetmap.org/reverse", timeout=8, headers={"User-Agent": USER_AGENT},
                            params={"lat": lat, "lon": lon, "format": "json", "zoom": 3})
        resp.raise_for_status()
        code = (resp.json().get("address") or {}).get("country_code")
        if code:
            return code == "us"
    except (requests.RequestException, ValueError):
        pass
    return any(s <= lat <= n and w <= lon <= e for s, w, n, e in US_BOXES)


@lru_cache(maxsize=256)
def local_timezone(lat: float, lon: float) -> str:
    """IANA time zone for a point (Open-Meteo, free); New Haven's without a network call."""
    if in_new_haven(lat, lon):
        return "America/New_York"
    try:
        resp = requests.get("https://api.open-meteo.com/v1/forecast", timeout=8,
                            params={"latitude": round(lat, 2), "longitude": round(lon, 2),
                                    "timezone": "auto", "forecast_days": 1})
        resp.raise_for_status()
        return resp.json().get("timezone") or "America/New_York"
    except (requests.RequestException, ValueError):
        return "America/New_York"


US_ONLY_MESSAGE = "Route Captain plans runs in the United States. That location is outside the US."
