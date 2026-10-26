"""Downloads New Haven's places (stops and "surprise me" destinations) from OpenStreetMap.

    .venv/bin/python scripts/download_places.py
Saves raw Overpass JSON to sample_data/new_haven_places.json.gz (© OpenStreetMap contributors, ODbL).
"""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from route_captain.area import NEW_HAVEN_CENTER, PLACE_TAGS, _bbox, _overpass  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "sample_data" / "new_haven_places.json.gz"

s, w, n, e = _bbox([NEW_HAVEN_CENTER], 5000 / 1609.344)
bbox = f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}"
query = "".join(f'nwr["{k}"="{v}"]({bbox});' for (k, v) in PLACE_TAGS)
data = _overpass(f"[out:json][timeout:90];({query});out center tags;")
OUT.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 9))
print(f"{len(data.get('elements', []))} places -> {OUT.name}")
