"""Adds ground elevation (meters) to every node of the compact map file, once, so
pacing by hills works offline.

Source: Open Topo Data public API, USGS NED 10 m dataset (free, no key; at most
100 points per request and 1 request per second). Progress is saved as it goes, so an
interrupted run resumes where it stopped. Run after scripts/build_compact_graph.py:
    .venv/bin/python scripts/add_elevation.py
"""
import gzip
import json
import time
from pathlib import Path

import requests

GRAPH = Path(__file__).resolve().parent.parent / "sample_data" / "new_haven_graph.json.gz"
URL = "https://api.opentopodata.org/v1/ned10m"
BATCH = 100


def main():
    data = json.loads(gzip.decompress(GRAPH.read_bytes()))
    nodes = data["nodes"]
    todo = [n for n, v in nodes.items() if len(v) < 4 or v[3] is None]
    print(f"{len(todo)} of {len(nodes)} nodes need elevation")
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        locations = "|".join(f"{nodes[n][0]},{nodes[n][1]}" for n in chunk)
        for attempt in range(6):
            resp = requests.post(URL, data={"locations": locations}, timeout=60)
            if resp.status_code == 200:
                break
            time.sleep(min(60, 2 ** (attempt + 1)))  # rate limited or busy: back off and retry
        resp.raise_for_status()
        for n, hit in zip(chunk, resp.json()["results"]):
            elev = hit.get("elevation")
            nodes[n] = nodes[n][:3] + [round(float(elev), 1) if elev is not None else None]
        if (i // BATCH) % 25 == 0:
            print(f"  {i + len(chunk)} / {len(todo)}", flush=True)
            GRAPH.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 9))
        time.sleep(1.05)  # the public API allows one request per second
    data["elevation_source"] = "Open Topo Data, USGS National Elevation Dataset 10 m"
    GRAPH.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 9))
    print(f"done -> {GRAPH.name} ({GRAPH.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
