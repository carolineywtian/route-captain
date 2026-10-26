"""Multiple stops and GPS start. No API key needed."""
import pytest


def test_stops_split_into_main_and_on_the_way():
    from app import _stops
    assert _stops(["bakery", "water"]) == ("bakery", ["water"])
    assert _stops(["water", "restroom"]) == (None, ["water", "restroom"])
    assert _stops(["none"]) == (None, [])
    assert _stops("cafe") == ("cafe", [])


def test_compose_request_with_several_stops():
    from app import compose_request
    text = compose_request(5, "at_stop", ["bakery", "water"], "", "")
    assert text == "5 miles, ending at a bakery, passing a water fountain on the way"
    text = compose_request(4, "at_stop", ["water", "restroom"], "", "")
    assert text == "4 miles, loop, passing a water fountain and a restroom on the way"


def test_finish_question_maps_to_agent_fields():
    from app import compose_request, finish_form
    assert finish_form("at_stop", ["cafe"])["stop_position"] == "end"
    assert finish_form("at_stop", ["water"])["stop_position"] == "midway"   # no main stop -> plain loop
    assert finish_form("out_and_back", ["none"])["shape"] == "out_and_back"
    surprise = finish_form("surprise", ["none"], " street art ")
    assert surprise["surprise"] and surprise["interests"] == "street art"
    assert compose_request(5, "surprise", ["bakery"], "", "", "views") == \
        "5 miles, to a surprise destination I'd enjoy (views), stopping at a bakery on the way"


def test_none_and_stops_exclude_each_other():
    from dash import no_update

    from app import normalize_stops
    assert normalize_stops(["bakery", "none"]) == ["none"]          # picking None clears the others
    assert normalize_stops(["none", "bakery"]) == ["bakery"]        # picking a stop clears None
    assert normalize_stops([]) == ["none"]
    assert normalize_stops(["bakery", "water"]) is no_update


def test_gps_location_text_is_understood():
    from route_captain.geocode import locate
    spot = locate("My location (41.31375, -72.91408)")
    assert spot["label"] == "Your location" and spot["lat"] == pytest.approx(41.31375)


def test_chain_route_passes_through_every_stop():
    from route_captain.agents import _chain
    from route_captain.mapdata import load_graph, nearest_node
    g = load_graph()
    start = nearest_node(g, 41.3104, -72.9273)
    water = nearest_node(g, 41.3125, -72.9225)
    bakery = nearest_node(g, 41.3152, -72.9107)
    route = _chain(g, [start, water, bakery], 5, "point_to_point")
    assert route.nodes[0] == start and route.nodes[-1] == bakery and water in route.nodes
    assert abs(route.miles - 5) <= 0.25


def test_run_mode_payload_matches_route_length():
    from app import _run_payload
    plan = {"coords": [[41.31, -72.93], [41.32, -72.93], [41.32, -72.92]], "miles": 1.5,
            "cues": [{"mile": 0.0, "text": "Start on Elm Street for 0.7 mi"}], "stop": None, "extra_stops": [],
            "briefing": {"headline": "test"}}
    data = _run_payload(plan, 9.5)
    assert data["miles"][0] == 0 and data["miles"][-1] == pytest.approx(1.5) and data["pace"] == 9.5


def test_location_message_clears_when_the_start_changes():
    from dash import no_update

    from app import clear_gps_status
    assert clear_gps_status("My location (41.31635, -72.92234)") == (no_update, no_update)  # keep "Using your location"
    assert clear_gps_status("Cross Campus") == ("", "hint")                               # a picked place clears it
