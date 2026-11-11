"""Average the TerraClimate grids over each ANA hydrographic unit.

Every 4 km cell whose centre falls inside a unit counts for that unit, weighted
by cos(latitude) so cells keep their true area. Units too small to contain a
cell centre take the cell nearest to their centroid (and are flagged).

Processing goes one year-file at a time as a matrix product
(12 months x cells) @ (cells x units), so memory stays small.

Output: data/processed/basin_monthly.parquet with columns
    scenario, unit_code, time, ppt, pet, q   (mm/month)
and data/processed/unit_cells.csv (cells per unit and the fallback flag).

Usage:
    python scripts/build_basin_series.py
"""
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
VARIABLES = ["ppt", "pet", "q"]
SCENARIOS = ["historical", "plus2C", "plus4C"]


def unit_weights(lat, lon, units):
    """Matrix (cells x units) with cos(lat) weights normalised per unit."""
    lon2, lat2 = np.meshgrid(lon, lat)
    xs, ys = lon2.ravel(), lat2.ravel()
    area = np.cos(np.deg2rad(ys))
    w = np.zeros((xs.size, len(units)), dtype="float64")
    fallback = []
    for j, geom in enumerate(units.geometry):
        inside = shapely.contains_xy(geom, xs, ys)
        if not inside.any():
            c = geom.representative_point()
            inside = np.zeros_like(inside)
            inside[np.argmin((xs - c.x) ** 2 + (ys - c.y) ** 2)] = True
            fallback.append(True)
        else:
            fallback.append(False)
        w[inside, j] = area[inside]
    w /= w.sum(axis=0, keepdims=True)
    cells = pd.DataFrame({"unit_code": units.unit_code.values,
                          "n_cells": (w > 0).sum(axis=0),
                          "nearest_cell_fallback": fallback})
    return w, cells


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    units = gpd.read_file(RAW / "ana" / "hydrographic_units.geojson")
    first = next((RAW / "terraclimate" / "historical").glob("ppt_*.nc"))
    with xr.open_dataset(first) as ds:
        lat, lon = ds.lat.values, ds.lon.values
    w, cells = unit_weights(lat, lon, units)
    cells.to_csv(OUT / "unit_cells.csv", index=False)
    print(f"{len(units)} units, {int(cells.nearest_cell_fallback.sum())} use the nearest cell")

    frames = []
    for scenario in SCENARIOS:
        per_var = {}
        for var in VARIABLES:
            parts = []
            for f in sorted((RAW / "terraclimate" / scenario).glob(f"{var}_*.nc")):
                with xr.open_dataset(f) as ds:
                    da = ds[var].load()
                x = np.nan_to_num(da.values.reshape(da.sizes["time"], -1), nan=0.0)
                # cells with no data (sea) get zero weight after renormalising
                valid = np.isfinite(da.values.reshape(da.sizes["time"], -1))
                num = x @ w
                den = valid.astype("float64") @ w
                parts.append(pd.DataFrame(num / den, index=da.time.values,
                                          columns=units.unit_code.values))
            per_var[var] = pd.concat(parts)
        long = pd.concat({v: df.stack() for v, df in per_var.items()}, axis=1)
        long.index.names = ["time", "unit_code"]
        long = long.reset_index()
        long.insert(0, "scenario", scenario)
        frames.append(long)
        print(f"{scenario}: {long.time.min():%Y-%m} to {long.time.max():%Y-%m}")
    out = pd.concat(frames, ignore_index=True)
    out[VARIABLES] = out[VARIABLES].astype("float32")
    out.to_parquet(OUT / "basin_monthly.parquet", index=False)
    print(f"saved {len(out):,} rows")


if __name__ == "__main__":
    main()
