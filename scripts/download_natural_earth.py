"""Download Natural Earth coastline and land borders (public domain) and keep
the part around Peru, for the dashboard map frame.

Output: outputs/natural_earth_lines.geojson (columns: kind = coast | border)

Usage:
    python scripts/download_natural_earth.py
"""
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "natural_earth_lines.geojson"
BASE = "https://naciscdn.org/naturalearth/10m"
LAYERS = {"coast": "physical/ne_10m_coastline", "border": "cultural/ne_10m_admin_0_boundary_lines_land"}
BBOX = box(-83.0, -20.0, -67.0, 1.5)


def read_layer(path, tmp):
    name = path.split("/")[1]
    r = requests.get(f"{BASE}/{path}.zip", timeout=120)
    r.raise_for_status()
    z = Path(tmp) / f"{name}.zip"
    z.write_bytes(r.content)
    zipfile.ZipFile(z).extractall(Path(tmp) / name)
    return gpd.read_file(Path(tmp) / name / f"{name}.shp")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for kind, path in LAYERS.items():
            g = read_layer(path, tmp).clip(BBOX)[["geometry"]]
            g["kind"] = kind
            parts.append(g)
    lines = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")
    lines["geometry"] = lines.geometry.simplify(0.01)
    lines.to_file(OUT, driver="GeoJSON")
    print(f"ok: {OUT} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
