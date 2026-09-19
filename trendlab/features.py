"""Feature matrix and forecast target.

Two invariants hold everything together:

1. Every feature at bar ``t`` is a function of bars ``<= t``.
2. The target at bar ``t`` is the return from ``t`` to ``t + horizon``, so the
   last ``horizon`` rows have no label and must never be trained on.

The features are all *scale-free* (ratios, percentiles, oscillators) so a model
fitted on a stock at 100 still means something when that stock trades at 400.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "rsi_14",
    "macd_hist_norm",
    "bb_pct",
    "bb_width",
    "adx",
    "di_spread",
    "stoch_k",
    "atr_pct",
    "vol_20",
    "vol_ratio_20_60",
    "drawdown",
    "dist_sma_50",
    "dist_sma_200",
    "sma_50_200_spread",
    "volume_ratio",
    "obv_slope_norm",
    "ret_1",
    "ret_5",
    "ret_21",
    "ret_63",
    "ret_126",
    "mom_accel",
]


def build(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive the model features from an indicator-enriched frame."""
    f = frame
    out = pd.DataFrame(index=f.index)

    out["rsi_14"] = f["rsi_14"]
    # MACD is in price units; divide by price so it compares across symbols.
    out["macd_hist_norm"] = f["macd_hist"] / f["close"]
    out["bb_pct"] = f["bb_pct"]
    out["bb_width"] = f["bb_width"]
    out["adx"] = f["adx"]
    out["di_spread"] = f["plus_di"] - f["minus_di"]
    out["stoch_k"] = f["stoch_k"]
    out["atr_pct"] = f["atr_pct"]
    out["vol_20"] = f["vol_20"]
    # Short vol vs long vol: >1 means volatility is expanding.
    out["vol_ratio_20_60"] = f["vol_20"] / f["vol_60"].replace(0.0, np.nan)
    out["drawdown"] = f["drawdown"]
    out["dist_sma_50"] = f["dist_sma_50"]
    out["dist_sma_200"] = f["dist_sma_200"]
    out["sma_50_200_spread"] = (f["sma_50"] - f["sma_200"]) / f["close"]
    out["volume_ratio"] = f["volume_ratio"]
    # OBV slope normalized by typical volume, so it is unit-free.
    out["obv_slope_norm"] = f["obv"].diff(20) / (
        f["volume"].rolling(20, min_periods=20).mean() * 20
    ).replace(0.0, np.nan)

    for w in (1, 5, 21, 63, 126):
        out[f"ret_{w}"] = f[f"ret_{w}"]
    # Is the 1-month move accelerating relative to the 3-month move?
    out["mom_accel"] = f["ret_21"] - f["ret_63"] / 3.0

    return out[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)


def target(frame: pd.DataFrame, horizon: int) -> pd.Series:
    """Forward return over ``horizon`` bars, aligned to the decision bar.

    ``shift(-horizon)`` is what places a *future* return on today's row. It is
    the only forward-looking operation in the package, and it exists solely to
    create labels — never as a feature.
    """
    close = frame["close"].astype(float)
    return (close.shift(-horizon) / close - 1.0).rename(f"fwd_ret_{horizon}")


def build_dataset(frame: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Return (X, y_return, y_up) with unusable rows dropped.

    Rows are dropped when a feature is missing (warm-up) or the label is missing
    (the final ``horizon`` bars). The dropped tail is why predictions for the
    most recent bars come from the model rather than from the training set.
    """
    X = build(frame)
    y_ret = target(frame, horizon)

    usable = X.notna().all(axis=1) & y_ret.notna()
    X, y_ret = X[usable], y_ret[usable]
    return X, y_ret, (y_ret > 0).astype(int).rename("up")


def latest_features(frame: pd.DataFrame) -> pd.DataFrame | None:
    """The most recent fully-populated feature row — what a live forecast uses."""
    X = build(frame)
    complete = X[X.notna().all(axis=1)]
    return complete.iloc[[-1]] if len(complete) else None
