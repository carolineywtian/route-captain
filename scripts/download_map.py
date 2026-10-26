"""One-time download of the OpenStreetMap snapshot used by Route Captain.

Saves the walkable street/path graph and a places layer (water, restrooms,
parks, cafés, bakeries) for the Yale / New Haven area into sample_data/, so
the app runs offline. Map data © OpenStreetMap contributors (ODbL).
"""
from pathlib import Path

import osmnx as ox

CENTER = (41.3111, -72.9267)  # Yale campus
RADIUS_M = 5000
OUT = Path(__file__).resolve().parent.parent / "sample_data"

PLACE_TAGS = {
    "amenity": ["cafe", "drinking_water", "toilets", "restaurant", "fast_food"],
    "shop": ["bakery", "coffee"],
    "leisure": ["park", "track"],
}


OVERPASS_MIRRORS = [
    "https://overpass.private.coffee/api",
    "https://overpass.kumi.systems/api",
    "https://overpass-api.de/api",
]


def main():
    OUT.mkdir(exist_ok=True)
    ox.settings.useful_tags_way = list(ox.settings.useful_tags_way) + [
        "sidewalk", "sidewalk:left", "sidewalk:right", "sidewalk:both",
        "lit", "maxspeed", "surface", "foot", "crossing",
    ]
    for url in OVERPASS_MIRRORS:
        ox.settings.overpass_url = url
        try:
            graph = ox.graph_from_point(CENTER, dist=RADIUS_M, network_type="walk", simplify=True)
            print(f"downloaded from {url}")
            break
        except Exception as exc:  # mirror busy or down, try the next one
            print(f"{url} failed: {exc.__class__.__name__}")
    else:
        raise SystemExit("all Overpass mirrors failed; retry later")
    ox.save_graphml(graph, OUT / "new_haven_walk.graphml")
    print(f"graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges")

    print("Places: run scripts/download_places.py")


if __name__ == "__main__":
    main()
