"""Drought hazard, its change with warming, and licensed water demand per
hydrographic unit.

SPEI (Vicente-Serrano et al., 2010) is computed from the climatic water balance
P - PET with a log-logistic (Fisk) distribution, fitted per unit and calendar
month on 1991-2020. The same fitted distribution is applied to the +2 °C and
+4 °C series, so a future SPEI-12 below -1.5 means "as dry as a severe drought
of today's climate".

Why there is no "available water" ratio: TerraClimate runoff comes from a
simple monthly bucket model without glacier melt or upstream inflow, and in
Pacific basins it is well below gauged flows. Dividing licensed use by it
would give unreliable stress ratios, so demand and hazard are reported side
by side instead, and runoff is used only as a relative change.

Outputs
- outputs/basin_spei_monthly.parquet: SPEI-3 and SPEI-12 per unit and month
  (historical) and SPEI-12 under +2 °C and +4 °C
- outputs/basin_indicators.csv, one row per unit:
    severe_drought_freq_{baseline,plus2C,plus4C}  share of months SPEI-12 <= -1.5
    ppt/pet change (%) and runoff change (%) under +2 °C and +4 °C
    wb_trend_mm_decade, wb_trend_p   Sen's slope and Kendall p of annual P - PET, 1958-2025
    worst_spei12, worst_month        driest month on record (SPEI capped at -3)
    consumptive_use_hm3 and one column per sector (licensed volume, hm³/year)
    hyper_arid                       aridity index P/PET < 0.05
    priority                         high demand and severe droughts at least
                                     twice as frequent at +2 °C

Usage:
    python scripts/compute_indicators.py
"""
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from scipy import stats
from xclim.indices import standardized_precipitation_evapotranspiration_index as spei
from xclim.indices.stats import standardized_index_fit_params

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"
OUT = ROOT / "outputs"
CAL = ("1991-01-01", "2020-12-31")
SEVERE = -1.5
SPEI_CAP = 3.0           # fitted tails give |SPEI| > 3 in dry units; report capped
MIN_RUNOFF_MM = 20.0     # below this the relative runoff change is noise
HYPER_ARID = 0.05        # UNEP aridity index threshold
# hydropower and aquaculture return the water to the river: not consumptive
NON_CONSUMPTIVE = {"energy", "aquaculture", "fishing"}
SCENARIOS = ("plus2C", "plus4C")


def to_array(df, value):
    wide = df.pivot(index="time", columns="unit_code", values=value)
    da = xr.DataArray(wide.values, coords={"time": pd.to_datetime(wide.index),
                                            "unit": wide.columns.values},
                      dims=("time", "unit"))
    da.attrs["units"] = "mm/month"
    return da


def spei_series(wb_hist, wb_scen, window):
    """SPEI of the historical record and of each scenario, all with the
    distribution fitted on the historical 1991-2020 months."""
    # start window-1 months earlier so the first rolling sum in the fit ends in January 1991
    start = pd.Timestamp(CAL[0]) - pd.DateOffset(months=window - 1)
    params = standardized_index_fit_params(
        wb_hist.sel(time=slice(start, CAL[1])), freq=None, window=window,
        dist="fisk", method="ML", zero_inflated=False)
    hist = spei(wb_hist, freq=None, window=window, dist="fisk", method="ML", params=params)
    scen = {k: spei(v, freq=None, window=window, dist="fisk", method="ML", params=params)
            for k, v in wb_scen.items()}
    return hist, scen


def sen_kendall(years, values):
    ok = np.isfinite(values)
    slope = stats.theilslopes(values[ok], years[ok])[0]
    p = stats.kendalltau(years[ok], values[ok]).pvalue
    return slope, p


def compute_spei(df):
    """SPEI table (long format). Slow: one distribution fit per unit and month."""
    by = {s: g for s, g in df.groupby("scenario")}
    wb_hist = to_array(by["historical"], "wb")
    wb_scen = {s: to_array(by[s], "wb") for s in SCENARIOS}
    s12, s12_scen = spei_series(wb_hist, wb_scen, 12)
    s3, _ = spei_series(wb_hist, wb_scen, 3)
    cols = {"spei3": s3, "spei12": s12, **{f"spei12_{s}": v for s, v in s12_scen.items()}}
    table = pd.concat({k: v.clip(-SPEI_CAP, SPEI_CAP).to_pandas().stack() for k, v in cols.items()},
                      axis=1)
    table.index.names = ["time", "unit_code"]
    return table.reset_index()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(PROC / "basin_monthly.parquet")
    df["wb"] = df.ppt - df.pet
    spei_path = OUT / "basin_spei_monthly.parquet"
    if not spei_path.exists():
        compute_spei(df).to_parquet(spei_path, index=False)
    sp = pd.read_parquet(spei_path)
    cal = sp[(sp.time >= CAL[0]) & (sp.time <= CAL[1])]

    units = gpd.read_file(RAW / "ana" / "hydrographic_units.geojson")
    rights = pd.read_csv(RAW / "ana" / "water_rights_by_unit.csv", dtype={"unit_code": str})
    sectors = rights.pivot_table(index="unit_code", columns="sector", values="volume_m3_yr",
                                 aggfunc="sum", fill_value=0) / 1e6
    consumptive = sectors.drop(columns=[c for c in NON_CONSUMPTIVE if c in sectors]).sum(axis=1)

    hist = df[df.scenario == "historical"].set_index(["unit_code", "time"])
    rows = []
    for u, g in sp.groupby("unit_code"):
        h = hist.loc[u]
        annual = h[["ppt", "pet"]].resample("YS").sum()
        slope, p = sen_kendall(annual.index.year.values.astype(float),
                               (annual.ppt - annual.pet).values)
        c = cal[cal.unit_code == u]
        worst = g.set_index("time").spei12.dropna()
        base = h.loc[CAL[0]:CAL[1]]
        row = {"unit_code": u,
               "severe_drought_freq_baseline": float((c.spei12 <= SEVERE).mean()),
               "wb_trend_mm_decade": slope * 10, "wb_trend_p": p,
               "worst_spei12": float(worst.min()), "worst_month": f"{worst.idxmin():%Y-%m}",
               "ppt_mm_yr": base.ppt.sum() / 30, "pet_mm_yr": base.pet.sum() / 30,
               "runoff_mm_yr": base.q.sum() / 30}
        for s in SCENARIOS:
            sc = df[(df.scenario == s) & (df.unit_code == u)].set_index("time")
            row[f"severe_drought_freq_{s}"] = float((c[f"spei12_{s}"] <= SEVERE).mean())
            row[f"ppt_change_pct_{s}"] = 100 * (sc.ppt.sum() / base.ppt.sum() - 1)
            row[f"pet_change_pct_{s}"] = 100 * (sc.pet.sum() / base.pet.sum() - 1)
            row[f"runoff_change_pct_{s}"] = (100 * (sc.q.sum() / base.q.sum() - 1)
                                             if row["runoff_mm_yr"] >= MIN_RUNOFF_MM else np.nan)
        rows.append(row)
    ind = pd.DataFrame(rows)
    ind["hyper_arid"] = ind.ppt_mm_yr / ind.pet_mm_yr < HYPER_ARID
    ind["consumptive_use_hm3"] = ind.unit_code.map(consumptive).fillna(0.0)
    for sector in ("mining", "agriculture", "public_supply", "industry", "energy"):
        if sector in sectors:
            ind[f"use_{sector}_hm3"] = ind.unit_code.map(sectors[sector]).fillna(0.0)
    demand_high = ind.consumptive_use_hm3 >= ind.consumptive_use_hm3.quantile(2 / 3)
    doubling = ind.severe_drought_freq_plus2C >= 2 * ind.severe_drought_freq_baseline
    ind["priority"] = demand_high & doubling & ~ind.hyper_arid
    # light geometry for the web map (~1 km tolerance, enough at country scale)
    web = units[["unit_code", "geometry"]].copy()
    web["geometry"] = web.geometry.simplify(0.01, preserve_topology=True)
    web.to_file(OUT / "basins_web.geojson", driver="GeoJSON")
    ind = units.drop(columns="geometry").merge(ind, on="unit_code")
    ind.to_csv(OUT / "basin_indicators.csv", index=False)
    print(f"{len(ind)} units, {int(ind.priority.sum())} priority, "
          f"{int(ind.hyper_arid.sum())} hyper-arid")


if __name__ == "__main__":
    main()
