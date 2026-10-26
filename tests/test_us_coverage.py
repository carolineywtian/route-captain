"""US-wide coverage: building a network from downloaded OpenStreetMap data, the US check,
time zones and the area choice. No network needed (outside calls are faked)."""
import pytest

from route_captain import area as A

FAKE_OSM = {"elements": [
    {"type": "node", "id": 1, "lat": 30.2600, "lon": -97.7500},
    {"type": "node", "id": 2, "lat": 30.2610, "lon": -97.7500},
    {"type": "node", "id": 3, "lat": 30.2620, "lon": -97.7500, "tags": {"highway": "crossing"}},
    {"type": "node", "id": 4, "lat": 30.2630, "lon": -97.7500},
    {"type": "node", "id": 5, "lat": 30.2620, "lon": -97.7490},
    {"type": "way", "id": 10, "nodes": [1, 2, 3, 4], "tags": {"highway": "footway", "name": "Trail"}},
    {"type": "way", "id": 11, "nodes": [3, 5], "tags": {"highway": "residential", "name": "Elm St"}},
    {"type": "way", "id": 12, "nodes": [4, 5], "tags": {"highway": "motorway"}},
]}


def test_build_graph_simplifies_and_keeps_crossings():
    g = A.build_graph(FAKE_OSM)
    assert set(g.nodes) == {1, 3, 4, 5}                 # node 2 is just shape, folded into an edge
    assert g.nodes[3]["highway"] == "crossing"
    edge = min(g.get_edge_data(1, 3).values(), key=lambda d: d["length"])
    assert len(edge["points"]) == 3 and 200 < edge["length"] < 250
    assert edge["cost"] == pytest.approx(edge["length"])  # footways cost no extra


def test_parse_places_reads_nodes_and_areas():
    places = A.parse_places({"elements": [
        {"type": "node", "id": 1, "lat": 30.26, "lon": -97.75, "tags": {"shop": "bakery", "name": "Bread"}},
        {"type": "way", "id": 2, "center": {"lat": 30.27, "lon": -97.74}, "tags": {"leisure": "park"}},
        {"type": "node", "id": 3, "lat": 30.28, "lon": -97.73, "tags": {"amenity": "bank"}},
    ]})
    assert [(p["category"], p["name"]) for p in places] == [("bakery", "Bread"), ("park", None)]


def test_radius_grows_with_distance_but_is_capped():
    assert A.radius_for(3) < A.radius_for(6) <= A.MAX_RADIUS_MI
    assert A.radius_for(26.2) == A.MAX_RADIUS_MI


def test_new_haven_runs_use_the_built_in_map():
    assert A.in_new_haven(41.3104, -72.9273, 3)
    assert not A.in_new_haven(42.3550, -71.0656)               # Boston
    assert A.get_area(41.3104, -72.9273, 5).source == A.new_haven_area().source


def test_us_check_falls_back_to_map_boxes_when_offline(monkeypatch):
    def boom(*a, **k):
        raise A.requests.ConnectionError("offline")
    monkeypatch.setattr(A.requests, "get", boom)
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    A.in_us.cache_clear()
    assert A.in_us(30.2672, -97.7431)       # Austin
    assert A.in_us(21.3069, -157.8583)      # Honolulu
    assert not A.in_us(51.5074, -0.1278)    # London
    A.in_us.cache_clear()


def test_census_failure_falls_back_to_reverse_lookup(monkeypatch):
    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body = status, body
        def json(self):
            return self._body
        def raise_for_status(self):
            pass
    def fake_get(url, **kw):
        if "census" in url:
            return Resp(502, {"errors": ["gateway"]})
        return Resp(200, {"address": {"country_code": "ca"}})   # Toronto
    monkeypatch.setattr(A.requests, "get", fake_get)
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    A.in_us.cache_clear()
    assert not A.in_us(43.6532, -79.3832)
    A.in_us.cache_clear()


def test_new_haven_time_zone_needs_no_network():
    assert A.local_timezone(41.3104, -72.9273) == "America/New_York"


def test_outside_us_address_is_refused(monkeypatch):
    from route_captain import geocode as G
    monkeypatch.setattr(G, "_nominatim", lambda text: {"label": "Toronto, Ontario, Canada", "lat": 43.65,
                                                      "lon": -79.38, "outside_us": True})
    with pytest.raises(G.LocationError, match="outside the United States"):
        G.locate("Toronto, Ontario", allow_online=True)
