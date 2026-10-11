"""Consumer screens: market overview, stock snapshots, returns and plain-English text."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trendlab import explain, features, market
from trendlab.analysis import analyze
from trendlab.config import ModelConfig
from trendlab.data import SyntheticProvider
from trendlab.indicators import enrich
from trendlab.search import DEMO_FUNDS, lookup_name, search
from trendlab.server.app import app


def _frame(n=300, volume=1000.0, start=100.0):
    idx = pd.bdate_range(end="2026-10-09", periods=n)
    close = np.linspace(start, start * 1.5, n)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": volume}, index=idx)


# ---------- asset class ----------
def test_equity_with_volume():
    assert market.asset_profile("RELIANCE.NS", _frame()) == ("equity", True)


def test_fund_nav_has_no_volume():
    assert market.asset_profile("MF:900001", _frame(volume=np.nan)) == ("fund", False)


def test_index_with_zero_volume_has_no_volume():
    """Yahoo reports 0 volume for many Indian indices; zeros are not volume."""
    assert market.asset_profile("^NSEI", _frame(volume=0.0)) == ("index", False)


def test_zero_volume_index_still_gets_a_forecast():
    """Regression: zero volume used to pass the volume check and empty the dataset."""
    raw = SyntheticProvider().history("^NSEI", period="5y").copy()
    raw["volume"] = 0.0
    kind, has_volume = market.asset_profile("^NSEI", raw)
    X, _, _ = features.build_dataset(enrich(raw), 5, has_volume=has_volume)
    assert has_volume is False and len(X) > 500


# ---------- quote ----------
def test_quote_change_is_against_previous_close():
    f = _frame()
    q = market.quote_summary(f, "equity", True)
    last, prev = f["close"].iloc[-1], f["close"].iloc[-2]
    assert q["change"] == pytest.approx(last - prev, abs=0.01)
    # From the exact prices, not the paise-rounded display figures.
    assert q["change_pct"] == pytest.approx(last / prev - 1, rel=1e-4)


def test_fund_quote_omits_figures_a_nav_does_not_have():
    q = market.quote_summary(_frame(volume=np.nan), "fund", False)
    assert q["open"] is None and q["day_high"] is None and q["volume"] is None
    assert q["high_52w"] is not None


def test_52_week_window_is_calendar_based():
    f = _frame(n=600)
    q = market.quote_summary(f, "equity", True)
    last_year = f[f.index > f.index[-1] - pd.DateOffset(years=1)]
    assert q["low_52w"] == pytest.approx(last_year["low"].min(), abs=0.01)


# ---------- returns ----------
def _five_years(bars=None):
    end = pd.Timestamp("2026-10-09")
    idx = pd.bdate_range(start=end - pd.DateOffset(years=5) + pd.Timedelta(days=2), end=end)
    if bars:
        keep = np.linspace(0, len(idx) - 1, bars).round().astype(int)
        idx = idx[np.unique(keep)]
    return pd.Series(np.linspace(100, 200, len(idx)), index=idx)


def test_five_year_return_appears_with_a_real_trading_calendar():
    """Regression: a bar-count rule needed 1,261 bars, but NSE has ~1,240 in 5 years."""
    r = market.period_returns(_five_years(bars=1240))
    assert r["5Y"] is not None
    assert r["5Y"]["value"] == pytest.approx(2 ** (1 / 5) - 1, abs=1e-3)


def test_long_returns_are_annualised_and_short_ones_are_not():
    r = market.period_returns(_five_years())
    assert r["3Y"]["annualised"] and r["5Y"]["annualised"]
    assert not r["1Y"]["annualised"] and not r["1M"]["annualised"]


def test_return_is_blank_when_history_is_too_short():
    r = market.period_returns(_frame(n=200)["close"])
    assert r["1Y"] is None and r["5Y"] is None
    assert r["1M"] is not None


# ---------- batch download ----------
def _download_like(symbols, ticker_level):
    idx = pd.bdate_range(end="2026-10-09", periods=10)
    fields = ["Open", "High", "Low", "Close", "Volume"]
    data = {}
    for s in symbols:
        for f in fields:
            data[(s, f) if ticker_level == 0 else (f, s)] = np.arange(10, 20, dtype=float)
    return pd.DataFrame(data, index=idx)


@pytest.mark.parametrize("level", [0, 1])
def test_split_download_finds_tickers_on_either_level(level):
    out = market.split_download(_download_like(["A.NS", "B.NS"], level), ["A.NS", "B.NS"])
    assert set(out) == {"A.NS", "B.NS"}
    assert list(out["A.NS"].columns) == ["open", "high", "low", "close", "volume"]


def test_split_download_drops_a_ticker_that_failed():
    raw = _download_like(["A.NS", "B.NS"], 0)
    raw[[c for c in raw.columns if c[0] == "B.NS"]] = np.nan
    assert set(market.split_download(raw, ["A.NS", "B.NS"])) == {"A.NS"}


def test_split_download_handles_empty_and_flat_frames():
    assert market.split_download(pd.DataFrame(), ["A.NS"]) == {}
    flat = _download_like(["A.NS"], 0).droplevel(0, axis=1)
    assert set(market.split_download(flat, ["A.NS"])) == {"A.NS"}


def test_fetch_many_survives_a_failing_symbol():
    class Flaky:
        def history(self, symbol, period="3mo"):
            if symbol == "BAD.NS":
                raise ConnectionError("nope")
            return _frame(n=30)

    assert set(market.fetch_many(Flaky(), ["A.NS", "BAD.NS", "C.NS"])) == {"A.NS", "C.NS"}


def test_fetch_many_falls_back_when_batch_raises():
    class BatchBroken:
        def history_many(self, symbols, period="3mo"):
            raise RuntimeError("batch endpoint changed")

        def history(self, symbol, period="3mo"):
            return _frame(n=30)

    assert set(market.fetch_many(BatchBroken(), ["A.NS", "B.NS"])) == {"A.NS", "B.NS"}


# ---------- screens ----------
def test_overview_has_indices_and_named_stocks():
    m = market.market_overview("synthetic", use_cache=False)
    assert [i["name"] for i in m["indices"]] == ["NIFTY 50", "BSE SENSEX", "NIFTY Bank"]
    assert len(m["stocks"]) == len(market.POPULAR_STOCKS)
    assert all(s["name"] and s["name"] != s["symbol"] for s in m["stocks"])
    assert m["demo"] is True and m["missing"] == []


def test_overview_is_cached():
    first = market.market_overview("synthetic", use_cache=False)
    assert market.market_overview("synthetic") is first


def test_snapshot_for_a_stock():
    s = market.stock_snapshot("RELIANCE.NS", "synthetic")
    assert s["name"] == "Reliance Industries" and s["exchange"] == "NSE" and s["kind"] == "equity"
    assert len(s["history"]["dates"]) == len(s["history"]["close"]) > 1000
    assert s["mood"]["headline"]


def test_snapshot_for_a_demo_fund_uses_its_fictional_name():
    s = market.stock_snapshot("MF:900002", "synthetic")
    assert s["kind"] == "fund" and s["exchange"] == "Mutual fund"
    assert s["name"].startswith("Demo ")


# ---------- plain English ----------
FC = {"probability_up": 0.62, "horizon_days": 5,
      "price_range": {"likely_low": 1380.2, "likely_high": 1452.9},
      "evaluation": {"has_edge": False, "accuracy": 0.49, "baseline_accuracy": 0.53, "up_rate": 0.53}}


def test_unreliable_forecast_says_coin_flip_in_the_same_breath():
    p = explain.prediction(FC)
    assert p["headline"] == "No reliable signal" and p["lean"] == "none"
    assert "coin flip" in " ".join(p["sentences"])


def test_reliable_forecast_names_its_lean_and_track_record():
    fc = {**FC, "evaluation": {**FC["evaluation"], "has_edge": True, "accuracy": 0.58}}
    p = explain.prediction(fc)
    assert p["headline"] == "Leans up" and p["reliable"]
    assert "58%" in " ".join(p["sentences"]) and "not a guarantee" in " ".join(p["sentences"])


def test_explanations_never_say_buy_or_sell():
    texts = []
    for edge in (True, False):
        for prob in (0.2, 0.5, 0.8):
            fc = {**FC, "probability_up": prob, "evaluation": {**FC["evaluation"], "has_edge": edge}}
            texts += explain.prediction(fc)["sentences"]
    for trend in ("strong_uptrend", "uptrend", "range", "downtrend", "strong_downtrend"):
        texts += explain.mood({"trend": trend, "volatility": "extreme", "momentum": "overbought"}, -0.3)["sentences"]
    joined = " ".join(texts).lower()
    assert "buy" not in joined and "sell" not in joined


def test_rupee_formatting_uses_indian_grouping():
    assert explain._money(1234567.5, True) == "₹12,34,567.50"
    assert explain._money(999.1, True) == "₹999.10"
    assert explain._money(24567.85, False) == "24,567.85"


def test_index_range_is_not_quoted_in_rupees():
    assert "₹" not in " ".join(explain.prediction(FC, kind="index")["sentences"])


def test_missing_forecast_explains_itself():
    p = explain.prediction(None, error="need at least 521 usable bars")
    assert p["headline"] == "No forecast" and "not enough price history" in p["sentences"][0]


def test_analysis_report_carries_the_explanation():
    cfg_model = ModelConfig(min_train_bars=400, retrain_every=150, n_estimators=30)
    from dataclasses import replace
    from trendlab.config import AppConfig
    r = analyze("RELIANCE.NS", provider="synthetic", config=replace(AppConfig(), model=cfg_model))
    assert r["explain"]["mood"]["headline"] and r["explain"]["prediction"]["sentences"]
    assert r["display_name"] == "Reliance Industries"


# ---------- names and demo search ----------
def test_lookup_name():
    assert lookup_name("reliance.ns") == "Reliance Industries"
    assert lookup_name("^NSEI") == "NIFTY 50"
    assert lookup_name("UNKNOWN.NS") is None


def test_demo_fund_names_are_all_fictional():
    assert all(f["name"].startswith("Demo ") for f in DEMO_FUNDS)


def test_demo_search_never_touches_real_funds():
    hits = search("fund", demo=True, include_equities=False)
    assert hits and all(h.symbol.startswith("MF:9000") for h in hits)


# ---------- API ----------
@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_market_endpoint(client):
    body = client.get("/api/market", params={"provider": "synthetic"}).json()
    assert body["indices"] and body["stocks"]


def test_stock_endpoint(client):
    body = client.get("/api/stock", params={"symbol": "TCS.NS", "provider": "synthetic"}).json()
    assert body["name"] == "Tata Consultancy Services" and body["quote"]["price"] > 0


def test_fund_search_endpoint_in_demo(client):
    body = client.get("/api/search", params={"q": "small cap", "kind": "fund",
                                             "provider": "synthetic"}).json()
    assert body["results"][0]["symbol"] == "MF:900003"


def test_equity_only_search_excludes_funds(client):
    body = client.get("/api/search", params={"q": "hdfc", "kind": "equity", "provider": "synthetic"}).json()
    assert all(r["kind"] != "fund" for r in body["results"])
