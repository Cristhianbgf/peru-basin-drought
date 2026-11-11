"""Checks of the two steps where a silent error would change every result:
the cell weights per hydrographic unit and the SPEI calibration."""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_basin_series import unit_weights  # noqa: E402
from compute_indicators import spei_series  # noqa: E402


def test_weights_sum_to_one_and_cover_only_inside_cells():
    lat = np.array([-10.0, -10.5, -11.0])
    lon = np.array([-75.0, -74.5, -74.0])
    units = gpd.GeoDataFrame({"unit_code": ["a", "b"]},
                             geometry=[box(-75.2, -10.7, -74.3, -9.8),   # 4 cell centres
                                       box(-74.05, -11.05, -73.95, -10.95)])  # 1 centre
    w, cells = unit_weights(lat, lon, units)
    assert np.allclose(w.sum(axis=0), 1.0)
    assert cells.n_cells.tolist() == [4, 1]
    assert not cells.nearest_cell_fallback.any()


def test_unit_without_cell_centre_takes_nearest_cell():
    lat = np.array([-10.0, -11.0])
    lon = np.array([-75.0, -74.0])
    tiny = box(-74.1, -10.9, -74.05, -10.85)          # between centres, near (-11, -74)
    w, cells = unit_weights(lat, lon, gpd.GeoDataFrame({"unit_code": ["t"]}, geometry=[tiny]))
    assert cells.nearest_cell_fallback.iloc[0]
    assert w[:, 0].argmax() == 3                       # row-major: (-11, -74) is the last cell


def _wb(values, start="1958-01-01"):
    t = pd.date_range(start, periods=len(values), freq="MS")
    return xr.DataArray(values[:, None], coords={"time": t, "unit": ["u"]},
                        dims=("time", "unit"), attrs={"units": "mm/month"})


def test_spei_is_standard_in_calibration_period_and_detects_a_drier_climate():
    rng = np.random.default_rng(42)
    seasonal = 60 * np.sin(2 * np.pi * np.arange(68 * 12) / 12)
    hist = _wb(seasonal + rng.normal(20, 40, 68 * 12))
    drier = _wb((seasonal + rng.normal(20, 40, 68 * 12) - 25)[33 * 12:63 * 12], "1991-01-01")
    s_hist, s_scen = spei_series(hist, {"drier": drier}, 12)
    cal = s_hist.sel(time=slice("1991", "2020"))
    assert abs(float(cal.mean())) < 0.1
    assert 0.85 < float(cal.std()) < 1.15
    assert float((cal <= -1.5).mean()) == pytest.approx(0.067, abs=0.03)
    assert float((s_scen["drier"] <= -1.5).mean()) > 0.2
