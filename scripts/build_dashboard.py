"""Build docs/index.html: a static Plotly dashboard published with GitHub Pages.

It reads only files in outputs/, so every number on the page comes from the
pipeline. A short script in the page switches the map indicator
and the basin shown in the time series.

Usage:
    python scripts/build_dashboard.py
"""
import html
import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
PAGE = ROOT / "docs" / "index.html"
REPO = "https://github.com/Cristhianbgf/peru-basin-drought"
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"

TEXT, TEXT2, GRID, AXIS, WHITE = "#222222", "#555555", "#e6e6e6", "#bbbbbb", "#ffffff"
REGION = {"Pacífico": "Pacific", "Amazonas": "Amazon", "Titicaca": "Titicaca"}
REGION_COLOR = {"Pacific": "#0072B2", "Amazon": "#009E73", "Titicaca": "#D55E00"}
CONFIG = {"displaylogo": False, "responsive": True,
          "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"]}

# map indicator: (column, label, colour scale, zmin, zmax, unit)
MAP = {
    # +2 °C first: the map draws reliably only when the first trace starts visible
    "freq_2": ("severe_drought_freq_plus2C", "Severe drought at +2 °C", "YlOrRd", 0, 50, "% of months"),
    "freq_base": ("severe_drought_freq_baseline", "Severe drought, 1991-2020", "YlOrRd", 0, 50, "% of months"),
    "freq_4": ("severe_drought_freq_plus4C", "Severe drought at +4 °C", "YlOrRd", 0, 50, "% of months"),
    "runoff_2": ("runoff_change_pct_plus2C", "Runoff change at +2 °C", "RdBu", -30, 30, "%"),
    "runoff_4": ("runoff_change_pct_plus4C", "Runoff change at +4 °C", "RdBu", -30, 30, "%"),
    "use": ("log_use", "Licensed consumptive water use", "Blues", 0, 3.5, "log10 hm³/yr"),
}

# the map opens on +2 °C: the 1991-2020 map is ~7 % everywhere by construction
DEFAULT = "freq_2"


def clockwise(geo):
    """Plotly's vector maps (d3-geo) need outer rings clockwise and holes
    counter-clockwise, the opposite of the GeoJSON standard. A ring in the
    wrong direction is drawn as the whole globe minus the basin."""
    def area(ring):  # shoelace: positive = counter-clockwise
        return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:]))

    def fix(poly):
        return [r if (area(r) < 0) == (k == 0) else r[::-1] for k, r in enumerate(poly)]

    for f in geo["features"]:
        g = f["geometry"]
        if g["type"] == "Polygon":
            g["coordinates"] = fix(g["coordinates"])
        elif g["type"] == "MultiPolygon":
            g["coordinates"] = [fix(p) for p in g["coordinates"]]
    return geo


def layout(height, **extra):
    d = dict(height=height, paper_bgcolor=WHITE, plot_bgcolor=WHITE,
             font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', size=13, color=TEXT),
             margin=dict(l=12, r=12, t=12, b=12),
             hoverlabel=dict(bgcolor=WHITE, bordercolor=AXIS, font=dict(color=TEXT, size=12)),
             xaxis=dict(gridcolor=GRID, linecolor=AXIS, zeroline=False),
             yaxis=dict(gridcolor=GRID, linecolor=AXIS, zeroline=False))
    d.update(extra)
    return d


def main():
    ind = pd.read_csv(OUT / "basin_indicators.csv", dtype={"unit_code": str})
    ind["region"] = ind.drainage.str.extract(r"(Pacífico|Amazonas|Titicaca)")[0].map(REGION)
    for c in ind.columns:
        if c.startswith("severe_drought_freq"):
            ind[c] = 100 * ind[c]
    ind["log_use"] = np.log10(ind.consumptive_use_hm3.clip(lower=1))
    geo = clockwise(json.loads((OUT / "basins_web.geojson").read_text(encoding="utf-8")))
    spei = pd.read_parquet(OUT / "basin_spei_monthly.parquet")
    val = pd.read_csv(OUT / "validation_events.csv")
    order = ind.sort_values("unit_name").unit_code.tolist()
    name = ind.set_index("unit_code").unit_name

    # ---- map: one trace per indicator, toggled from the page
    fig = go.Figure()
    for i, (key, (col, label, scale, zmin, zmax, unit)) in enumerate(MAP.items()):
        hover = ("<b>%{customdata[0]}</b><br>" + label + ": %{customdata[1]}<br>"
                 "Region: %{customdata[2]}<extra></extra>")
        shown = ind[col].round(1).astype(str).replace("nan", "n/a (too little runoff)")
        if key == "use":
            shown = ind.consumptive_use_hm3.round(1).astype(str) + " hm³/yr"
        fig.add_trace(go.Choropleth(
            geojson=geo, featureidkey="properties.unit_code", locations=ind.unit_code,
            z=ind[col], colorscale=scale, zmin=zmin, zmax=zmax,
            marker=dict(line=dict(width=0.4, color="#ffffff")),
            customdata=np.stack([ind.unit_name, shown, ind.region], axis=1),
            hovertemplate=hover, visible=(key == DEFAULT),
            colorbar=dict(title=dict(text=unit, font=dict(color=TEXT2)), thickness=12, len=0.6)))
    # vector map without web tiles: it draws at once and also works offline
    fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator",
                    showcountries=True, countrycolor="#cccccc", showland=True, landcolor="#f2f2f2",
                    showocean=True, oceancolor="#e8eef3")
    fig.update_layout(**layout(620))
    map_visible = {k: [j == i for j in range(len(MAP))] for i, k in enumerate(MAP)}

    # ---- priority matrix
    mat = go.Figure()
    for r, g in ind[~ind.hyper_arid].groupby("region"):
        mat.add_trace(go.Scatter(
            x=g.consumptive_use_hm3.clip(lower=0.1), y=g.severe_drought_freq_plus2C, mode="markers",
            name=r, marker=dict(color=REGION_COLOR[r], size=8 + 18 * np.sqrt(g.use_mining_hm3 / max(ind.use_mining_hm3.max(), 1)),
                                line=dict(width=g.priority.map({True: 2, False: 0.5}), color=TEXT), opacity=0.8),
            customdata=np.stack([g.unit_name, g.severe_drought_freq_baseline.round(1),
                                 g.use_mining_hm3.round(1)], axis=1),
            hovertemplate=("<b>%{customdata[0]}</b><br>Consumptive use: %{x:.1f} hm³/yr"
                           "<br>Severe drought at +2 °C: %{y:.1f} % (today %{customdata[1]} %)"
                           "<br>Mining: %{customdata[2]} hm³/yr<extra></extra>")))
    q = ind.consumptive_use_hm3.quantile(2 / 3)
    mat.add_vline(x=q, line=dict(color=AXIS, dash="dash"))
    mat.update_layout(**layout(460), legend=dict(orientation="h", y=-0.15))
    mat.update_xaxes(type="log", title=dict(text="Licensed consumptive water use (hm³/yr, log scale)", font=dict(color=TEXT2)))
    mat.update_yaxes(title=dict(text="Months in severe drought at +2 °C (%)", font=dict(color=TEXT2)), rangemode="tozero")

    # ---- SPEI-12 series for one basin at a time
    # one bar trace; the page swaps its values when another basin is chosen
    wide = (spei.pivot(index="time", columns="unit_code", values="spei12")
            .loc["1959-01-01":].round(2))
    first = wide[order[0]]
    ser = go.Figure(go.Bar(x=wide.index, y=first, marker_line_width=0,
                           marker_color=np.where(first < 0, "#D55E00", "#0072B2"),
                           hovertemplate="%{x|%b %Y}<br>SPEI-12: %{y}<extra></extra>"))
    for v in (-1.5, 1.5):
        ser.add_hline(y=v, line=dict(color=TEXT2, dash="dot", width=1))
    ser.update_layout(**layout(380), showlegend=False, bargap=0)
    ser.update_yaxes(title=dict(text="SPEI-12", font=dict(color=TEXT2)), range=[-3.2, 3.2])
    serie_values = {u: [None if pd.isna(v) else float(v) for v in wide[u]] for u in order}

    blocks = {k: f.to_html(full_html=False, include_plotlyjs=False, div_id=f"fig-{k}", config=CONFIG)
              for k, f in {"map": fig, "matrix": mat, "series": ser}.items()}

    pri = ind[ind.priority].sort_values("consumptive_use_hm3", ascending=False)
    rows = "".join(
        f"<tr><td>{html.escape(r.unit_name)}</td><td>{r.region}</td>"
        f'<td class="num">{r.consumptive_use_hm3:,.0f}</td><td class="num">{r.use_mining_hm3:,.1f}</td>'
        f'<td class="num">{r.severe_drought_freq_baseline:.0f}</td><td class="num">{r.severe_drought_freq_plus2C:.0f}</td>'
        f'<td class="num">{r.severe_drought_freq_plus4C:.0f}</td></tr>' for r in pri.itertuples())
    val_rows = "".join(
        f"<tr><td>{r.region}</td><td>{html.escape(r.event)}</td><td>{html.escape(r.reference)}</td>"
        f'<td class="num">{r.min_spei12:.2f}</td><td>{r.month}</td><td class="num">{r.percentile_rank:.0f}</td></tr>'
        for r in val.itertuples())

    ok = ~ind.hyper_arid
    cards = [
        ("Basins", f"{len(ind)}", "ANA hydrographic units covering Peru"),
        ("Severe drought today", f"{ind.loc[ok, 'severe_drought_freq_baseline'].median():.1f} %",
         "median share of months with SPEI-12 ≤ −1.5, 1991-2020"),
        ("At +2 °C", f"{ind.loc[ok, 'severe_drought_freq_plus2C'].median():.1f} %",
         f"median; {int((ind.loc[ok, 'severe_drought_freq_plus2C'] >= 2 * ind.loc[ok, 'severe_drought_freq_baseline']).sum())} basins at least double"),
        ("Priority basins", f"{int(ind.priority.sum())}",
         "high licensed demand and severe droughts at least twice as frequent at +2 °C"),
    ]
    cards_html = "".join(f'<div class="card"><div class="card-title">{k}</div><div class="card-value">{v}</div>'
                         f'<div class="card-text">{html.escape(d)}</div></div>' for k, v, d in cards)
    buttons = "".join(f'<button data-k="{k}" aria-pressed="{str(k == DEFAULT).lower()}">{v[1]}</button>'
                      for k, v in MAP.items())
    options = "".join(f'<option value="{u}">{html.escape(name[u])}</option>' for u in order)
    data_js = json.dumps({"map": map_visible, "series": serie_values}, separators=(",", ":"))

    page = TEMPLATE.format(plotly=PLOTLY_CDN, repo=REPO, cards=cards_html, buttons=buttons,
                           options=options, priority_rows=rows, validation_rows=val_rows,
                           data_js=data_js, q=f"{q:,.0f}", **{f"fig_{k}": v for k, v in blocks.items()})
    PAGE.parent.mkdir(exist_ok=True)
    PAGE.write_text(page, encoding="utf-8")
    print(f"ok: {PAGE} ({PAGE.stat().st_size / 1e6:.1f} MB)")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Drought and water demand in Peru's river basins</title>
<meta name="description" content="SPEI drought hazard for Peru's 231 hydrographic units, its change at +2 °C and +4 °C of global warming, and licensed water use by sector.">
<script src="{plotly}" charset="utf-8"></script>
<style>
:root {{ color-scheme: light; --bg: #f5f5f5; --panel: #ffffff; --text: #222222; --text2: #555555;
  --line: #e6e6e6; --border: #dddddd; --link: #0072B2; }}
* {{ box-sizing: border-box; }}
html, body {{ background: var(--bg); }}
body {{ margin: 0; color: var(--text); font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 32px 16px 48px; }}
h1 {{ font-size: 30px; line-height: 1.2; margin: 0 0 8px; }}
h2 {{ font-size: 20px; margin: 0 0 4px; }}
p {{ margin: 0 0 12px; }}
a {{ color: var(--link); }}
.lead {{ color: var(--text2); max-width: 800px; font-size: 17px; }}
.cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 20px 0 8px; }}
.card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }}
.card-title {{ color: var(--text2); font-size: 13px; }}
.card-value {{ font-size: 26px; font-weight: 600; margin: 2px 0; }}
.card-text {{ color: var(--text2); font-size: 13px; line-height: 1.4; }}
.panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 18px 18px 8px; margin: 16px 0; }}
.note {{ color: var(--text2); font-size: 14px; }}
.buttons {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 10px; }}
.buttons button {{ font: inherit; font-size: 13px; border: 1px solid var(--border); background: var(--panel);
  color: var(--text); padding: 5px 12px; border-radius: 6px; cursor: pointer; }}
.buttons button[aria-pressed="true"] {{ background: var(--text); color: #fff; border-color: var(--text); }}
select {{ font: inherit; font-size: 14px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px;
  background: var(--panel); color: var(--text); max-width: 100%; }}
.table {{ max-height: 420px; overflow: auto; border: 1px solid var(--border); border-radius: 6px; margin-top: 10px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; background: var(--panel); }}
th, td {{ padding: 6px 10px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }}
th {{ position: sticky; top: 0; background: #f0f0f0; color: var(--text2); font-weight: 600; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.method {{ color: var(--text2); font-size: 15px; max-width: 860px; }}
.method h2 {{ color: var(--text); margin-top: 28px; }}
footer {{ border-top: 1px solid var(--border); margin-top: 32px; padding-top: 16px; color: #888; font-size: 14px; }}
@media (max-width: 860px) {{ .cards {{ grid-template-columns: 1fr 1fr; }} h1 {{ font-size: 25px; }} }}
</style>
</head>
<body>
<main>
<h1>Drought and water demand in Peru's river basins</h1>
<p class="lead">How often each of Peru's 231 hydrographic units is in severe drought today, how that changes
with +2 °C and +4 °C of global warming, and how much water is licensed in each basin, by sector.
Drought is measured with SPEI, which counts both rainfall and the evaporative demand of the atmosphere.</p>

<div class="cards">{cards}</div>

<div class="panel">
  <h2>Map by basin</h2>
  <div class="buttons" id="indicator">{buttons}</div>
  <p class="note">Severe drought: SPEI-12 at or below −1.5. Future SPEI uses the distribution fitted to
  1991-2020, so it reads as "as dry as a severe drought of today's climate". Runoff change is not shown
  for basins with under 20 mm of runoff per year.</p>
  {fig_map}
</div>

<div class="panel">
  <h2>Where demand and drought meet</h2>
  <p class="note">Each point is a basin (hyper-arid coastal units excluded). Right of the dashed line: the third of
  basins with the most licensed consumptive water use (≥ {q} hm³/yr). Point size: water licensed to mining.
  Thick outline: priority basins.</p>
  {fig_matrix}
</div>

<div class="panel">
  <h2>SPEI-12 month by month, 1959-2025</h2>
  <p><label for="unit">Basin</label> <select id="unit">{options}</select></p>
  {fig_series}
</div>

<div class="panel">
  <h2>Priority basins</h2>
  <p class="note">High licensed demand and severe droughts at least twice as frequent at +2 °C as in 1991-2020.</p>
  <div class="table"><table>
    <thead><tr><th>Basin</th><th>Region</th><th>Consumptive use (hm³/yr)</th><th>Mining (hm³/yr)</th>
    <th>Severe drought today (%)</th><th>at +2 °C (%)</th><th>at +4 °C (%)</th></tr></thead>
    <tbody>{priority_rows}</tbody>
  </table></div>
</div>

<div class="panel">
  <h2>Does SPEI catch documented droughts?</h2>
  <p class="note">Area-weighted SPEI-12 of each drainage region during droughts described in the literature.
  Rank: share of all months 1959-2025 that were drier (lower is drier).</p>
  <div class="table"><table>
    <thead><tr><th>Region</th><th>Event</th><th>Reference</th><th>Lowest SPEI-12</th><th>Month</th><th>Drier months (%)</th></tr></thead>
    <tbody>{validation_rows}</tbody>
  </table></div>
</div>

<section class="method">
  <h2>Data and method</h2>
  <p>Climate: TerraClimate v1.1 (Abatzoglou et al., 2026; CC0), monthly at about 4 km, 1958-2025, and its
  +2 °C and +4 °C pseudo-warming versions. Evaporative demand is Penman-Monteith reference evapotranspiration.
  Basins and water rights: National Water Authority (ANA): 231 hydrographic units and the water use rights in
  force, summed per basin and sector. Hydropower, aquaculture and fishing are not counted as consumptive.</p>
  <p>SPEI follows Vicente-Serrano et al. (2010) with a log-logistic distribution fitted per basin and calendar
  month on 1991-2020 (xclim).</p>
  <h2>Limitations</h2>
  <p>The +2 °C and +4 °C series apply one pattern of change to the observed record: they do not show the spread
  between climate models. TerraClimate runoff comes from a simple water balance without glacier melt or inflow from
  upstream basins and is well below gauged flows in Pacific basins, so it is used only as a relative change, never as
  available water. Licensed volume is the volume a right allows, not the volume used.</p>
</section>

<footer>Cristhian B. Gamarra Fuentes · <a href="{repo}">code on GitHub</a> ·
contains data from TerraClimate (CC0) and Peru's National Water Authority (ANA).</footer>
</main>
<script>
const DATA = {data_js};
document.querySelectorAll("#indicator button").forEach(b => b.addEventListener("click", () => {{
  document.querySelectorAll("#indicator button").forEach(x => x.setAttribute("aria-pressed", x === b));
  Plotly.restyle("fig-map", {{visible: DATA.map[b.dataset.k]}});
}}));
document.getElementById("unit").addEventListener("change", ev => {{
  const y = DATA.series[ev.target.value];
  Plotly.restyle("fig-series", {{y: [y], "marker.color": [y.map(v => v < 0 ? "#D55E00" : "#0072B2")]}});
}});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
