"""Download TerraClimate v1.1 monthly fields cropped to Peru.

TerraClimate (Abatzoglou et al., 2026; CC0) is a 4 km monthly climate and
climatic water balance dataset built from WorldClim v2.1 climatologies and
ERA5 anomalies. It also ships "pseudo climate change" versions of the same
record under +2 °C and +4 °C of global warming, which is what this project
uses for the future.

Each variable-year is read through OPeNDAP, cropped to a box around Peru and
written as a compressed NetCDF in data/raw/terraclimate/<scenario>/. Files
already on disk are skipped, so the script can be stopped and resumed.

Usage:
    python scripts/download_terraclimate.py
"""
import sys
import time
from pathlib import Path

import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "terraclimate"
THREDDS = "https://thredds.northwestknowledge.net/thredds/dodsC/TERRACLIMATE_ALL"

# Box around Peru (degrees). TerraClimate latitudes run north to south.
LAT = slice(0.5, -18.6)
LON = slice(-81.5, -68.5)

# ppt: precipitation, pet: Penman-Monteith reference evapotranspiration,
# q: runoff from TerraClimate's monthly water balance model (all in mm/month)
VARIABLES = ["ppt", "pet", "q"]
SCENARIOS = {
    "historical": ("data", "TerraClimate", range(1958, 2026)),
    "plus2C": ("data_plus2C", "TerraClimate_plus2C", range(1991, 2021)),
    "plus4C": ("data_plus4C", "TerraClimate_plus4C", range(1991, 2021)),
}


def download(scenario, folder, prefix, var, year):
    dest = OUT / scenario / f"{var}_{year}.nc"
    if dest.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{THREDDS}/{folder}/{prefix}_{var}_{year}.nc"
    with xr.open_dataset(url) as ds:
        da = ds[var].sel(lat=LAT, lon=LON).load()
    tmp = dest.with_suffix(".part")
    da.to_netcdf(tmp, encoding={var: {"zlib": True, "complevel": 4, "dtype": "float32"}})
    tmp.replace(dest)
    return True


def main():
    jobs = [(s, f, p, v, y) for s, (f, p, years) in SCENARIOS.items()
            for v in VARIABLES for y in years]
    t0 = time.time()
    for i, job in enumerate(jobs, 1):
        for attempt in range(3):
            try:
                new = download(*job)
                break
            except Exception as err:  # the THREDDS server drops connections now and then
                print(f"retry {attempt + 1} {job[0]} {job[3]} {job[4]}: {err}", flush=True)
                time.sleep(10)
        else:
            sys.exit(f"failed: {job}")
        if new:
            print(f"[{i}/{len(jobs)}] {job[0]} {job[3]} {job[4]} "
                  f"({(time.time() - t0) / 60:.1f} min)", flush=True)
    print("done")


if __name__ == "__main__":
    main()
