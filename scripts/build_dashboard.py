"""Build docs/index.html: a static Plotly dashboard published with GitHub Pages.

It reads only files in outputs/, so every number on the page comes from the
pipeline. A short script in the page switches the map indicator
and the basin shown in the time series.

The charts follow the look of matplotlib figures (black frame, outside ticks,
dotted grid, bold titles, a map on longitude/latitude axes with coastline and
dashed borders) while staying interactive.

Usage:
    python scripts/build_dashboard.py
"""
import html
import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.colors import get_colorscale, sample_colorscale

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
PAGE = ROOT / "docs" / "index.html"
REPO = "https://github.com/Cristhianbgf/peru-basin-drought"
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"

TEXT, WHITE = "#222222", "#ffffff"
FONT = '"DejaVu Sans", Verdana, Arial, sans-serif'
REGION = {"Pacífico": "Pacific", "Amazonas": "Amazon", "Titicaca": "Titicaca"}
REGION_COLOR = {"Pacific": "#1f77b4", "Amazon": "#2ca02c", "Titicaca": "#ff7f0e"}
WET, DRY, NODATA = "#4682b4", "#ff7f50", "#d9d9d9"
CONFIG = {"displaylogo": False, "responsive": True,
          "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"]}

# map indicator: (column, button label, colour scale, zmin, zmax, colour bar title, map title)
MAP = {
    "freq_2": ("severe_drought_freq_plus2C", "Severe drought at +2 °C", "YlOrRd", 0, 50,
               "Months in severe drought (%)", "Severe drought frequency at +2 °C of global warming"),
    "freq_base": ("severe_drought_freq_baseline", "Severe drought, 1991-2020", "YlOrRd", 0, 50,
                  "Months in severe drought (%)", "Severe drought frequency, 1991–2020"),
    "freq_4": ("severe_drought_freq_plus4C", "Severe drought at +4 °C", "YlOrRd", 0, 50,
               "Months in severe drought (%)", "Severe drought frequency at +4 °C of global warming"),
    "runoff_2": ("runoff_change_pct_plus2C", "Runoff change at +2 °C", "RdBu", -30, 30,
                 "Runoff change (%)", "Change in mean runoff at +2 °C (vs 1991–2020)"),
    "runoff_4": ("runoff_change_pct_plus4C", "Runoff change at +4 °C", "RdBu", -30, 30,
                 "Runoff change (%)", "Change in mean runoff at +4 °C (vs 1991–2020)"),
    "use": ("log_use", "Licensed consumptive water use", "Blues", 0, 3.5,
            "log10 of hm³/yr", "Licensed consumptive water use"),
}

# the map opens on +2 °C: the 1991-2020 map is ~7 % everywhere by construction
DEFAULT = "freq_2"


def axis(**extra):
    d = dict(showline=True, linecolor="black", linewidth=1, mirror=True, ticks="outside",
             ticklen=4, tickcolor="black", showgrid=True, gridcolor="#d9d9d9", griddash="dot",
             zeroline=False, title_font=dict(size=13))
    d.update(extra)
    return d


def layout(height, title, **extra):
    d = dict(height=height, paper_bgcolor=WHITE, plot_bgcolor=WHITE,
             font=dict(family=FONT, size=12, color="black"),
             title=dict(text=f"<b>{title}</b>", x=0.5, xanchor="center", font=dict(size=15)),
             margin=dict(l=70, r=20, t=50, b=55),
             hoverlabel=dict(bgcolor=WHITE, bordercolor="black", font=dict(family=FONT, color=TEXT, size=12)),
             xaxis=axis(), yaxis=axis())
    d.update(extra)
    return d


def text_box(text, x, y):
    """Text in a white box with a thin black edge, bottom-left corner at (x, y) of the axes."""
    return dict(text=text, xref="x domain", yref="y domain", x=x, y=y, xanchor="left", yanchor="bottom",
                showarrow=False, bgcolor=WHITE, bordercolor="black", borderwidth=1, borderpad=4,
                font=dict(size=11))


def polygon_xy(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    xs, ys = [], []
    for poly in polys:
        for ring in poly:
            xs += [round(p[0], 3) for p in ring] + [None]
            ys += [round(p[1], 3) for p in ring] + [None]
    return xs, ys


def lines_xy(geo, kind):
    xs, ys = [], []
    for f in geo["features"]:
        if f["properties"]["kind"] != kind:
            continue
        g = f["geometry"]
        for line in ([g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]):
            xs += [round(p[0], 3) for p in line] + [None]
            ys += [round(p[1], 3) for p in line] + [None]
    return xs, ys


def main():
    ind = pd.read_csv(OUT / "basin_indicators.csv", dtype={"unit_code": str})
    ind["region"] = ind.drainage.str.extract(r"(Pacífico|Amazonas|Titicaca)")[0].map(REGION)
    for c in ind.columns:
        if c.startswith("severe_drought_freq"):
            ind[c] = 100 * ind[c]
    ind["log_use"] = np.log10(ind.consumptive_use_hm3.clip(lower=1))
    geo = json.loads((OUT / "basins_web.geojson").read_text(encoding="utf-8"))
    ne = json.loads((OUT / "natural_earth_lines.geojson").read_text(encoding="utf-8"))
    spei = pd.read_parquet(OUT / "basin_spei_monthly.parquet")
    val = pd.read_csv(OUT / "validation_events.csv")
    order = ind.sort_values("unit_name").unit_code.tolist()
    name = ind.set_index("unit_code").unit_name
    ok = ~ind.hyper_arid.astype(bool)

    # ---- map on longitude/latitude axes with equal aspect (as a plate carrée map):
    # one filled polygon per basin, recoloured by the page for each indicator
    by_code = ind.set_index("unit_code")
    codes = [str(f["properties"]["unit_code"]) for f in geo["features"]]
    n_basins = len(codes)
    states = {}
    for key, (col, label, scale, zmin, zmax, unit, title) in MAP.items():
        v = by_code.loc[codes, col]
        frac = ((v.fillna(zmin) - zmin) / (zmax - zmin)).clip(0, 1).tolist()
        fill = [NODATA if pd.isna(x) else c for x, c in zip(v, sample_colorscale(get_colorscale(scale), frac))]
        if key == "use":
            shown = by_code.loc[codes, "consumptive_use_hm3"].map(lambda x: f"{x:,.1f} hm³/yr")
            box = f"Total licensed: {ind.consumptive_use_hm3.sum() / 1000:,.1f} km³/yr"
        elif key.startswith("freq"):
            shown = v.map(lambda x: f"{x:.1f} % of months")
            box = f"Median: {ind.loc[ok, col].median():.1f} % of months"
        else:
            shown = v.map(lambda x: "n/a (too little runoff)" if pd.isna(x) else f"{x:+.1f} %")
            box = f"Median: {ind[col].median():+.1f} %"
        text = [f"<b>{html.escape(by_code.at[c, 'unit_name'])}</b><br>{label}: {s}"
                f"<br>Region: {by_code.at[c, 'region']}" for c, s in zip(codes, shown)]
        # explicit scale: plotly.js orders some named scales the other way round
        states[key] = {"fill": fill, "text": text, "scale": get_colorscale(scale), "zmin": zmin, "zmax": zmax,
                       "unit": unit, "title": f"<b>{title}</b>", "box": box}

    s0 = states[DEFAULT]
    fig = go.Figure()
    for i, f in enumerate(geo["features"]):
        xs, ys = polygon_xy(f["geometry"])
        fig.add_trace(go.Scatter(x=xs, y=ys, fill="toself", fillcolor=s0["fill"][i], mode="lines",
                                 line=dict(color=WHITE, width=0.5), hoveron="fills", hoverinfo="text",
                                 text=s0["text"][i], showlegend=False))
    for kind, style in {"coast": dict(color="black", width=1),
                        "border": dict(color="black", width=0.8, dash="dash")}.items():
        xs, ys = lines_xy(ne, kind)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=style, hoverinfo="skip", showlegend=False))
    # colour bar carried by an empty marker trace
    fig.add_trace(go.Scatter(
        x=[None], y=[None], mode="markers", hoverinfo="skip", showlegend=False,
        marker=dict(colorscale=s0["scale"], cmin=s0["zmin"], cmax=s0["zmax"], color=[s0["zmin"]], showscale=True,
                    colorbar=dict(x=0.72, xanchor="left", thickness=16, len=0.8, outlinecolor="black", outlinewidth=1, ticks="outside",
                                  tickcolor="black", title=dict(text=s0["unit"], side="right")))))
    lon_ticks = list(range(-80, -67, 2))
    lat_ticks = [x / 2 for x in range(-35, 1, 5)]
    fig.update_layout(**layout(
        720, MAP[DEFAULT][6], margin=dict(l=70, r=20, t=50, b=40),
        xaxis=axis(range=[-81.6, -68.4], tickvals=lon_ticks, ticktext=[f"{-v:g}°W" for v in lon_ticks],
                   domain=[0.0, 0.7], constrain="domain", constraintoward="right"),
        yaxis=axis(range=[-18.6, 0.3], tickvals=lat_ticks, scaleanchor="x", scaleratio=1, constrain="domain",
                   ticktext=["0°" if v == 0 else f"{-v:g}°S" for v in lat_ticks]),
        annotations=[text_box(s0["box"], 0.02, 0.02)]))

    # ---- priority matrix
    mat = go.Figure()
    for r, g in ind[ok].groupby("region"):
        mat.add_trace(go.Scatter(
            x=g.consumptive_use_hm3.clip(lower=0.1), y=g.severe_drought_freq_plus2C, mode="markers",
            name=r, marker=dict(color=REGION_COLOR[r], size=8 + 18 * np.sqrt(g.use_mining_hm3 / max(ind.use_mining_hm3.max(), 1)),
                                line=dict(width=g.priority.map({True: 2, False: 0.5}), color="black"), opacity=0.8),
            customdata=np.stack([g.unit_name, g.severe_drought_freq_baseline.round(1),
                                 g.use_mining_hm3.round(1)], axis=1),
            hovertemplate=("<b>%{customdata[0]}</b><br>Consumptive use: %{x:.1f} hm³/yr"
                           "<br>Severe drought at +2 °C: %{y:.1f} % (today %{customdata[1]} %)"
                           "<br>Mining: %{customdata[2]} hm³/yr<extra></extra>")))
    q = ind.consumptive_use_hm3.quantile(2 / 3)
    mat.add_vline(x=q, line=dict(color="black", dash="dash", width=1))
    mat.update_layout(**layout(480, "Licensed water demand vs severe drought at +2 °C"),
                      legend=dict(x=0.01, y=0.99, xanchor="left", yanchor="top", bgcolor=WHITE,
                                  bordercolor="#cccccc", borderwidth=1))
    mat.update_xaxes(type="log", title_text="Licensed consumptive water use (hm³/yr, log scale)")
    mat.update_yaxes(title_text="Months in severe drought at +2 °C (%)", rangemode="tozero")

    # ---- SPEI-12 series for one basin at a time
    # one bar trace; the page swaps its values, the trend line and the title
    wide = (spei.pivot(index="time", columns="unit_code", values="spei12")
            .loc["1959-01-01":].round(2))
    years = np.round(wide.index.year + (wide.index.month - 0.5) / 12, 3)
    x0, x1 = float(years.min()), float(years.max())

    def trend(y):
        m = y.notna().to_numpy()
        a, b = np.polyfit(years[m], y.to_numpy()[m], 1)
        return [round(a * x0 + b, 3), round(a * x1 + b, 3)], f"Trend: {10 * a:+.2f} SPEI/decade"

    def series_title(u):
        return f"<b>SPEI-12 — {html.escape(name[u])} (ref. 1991–2020)</b>"

    first = wide[order[0]]
    t0, label0 = trend(first)
    ser = go.Figure()
    ser.add_trace(go.Bar(x=list(years), y=first, width=1 / 12, marker_line_width=0, showlegend=False,
                         marker_color=np.where(first < 0, DRY, WET), customdata=wide.index.strftime("%b %Y"),
                         hovertemplate="%{customdata}<br>SPEI-12: %{y}<extra></extra>"))
    ser.add_trace(go.Scatter(x=[x0, x1], y=[-1.5, -1.5], mode="lines", hoverinfo="skip",
                             line=dict(color="black", dash="dot", width=1), name="Severe drought (−1.5)"))
    ser.add_trace(go.Scatter(x=[x0, x1], y=t0, mode="lines", hoverinfo="skip",
                             line=dict(color="black", dash="dash", width=1.5), name=label0))
    ser.add_hline(y=0, line=dict(color="black", width=1))
    ser.update_layout(**layout(420, ""), bargap=0,
                      legend=dict(x=0.01, y=0.99, xanchor="left", yanchor="top", bgcolor=WHITE, borderwidth=0))
    ser.update_layout(title_text=series_title(order[0]))
    ser.update_xaxes(title_text="Year", range=[x0 - 0.5, x1 + 0.5], tickvals=list(range(1960, 2026, 5)))
    ser.update_yaxes(title_text="SPEI-12", range=[-3.2, 3.2])
    serie_values = {}
    for u in order:
        tr, lab = trend(wide[u])
        serie_values[u] = {"y": [None if pd.isna(v) else float(v) for v in wide[u]],
                           "t": tr, "s": lab, "title": series_title(u)}

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
    data_js = json.dumps({"map": states, "n": n_basins, "series": serie_values}, separators=(",", ":"))

    page = TEMPLATE.format(plotly=PLOTLY_CDN, repo=REPO, cards=cards_html, buttons=buttons,
                           options=options, priority_rows=rows, validation_rows=val_rows, dry=DRY, wet=WET,
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
  (grey) for basins with under 20 mm of runoff per year. Medians of drought frequency leave out hyper-arid
  coastal units.</p>
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
  force, summed per basin and sector. Hydropower, aquaculture and fishing are not counted as consumptive.
  Coastline and borders: Natural Earth (public domain).</p>
  <p>SPEI follows Vicente-Serrano et al. (2010) with a log-logistic distribution fitted per basin and calendar
  month on 1991-2020 (xclim).</p>
  <h2>Limitations</h2>
  <p>The +2 °C and +4 °C series apply one pattern of change to the observed record: they do not show the spread
  between climate models. TerraClimate runoff comes from a simple water balance without glacier melt or inflow from
  upstream basins and is well below gauged flows in Pacific basins, so it is used only as a relative change, never as
  available water. Licensed volume is the volume a right allows, not the volume used.</p>
</section>

<footer>Cristhian B. Gamarra Fuentes · <a href="{repo}">code on GitHub</a> ·
contains data from TerraClimate (CC0), Peru's National Water Authority (ANA) and Natural Earth.</footer>
</main>
<script>
const DATA = {data_js};
const BASINS = [...Array(DATA.n).keys()];
document.querySelectorAll("#indicator button").forEach(b => b.addEventListener("click", () => {{
  document.querySelectorAll("#indicator button").forEach(x => x.setAttribute("aria-pressed", x === b));
  const m = DATA.map[b.dataset.k];
  Plotly.restyle("fig-map", {{fillcolor: m.fill, text: m.text}}, BASINS);
  Plotly.update("fig-map", {{"marker.colorscale": [m.scale], "marker.cmin": [m.zmin], "marker.cmax": [m.zmax],
    "marker.colorbar.title.text": [m.unit]}}, {{"title.text": m.title, "annotations[0].text": m.box}}, [DATA.n + 2]);
}}));
document.getElementById("unit").addEventListener("change", ev => {{
  const s = DATA.series[ev.target.value];
  Plotly.restyle("fig-series", {{y: [s.y], "marker.color": [s.y.map(v => v < 0 ? "{dry}" : "{wet}")]}}, [0]);
  Plotly.update("fig-series", {{y: [s.t], name: [s.s]}}, {{"title.text": s.title}}, [2]);
}});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
