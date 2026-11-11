"""Check that SPEI-12 picks up droughts documented in the literature.

The basins are grouped by drainage region (Pacific, Amazon, Titicaca) and the
area-weighted mean SPEI-12 of each region is compared with documented events.
The check is the rank of the driest month of each event among all months of
1959-2025 in that region: a documented drought should be among the driest.

Output: outputs/validation_events.csv and outputs/region_spei12.csv
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"

# region, first month, last month, description and reference
EVENTS = [
    ("Amazon", "2005-01", "2005-12", "2005 Amazon drought", "Marengo et al. (2008), J. Climate 21"),
    ("Amazon", "2010-01", "2010-12", "2010 Amazon drought", "Lewis et al. (2011), Science 331"),
    ("Amazon", "2015-06", "2016-06", "2015-16 El Niño drought", "Jiménez-Muñoz et al. (2016), Sci. Rep. 6"),
    ("Amazon", "2023-06", "2024-03", "2023 record drought", "Espinoza et al. (2024), Sci. Rep. 14"),
    ("Titicaca", "1982-10", "1983-09", "1982-83 El Niño, Altiplano", "Garreaud & Aceituno (2001), J. Climate 14"),
    ("Titicaca", "1991-10", "1992-09", "1991-92 El Niño, Altiplano", "Garreaud & Aceituno (2001), J. Climate 14"),
]


def main():
    ind = pd.read_csv(OUT / "basin_indicators.csv", dtype={"unit_code": str})
    spei = pd.read_parquet(OUT / "basin_spei_monthly.parquet")
    region = ind.set_index("unit_code").drainage.str.extract(r"(Pacífico|Amazonas|Titicaca)")[0]
    region = region.map({"Pacífico": "Pacific", "Amazonas": "Amazon", "Titicaca": "Titicaca"})
    area = ind.set_index("unit_code").area_km2
    spei["region"] = spei.unit_code.map(region)
    spei["w"] = spei.unit_code.map(area)
    spei = spei.dropna(subset=["spei12"])
    reg = (spei.assign(x=spei.spei12 * spei.w)
           .groupby(["region", "time"])[["x", "w"]].sum())
    reg = (reg.x / reg.w).rename("spei12").reset_index()
    reg.to_csv(OUT / "region_spei12.csv", index=False)

    rows = []
    for r, a, b, desc, ref in EVENTS:
        s = reg[reg.region == r].set_index("time").spei12
        ev = s.loc[a:b]
        worst = ev.min()
        rows.append({"region": r, "event": desc, "reference": ref,
                     "min_spei12": round(worst, 2), "month": f"{ev.idxmin():%Y-%m}",
                     "percentile_rank": round(100 * (s < worst).mean(), 1)})
    val = pd.DataFrame(rows)
    val.to_csv(OUT / "validation_events.csv", index=False)
    print(val.to_string(index=False))


if __name__ == "__main__":
    main()
