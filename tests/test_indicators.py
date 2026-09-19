"""Indicator correctness, checked against hand-computed values where possible."""

import numpy as np
import pandas as pd
import pytest

from trendlab import indicators as ind
from trendlab.data import SyntheticProvider


@pytest.fixture(scope="module")
def frame():
    return SyntheticProvider().history("TEST", period="3y")


def test_sma_matches_manual_mean(frame):
    close = frame["close"]
    result = ind.sma(close, 10)
    assert result.iloc[:9].isna().all(), "SMA must not emit a value before its window fills"
    assert result.iloc[9] == pytest.approx(close.iloc[:10].mean())
    assert result.iloc[50] == pytest.approx(close.iloc[41:51].mean())


def test_rsi_bounds_and_direction():
    # A strictly rising series has no down-closes, so RSI pegs at 100.
    rising = pd.Series(np.arange(100, 160, dtype=float))
    assert ind.rsi(rising, 14).iloc[-1] == pytest.approx(100.0)

    falling = pd.Series(np.arange(160, 100, -1, dtype=float))
    assert ind.rsi(falling, 14).iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_rsi_stays_in_range(frame):
    values = ind.rsi(frame["close"], 14).dropna()
    assert values.between(0, 100).all()


def test_macd_histogram_is_line_minus_signal(frame):
    m = ind.macd(frame["close"])
    diff = (m["macd"] - m["macd_signal"] - m["macd_hist"]).dropna()
    assert (diff.abs() < 1e-9).all()


def test_bollinger_band_ordering(frame):
    b = ind.bollinger(frame["close"]).dropna()
    assert (b["bb_upper"] >= b["bb_mid"]).all()
    assert (b["bb_mid"] >= b["bb_lower"]).all()


def test_true_range_covers_gaps():
    # A gap-up day: the true range must span from the previous close, not just
    # the day's own high-low.
    f = pd.DataFrame({
        "high": [10.0, 20.0], "low": [9.0, 19.0], "close": [9.5, 19.5],
    })
    tr = ind.true_range(f["high"], f["low"], f["close"])
    assert tr.iloc[1] == pytest.approx(20.0 - 9.5)


def test_atr_is_positive(frame):
    assert (ind.atr(frame["high"], frame["low"], frame["close"]).dropna() > 0).all()


def test_adx_in_range(frame):
    a = ind.adx(frame["high"], frame["low"], frame["close"]).dropna()
    assert a["adx"].between(0, 100).all()
    assert a["plus_di"].between(0, 100).all()


def test_drawdown_is_non_positive(frame):
    assert (ind.rolling_drawdown(frame["close"]) <= 1e-12).all()


def test_enrich_adds_expected_columns(frame):
    out = ind.enrich(frame)
    for column in ("sma_200", "rsi_14", "macd_hist", "bb_pct", "adx", "atr_pct", "drawdown"):
        assert column in out.columns
    assert len(out) == len(frame)


@pytest.mark.parametrize("fn", ["sma_20", "sma_50", "sma_200", "rsi_14", "macd", "adx", "atr_14"])
def test_indicators_are_causal(frame, fn):
    """Recomputing on a truncated history must not change earlier values.

    This is the property that makes the whole package trustworthy: if an
    indicator changed when future bars arrived, every backtest built on it
    would be quietly using tomorrow's data.
    """
    full = ind.enrich(frame)[fn]
    truncated = ind.enrich(frame.iloc[:-30])[fn]
    overlap = truncated.dropna().index
    assert len(overlap) > 100
    pd.testing.assert_series_equal(full.loc[overlap], truncated.loc[overlap])
