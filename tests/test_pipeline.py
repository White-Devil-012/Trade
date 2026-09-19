"""Data normalization, signals, regime labels and the HTTP API."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trendlab import regime, signals
from trendlab.analysis import analyze, compare
from trendlab.data import SyntheticProvider
from trendlab.data.base import DataError, normalize
from trendlab.indicators import enrich
from trendlab.server.app import app


@pytest.fixture(scope="module")
def frame():
    return enrich(SyntheticProvider().history("PIPE", period="5y"))


# ---------- data ----------
def test_normalize_rejects_empty():
    with pytest.raises(DataError):
        normalize(pd.DataFrame(), "NOPE")


def test_normalize_renames_and_sorts():
    idx = pd.to_datetime(["2024-01-03", "2024-01-02"])
    raw = pd.DataFrame(
        {"Open": [2.0, 1.0], "High": [2.0, 1.0], "Low": [2.0, 1.0],
         "Close": [2.0, 1.0], "Volume": [10, 20]}, index=idx)
    out = normalize(raw, "X")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index.is_monotonic_increasing
    assert out["close"].iloc[0] == 1.0


def test_normalize_drops_duplicate_dates():
    idx = pd.to_datetime(["2024-01-02", "2024-01-02"])
    raw = pd.DataFrame({"open": [1.0, 9.0], "high": [1.0, 9.0], "low": [1.0, 9.0],
                        "close": [1.0, 9.0], "volume": [1, 1]}, index=idx)
    out = normalize(raw, "X")
    assert len(out) == 1
    assert out["close"].iloc[0] == 9.0  # keeps the later record


def test_synthetic_respects_ohlc_invariants():
    d = SyntheticProvider().history("INV", period="2y")
    assert (d["high"] >= d[["open", "close"]].max(axis=1) - 1e-9).all()
    assert (d["low"] <= d[["open", "close"]].min(axis=1) + 1e-9).all()
    assert (d["volume"] > 0).all()


def test_synthetic_is_deterministic_per_symbol():
    a = SyntheticProvider().history("SEED", period="1y")
    b = SyntheticProvider().history("SEED", period="1y")
    pd.testing.assert_frame_equal(a, b)


# ---------- signals ----------
def test_golden_cross_fires_exactly_on_the_crossing_bar():
    f = pd.DataFrame({
        "sma_50":  [1.0, 1.0, 3.0, 3.0],
        "sma_200": [2.0, 2.0, 2.0, 2.0],
    })
    fired = signals.golden_cross(f).fillna(False)
    assert list(fired) == [False, False, True, False]


def test_signal_score_needs_a_margin_to_call_a_bias():
    one_each = [
        signals.Signal("a", "bullish", True, "", 0),
        signals.Signal("b", "bearish", True, "", 0),
    ]
    assert signals.score(one_each)["bias"] == "mixed"

    three_bull = [signals.Signal(f"s{i}", "bullish", True, "", 0) for i in range(3)]
    assert signals.score(three_bull)["bias"] == "bullish"


def test_unfired_signals_are_excluded_from_the_score():
    stale = [signals.Signal("old", "bullish", False, "", 400)]
    assert signals.score(stale)["bullish_count"] == 0


def test_evaluate_returns_every_rule(frame):
    result = signals.evaluate(frame)
    assert len(result) == len(signals.RULES)


def test_composite_position_is_binary(frame):
    pos = signals.composite_position(frame)
    assert set(np.unique(pos)) <= {0.0, 1.0}


# ---------- regime ----------
def test_regime_labels_are_known_values(frame):
    r = regime.classify(frame)
    assert r.trend in {"strong_uptrend", "uptrend", "range", "downtrend", "strong_downtrend", "unknown"}
    assert r.volatility in {"low", "normal", "high", "extreme", "unknown"}
    assert r.summary


def test_regime_history_covers_every_bar(frame):
    h = regime.history(frame)
    assert len(h) == len(frame)


# ---------- orchestration ----------
def test_analyze_without_forecast_is_complete(frame):
    r = analyze("PIPE", period="5y", provider="synthetic", include_forecast=False)
    assert r["symbol"] == "PIPE"
    assert r["bars"] > 1000
    assert r["forecast"] is None
    assert r["data_warning"]
    assert len(r["series"]["dates"]) == len(r["series"]["close"])


def test_compare_returns_a_row_per_symbol():
    rows = compare(["AAA", "BBB"], period="2y", provider="synthetic")
    assert [r["symbol"] for r in rows] == ["AAA", "BBB"]


def test_compare_isolates_a_failing_symbol(monkeypatch):
    from trendlab import analysis

    def explode(symbol, *args, **kwargs):
        if symbol == "BAD":
            raise DataError("no such symbol")
        return {"symbol": symbol, "last_close": 1.0, "change_1d": 0.0, "change_21d": 0.0,
                "regime": {"trend": "range", "volatility": "low", "momentum": "neutral"},
                "signal_score": {"bias": "mixed", "fired": []}}

    monkeypatch.setattr(analysis, "analyze", explode)
    rows = analysis.compare(["GOOD", "BAD"], provider="synthetic")
    assert "error" in rows[1]
    assert "error" not in rows[0]


# ---------- API ----------
@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_analyze_endpoint(client):
    res = client.get("/api/analyze",
                     params={"symbol": "API", "provider": "synthetic", "period": "2y", "forecast": False})
    assert res.status_code == 200
    body = res.json()
    assert body["symbol"] == "API"
    assert "regime" in body and "backtest" in body


def test_analyze_rejects_a_bad_period(client):
    res = client.get("/api/analyze", params={"symbol": "API", "period": "99y"})
    assert res.status_code == 422


def test_analyze_rejects_an_out_of_range_horizon(client):
    res = client.get("/api/analyze",
                     params={"symbol": "API", "provider": "synthetic", "horizon": 900})
    assert res.status_code == 422


def test_screen_endpoint(client):
    res = client.get("/api/screen", params={"symbols": "AAA,BBB", "provider": "synthetic"})
    assert res.status_code == 200
    assert len(res.json()["rows"]) == 2


def test_screen_rejects_empty_input(client):
    assert client.get("/api/screen", params={"symbols": " , "}).status_code == 400


def test_screen_caps_the_symbol_count(client):
    many = ",".join(f"S{i}" for i in range(40))
    rows = client.get("/api/screen", params={"symbols": many, "provider": "synthetic"}).json()["rows"]
    assert len(rows) == 12


def test_index_serves_the_dashboard(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "TrendLab" in res.text
