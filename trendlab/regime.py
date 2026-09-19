"""Market-condition classification.

Context beats prediction: the same RSI reading means different things in a
quiet uptrend and in a high-volatility drawdown. Every label here is computed
from bars up to and including ``t`` only.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Regime:
    trend: str          # strong_uptrend | uptrend | range | downtrend | strong_downtrend
    volatility: str     # low | normal | high | extreme
    momentum: str       # overbought | strong | neutral | weak | oversold
    participation: str  # accumulation | neutral | distribution
    drawdown_state: str # at_highs | pullback | correction | bear
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _trend_label(row: pd.Series) -> str:
    above_200 = row.get("dist_sma_200")
    above_50 = row.get("dist_sma_50")
    adx_val = row.get("adx")
    slope = row.get("sma_200_slope")

    if pd.isna(above_200) or pd.isna(adx_val):
        return "unknown"

    trending = adx_val >= 25
    rising = bool(slope > 0) if not pd.isna(slope) else above_200 > 0

    if above_200 > 0 and rising:
        return "strong_uptrend" if trending and (above_50 or 0) > 0 else "uptrend"
    if above_200 < 0 and not rising:
        return "strong_downtrend" if trending and (above_50 or 0) < 0 else "downtrend"
    return "range"


def _vol_label(pctile: float) -> str:
    if pd.isna(pctile):
        return "unknown"
    if pctile >= 0.90:
        return "extreme"
    if pctile >= 0.70:
        return "high"
    if pctile <= 0.30:
        return "low"
    return "normal"


def _momentum_label(rsi_val: float) -> str:
    if pd.isna(rsi_val):
        return "unknown"
    if rsi_val >= 70:
        return "overbought"
    if rsi_val >= 55:
        return "strong"
    if rsi_val <= 30:
        return "oversold"
    if rsi_val <= 45:
        return "weak"
    return "neutral"


def _participation_label(row: pd.Series) -> str:
    obv_slope = row.get("obv_slope_20")
    price_ret = row.get("ret_21")
    if pd.isna(obv_slope) or pd.isna(price_ret):
        return "unknown"
    # Volume confirming price is accumulation; volume diverging is distribution.
    if obv_slope > 0 and price_ret > 0:
        return "accumulation"
    if obv_slope < 0 and price_ret > 0:
        return "distribution"
    if obv_slope < 0 and price_ret < 0:
        return "distribution"
    return "neutral"


def _drawdown_label(dd: float) -> str:
    if pd.isna(dd):
        return "unknown"
    if dd >= -0.02:
        return "at_highs"
    if dd >= -0.10:
        return "pullback"
    if dd >= -0.20:
        return "correction"
    return "bear"


def annotate(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the helper columns regime labelling needs."""
    out = frame.copy()
    # Slope of the 200-day average over the last month, normalized by price.
    out["sma_200_slope"] = out["sma_200"].diff(21) / out["close"]
    out["obv_slope_20"] = out["obv"].diff(20)
    # Where today's volatility sits within its own two-year history.
    out["vol_pctile"] = out["vol_20"].rolling(504, min_periods=120).rank(pct=True)
    return out


def classify(frame: pd.DataFrame, at: int = -1) -> Regime:
    """Classify the market condition at one bar (default: the latest)."""
    annotated = frame if "vol_pctile" in frame.columns else annotate(frame)
    row = annotated.iloc[at]

    trend = _trend_label(row)
    volatility = _vol_label(row.get("vol_pctile", np.nan))
    momentum = _momentum_label(row.get("rsi_14", np.nan))
    participation = _participation_label(row)
    drawdown_state = _drawdown_label(row.get("drawdown", np.nan))

    readable_trend = trend.replace("_", " ")
    summary = (
        f"{readable_trend.capitalize()} with {volatility} volatility; "
        f"momentum {momentum}, {drawdown_state.replace('_', ' ')}."
    )
    return Regime(trend, volatility, momentum, participation, drawdown_state, summary)


def history(frame: pd.DataFrame) -> pd.DataFrame:
    """Vectorized trend/volatility labels for every bar, for charting."""
    annotated = annotate(frame)
    above_200 = annotated["dist_sma_200"]
    rising = annotated["sma_200_slope"] > 0
    trending = annotated["adx"] >= 25

    label = pd.Series("range", index=annotated.index, dtype=object)
    label[(above_200 > 0) & rising] = "uptrend"
    label[(above_200 > 0) & rising & trending & (annotated["dist_sma_50"] > 0)] = "strong_uptrend"
    label[(above_200 < 0) & ~rising] = "downtrend"
    label[(above_200 < 0) & ~rising & trending & (annotated["dist_sma_50"] < 0)] = "strong_downtrend"
    label[above_200.isna()] = "unknown"

    return pd.DataFrame(
        {"trend": label, "vol_pctile": annotated["vol_pctile"], "drawdown": annotated["drawdown"]}
    )
