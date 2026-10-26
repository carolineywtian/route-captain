"""Tests for the deterministic tools. No API key needed."""
from datetime import datetime

import pytest

from route_captain.mapdata import haversine_m, highway_types, load_graph, nearest_node
from route_captain.routing import DISTANCE_TOLERANCE, plan_route, repeat_share, route_coords
from route_captain.safety import score_route

YALE_BOWL = (41.3133, -72.9603)
CROSS_CAMPUS = (41.3104, -72.9273)
EAST_ROCK_SUMMIT = (41.3283, -72.9068)


@pytest.fixture(scope="module")
def graph():
    return load_graph()


def test_highway_types_handles_all_formats():
    assert highway_types("footway") == ["footway"]
    assert highway_types(["steps", "footway"]) == ["steps", "footway"]
    assert highway_types("['steps', 'footway']") == ["steps", "footway"]
    assert highway_types(None) == []


def test_haversine_one_degree_latitude():
    assert haversine_m(41.0, -72.0, 42.0, -72.0) == pytest.approx(111_195, rel=0.01)


@pytest.mark.parametrize("miles", [3, 5, 7])
def test_loop_hits_requested_distance(graph, miles):
    start = nearest_node(graph, *YALE_BOWL)
    route = plan_route(graph, start, miles)
    assert route.nodes[0] == route.nodes[-1] == start
    assert abs(route.miles - miles) <= DISTANCE_TOLERANCE * miles


def test_loop_mostly_avoids_retracing(graph):
    route = plan_route(graph, nearest_node(graph, *CROSS_CAMPUS), 5)
    assert repeat_share(route) < 0.2


def test_point_to_point_ends_at_requested_finish(graph):
    start = nearest_node(graph, *CROSS_CAMPUS)
    finish = nearest_node(graph, *EAST_ROCK_SUMMIT)
    route = plan_route(graph, start, 5, end=finish)
    assert route.nodes[0] == start and route.nodes[-1] == finish
    assert abs(route.miles - 5) <= DISTANCE_TOLERANCE * 5


def test_out_and_back_retraces_exactly(graph):
    route = plan_route(graph, nearest_node(graph, *YALE_BOWL), 4, prefer="out_and_back")
    assert route.nodes == route.nodes[::-1]


def test_route_coords_are_continuous(graph):
    route = plan_route(graph, nearest_node(graph, *CROSS_CAMPUS), 5,
                       end=nearest_node(graph, *EAST_ROCK_SUMMIT))
    coords = route_coords(graph, route.nodes)
    drawn = sum(haversine_m(*a, *b) for a, b in zip(coords, coords[1:]))
    # a segment drawn backwards adds a jump, so the drawn line would be longer than the route
    assert drawn == pytest.approx(route.length_m, rel=0.03)


def test_safety_report_fields(graph):
    route = plan_route(graph, nearest_node(graph, *YALE_BOWL), 5)
    report = score_route(graph, route, datetime(2026, 10, 20, 15, 30))
    assert 0 <= report.score <= 100
    assert sum(report.miles_by_type.values()) == pytest.approx(route.miles, rel=0.02)
    assert report.daylight == "ok"


def test_evening_run_is_flagged_dark(graph):
    route = plan_route(graph, nearest_node(graph, *YALE_BOWL), 5)
    report = score_route(graph, route, datetime(2026, 12, 15, 17, 0))
    assert report.daylight == "dark"
    assert not report.passes
    assert any("dark" in f for f in report.flags)


def test_impossible_out_and_back_falls_back_to_loop(graph):
    from route_captain.routing import route_candidates
    routes = route_candidates(graph, nearest_node(graph, *CROSS_CAMPUS), 13.1, prefer="out_and_back")
    assert routes and routes[0].shape == "loop"
    assert any("beyond the map" in n for n in routes[0].notes)
