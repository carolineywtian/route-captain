"""Converts the downloaded OpenStreetMap graph into a small file the app can load
with only networkx (no osmnx / geopandas / shapely at runtime).

Run once after scripts/download_map.py (needs requirements-dev.txt):
    .venv/bin/python scripts/build_compact_graph.py
Map data © OpenStreetMap contributors (ODbL).
"""
import gzip
import json
from pathlib import Path

import networkx as nx
import osmnx as ox

DATA = Path(__file__).resolve().parent.parent / "sample_data"
SRC = DATA / "new_haven_walk.graphml"
OUT = DATA / "new_haven_graph.json.gz"
KEEP = ("highway", "name", "lit", "maxspeed", "surface")


def main():
    graph = ox.convert.to_undirected(ox.load_graphml(SRC))
    drop = [(u, v, k) for u, v, k, d in graph.edges(keys=True, data=True)
            if "motorway" in str(d.get("highway"))]
    graph.remove_edges_from(drop)
    graph = graph.subgraph(max(nx.connected_components(graph), key=len)).copy()

    nodes = {str(n): [round(d["y"], 6), round(d["x"], 6), d.get("highway")]
             for n, d in graph.nodes(data=True)}
    edges = []
    for u, v, d in graph.edges(data=True):
        geom = d.get("geometry")
        # points from u to v as [lat, lon]; stored geometry may run either way
        pts = [[round(y, 6), round(x, 6)] for x, y in geom.coords] if geom is not None else None
        if pts:
            uy, ux = graph.nodes[u]["y"], graph.nodes[u]["x"]
            if (pts[-1][0] - uy) ** 2 + (pts[-1][1] - ux) ** 2 < (pts[0][0] - uy) ** 2 + (pts[0][1] - ux) ** 2:
                pts.reverse()
        attrs = {k: d[k] for k in KEEP if d.get(k) is not None}
        edges.append([u, v, round(float(d["length"]), 2), attrs, pts])

    payload = {"attribution": "Map data © OpenStreetMap contributors (ODbL)",
               "nodes": nodes, "edges": edges}
    OUT.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), 9))
    print(f"{len(nodes)} nodes, {len(edges)} edges -> {OUT.name} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
