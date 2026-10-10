"""Mutual fund support: NAV parsing, asset-class routing, and the volume gap.

The central risk this file guards: a NAV series has no traded volume, and the
volume-derived features silently reduce the training set to zero rows if the
asset class is not respected.
"""

import numpy as np
import pandas as pd
import pytest

from trendlab import features, model, regime
from trendlab.analysis import analyze
from trendlab.config import ModelConfig
from trendlab.data import SyntheticProvider, resolve_provider_name
from trendlab.data.amfi import parse_amfi_nav_all, parse_mfapi
from trendlab.data.base import DataError
from trendlab.indicators import enrich

FAST = ModelConfig(horizon_days=5, min_train_bars=400, retrain_every=150, n_estimators=30)

NAV_ALL_SAMPLE = """Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Large Cap Fund)
HDFC Mutual Fund
118834;INF179K01XQ0;-;HDFC Top 100 Fund - Growth;1102.4560;26-Sep-2026
118835;INF179K01XR8;-;HDFC Top 100 Fund - IDCW;62.1200;26-Sep-2026
Aditya Birla Sun Life Mutual Fund
119551;INF209KB1ZW4;INF209KB1ZX2;Aditya Birla Sun Life Frontline Equity Fund - Growth;412.5500;26-Sep-2026
"""


# ---------- AMFI parsing ----------
def test_nav_all_skips_headings_and_amc_rows():
    schemes = parse_amfi_nav_all(NAV_ALL_SAMPLE)
    assert [s["code"] for s in schemes] == ["118834", "118835", "119551"]
    assert schemes[0]["name"] == "HDFC Top 100 Fund - Growth"


def test_nav_all_deduplicates_repeated_codes():
    doubled = NAV_ALL_SAMPLE + "118834;INF179K01XQ0;-;HDFC Top 100 Fund - Growth;1102.4560;26-Sep-2026\n"
    assert len(parse_amfi_nav_all(doubled)) == 3


def test_nav_all_on_empty_input():
    assert parse_amfi_nav_all("") == []


def test_mfapi_parses_and_sorts_ascending():
    payload = {"data": [
        {"date": "26-09-2026", "nav": "1102.4560"},
        {"date": "25-09-2026", "nav": "1099.1000"},
        {"date": "24-09-2026", "nav": "1095.0000"},
    ]}
    frame = parse_mfapi(payload, "118834")
    assert frame.index.is_monotonic_increasing
    assert frame["close"].iloc[-1] == pytest.approx(1102.456)


def test_mfapi_skips_blank_navs():
    """AMFI publishes empty NAV strings on non-dealing days."""
    payload = {"data": [
        {"date": "26-09-2026", "nav": "1102.4560"},
        {"date": "25-09-2026", "nav": ""},
        {"date": "24-09-2026", "nav": "0"},
    ]}
    assert len(parse_mfapi(payload, "118834")) == 1


def test_mfapi_sets_every_price_column_to_the_nav():
    frame = parse_mfapi({"data": [{"date": "26-09-2026", "nav": "100.5"}]}, "1")
    row = frame.iloc[0]
    assert row["open"] == row["high"] == row["low"] == row["close"] == 100.5


def test_mfapi_leaves_volume_missing_not_zero():
    """Zero volume is a value the model would learn from; NaN excludes it."""
    frame = parse_mfapi({"data": [{"date": "26-09-2026", "nav": "100.5"}]}, "1")
    assert frame["volume"].isna().all()


def test_mfapi_rejects_an_empty_payload():
    with pytest.raises(DataError):
        parse_mfapi({"data": []}, "118834")


# ---------- routing ----------
@pytest.mark.parametrize("symbol,expected", [
    ("MF:118834", "amfi"), ("118834", "amfi"), ("mf:118834", "amfi"),
    ("RELIANCE.NS", "yahoo"), ("^NSEI", "yahoo"), ("AAPL", "yahoo"),
])
def test_symbols_route_to_the_right_provider(symbol, expected):
    assert resolve_provider_name(symbol) == expected


def test_explicit_provider_overrides_routing():
    assert resolve_provider_name("MF:118834", "synthetic") == "synthetic"


# ---------- the volume gap ----------
@pytest.fixture(scope="module")
def fund_frame():
    return enrich(SyntheticProvider().history("MF:118834", period="5y"))


def test_fund_frame_has_no_volume(fund_frame):
    assert fund_frame["volume"].isna().all()


def test_volume_features_would_erase_the_dataset(fund_frame):
    """Guards the bug this feature set exists to prevent."""
    with_volume, _, _ = features.build_dataset(fund_frame, 5, has_volume=True)
    assert len(with_volume) == 0

    without_volume, _, _ = features.build_dataset(fund_frame, 5, has_volume=False)
    assert len(without_volume) > 500


def test_fund_feature_set_excludes_volume_columns():
    cols = features.feature_columns(has_volume=False)
    assert "volume_ratio" not in cols and "obv_slope_norm" not in cols
    assert "rsi_14" in cols
    assert set(features.feature_columns(True)) - set(cols) == set(features.VOLUME_FEATURE_COLUMNS)


def test_fund_regime_reports_volume_flow_as_not_applicable(fund_frame):
    assert regime.classify(fund_frame, has_volume=False).participation == "not_applicable"


def test_equity_regime_still_reports_volume_flow():
    equity = enrich(SyntheticProvider().history("RELIANCE.NS", period="3y"))
    assert regime.classify(equity, has_volume=True).participation != "not_applicable"


def test_fund_forecast_runs_and_names_only_price_features(fund_frame):
    fc = model.forecast(fund_frame, FAST, has_volume=False)
    assert 0.0 <= fc.probability_up <= 1.0
    named = {f["feature"] for f in fc.feature_importance}
    assert not named & set(features.VOLUME_FEATURE_COLUMNS)


# ---------- end to end ----------
def test_analyze_detects_a_fund_from_its_data():
    report = analyze("MF:118834", period="5y", provider="synthetic", include_forecast=False)
    assert report["asset_kind"] == "fund"
    assert report["has_volume"] is False
    assert report["regime"]["participation"] == "not_applicable"


def test_analyze_still_treats_equities_as_equities():
    report = analyze("RELIANCE.NS", period="3y", provider="synthetic", include_forecast=False)
    assert report["asset_kind"] == "equity"
    assert report["has_volume"] is True
