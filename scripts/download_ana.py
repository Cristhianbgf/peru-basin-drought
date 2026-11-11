"""Download hydrographic units and licensed water use from Peru's National
Water Authority (ANA).

1. Hydrographic units (unidades hidrográficas, Pfafstetter coding, 1:100 000)
   from the ANA layer published on the national spatial data portal (IDEP).
2. Licensed water use (derechos de uso de agua, DUA) by sector from ANA's
   GeoSNIRH services. The servers aggregate the licensed volume per unit
   (sum of Vol_der, m³/year, and number of rights), so the ~500 000 individual
   rights never need to be downloaded. Rights with no unit code are fetched as
   points and assigned to a unit by location.

Outputs (data/raw/ana/):
    hydrographic_units.geojson
    water_rights_by_unit.csv   one row per unit and sector
    water_rights_unassigned.geojson

Usage:
    python scripts/download_ana.py
"""
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "ana"
UNITS_URL = ("https://www.idep.gob.pe/geoportal/rest/services/INSTITUCIONALES/"
             "ANA_WMS/FeatureServer/7/query")
GEOSNIRH = "https://geosnirh.ana.gob.pe/server/rest/services/P%C3%BAblico"
# GeoSNIRH service name -> sector label used in this project
SECTORS = {
    "DUA_Agrario": "agriculture", "DUA_Poblacional": "public_supply",
    "DUA_DomesticoPoblacional": "domestic", "DUA_Minero": "mining",
    "DUA_Energetico": "energy", "DUA_Indunstrial": "industry",
    "DUA_Pecuario": "livestock", "DUA_Acuicola": "aquaculture",
    "DUA_Pesquero": "fishing", "DUA_Recreativo": "recreation",
    "DUA_Turistico": "tourism", "DUA_DeTransporte": "transport",
    "DUA_OtrosUsos": "other",
}
TIMEOUT = 120


def get_json(url, **params):
    r = requests.get(url, params={**params, "f": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"{url}: {data['error']}")
    return data


def download_units():
    # Boundaries are simplified to ~100 m (maxAllowableOffset), far below the
    # 4 km climate grid; the full-detail layer is ~40 MB and the server fails
    # to return it in one request.
    r = requests.get(UNITS_URL, timeout=TIMEOUT, params={
        "where": "1=1", "outFields": "CODIGO,NOMBRE,NIVEL1,NOMB_UH_N1,AREA_KM2",
        "outSR": 4326, "maxAllowableOffset": 0.001, "f": "geojson"})
    r.raise_for_status()
    units = gpd.read_file(r.text)
    units = units.rename(columns={"CODIGO": "unit_code", "NOMBRE": "unit_name",
                                  "NOMB_UH_N1": "drainage", "AREA_KM2": "area_km2"})
    units = units[["unit_code", "unit_name", "drainage", "area_km2", "geometry"]]
    units.to_file(OUT / "hydrographic_units.geojson", driver="GeoJSON")
    return units


def layer_id(service):
    return get_json(f"{GEOSNIRH}/{service}/MapServer")["layers"][0]["id"]


def rights_by_unit(service, sector):
    url = f"{GEOSNIRH}/{service}/MapServer/{layer_id(service)}/query"
    stats = json.dumps([
        {"statisticType": "sum", "onStatisticField": "Vol_der", "outStatisticFieldName": "volume_m3_yr"},
        {"statisticType": "count", "onStatisticField": "OBJECTID", "outStatisticFieldName": "n_rights"},
    ])
    data = get_json(url, where="vigente=1", outStatistics=stats,
                    groupByFieldsForStatistics="CodigoUH")
    df = pd.DataFrame([f["attributes"] for f in data["features"]])
    # units where every right has a null volume come back as None
    df["volume_m3_yr"] = pd.to_numeric(df["volume_m3_yr"], errors="coerce").fillna(0.0)
    df["sector"] = sector
    return df.rename(columns={"CodigoUH": "unit_code"}), url


def unassigned_points(url, sector):
    """Rights without a unit code, as points (paged)."""
    rows, offset = [], 0
    while True:
        data = get_json(url, where="vigente=1 AND (CodigoUH IS NULL OR CodigoUH='')",
                        outFields="Vol_der", outSR=4326, resultOffset=offset,
                        resultRecordCount=1000, returnGeometry="true")
        feats = data["features"]
        rows += [{"volume_m3_yr": f["attributes"]["Vol_der"],
                  "lon": f["geometry"]["x"], "lat": f["geometry"]["y"]}
                 for f in feats if f.get("geometry")]
        if len(feats) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows, columns=["volume_m3_yr", "lon", "lat"])
    df["sector"] = sector
    return df


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    units = download_units()
    print(f"{len(units)} hydrographic units")
    tables, loose = [], []
    for service, sector in SECTORS.items():
        df, url = rights_by_unit(service, sector)
        tables.append(df)
        loose.append(unassigned_points(url, sector))
        print(f"{sector}: {int(df.n_rights.sum())} rights in force")
    rights = pd.concat(tables, ignore_index=True)

    # assign rights with no unit code by location
    pts = pd.concat(loose, ignore_index=True)
    pts = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts.lon, pts.lat), crs=4326)
    pts = gpd.sjoin(pts, units[["unit_code", "geometry"]], how="left", predicate="within")
    pts.drop(columns="index_right").to_file(OUT / "water_rights_unassigned.geojson", driver="GeoJSON")
    extra = (pts.dropna(subset=["unit_code"])
             .groupby(["unit_code", "sector"], as_index=False)
             .agg(volume_m3_yr=("volume_m3_yr", "sum"), n_rights=("volume_m3_yr", "size")))
    rights = rights[rights.unit_code.fillna("") != ""]
    rights = pd.concat([rights, extra], ignore_index=True)
    rights["volume_m3_yr"] = pd.to_numeric(rights["volume_m3_yr"], errors="coerce").fillna(0.0)
    rights = rights.groupby(["unit_code", "sector"], as_index=False)[
        ["volume_m3_yr", "n_rights"]].sum()
    rights.to_csv(OUT / "water_rights_by_unit.csv", index=False)
    print(f"unassigned rights placed by location: {len(pts.dropna(subset=['unit_code']))} "
          f"of {len(pts)}")


if __name__ == "__main__":
    main()
