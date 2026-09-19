"""Rule-based signals.

These are the classic textbook rules, stated explicitly so you can see exactly
what fired and why. They are *not* a strategy on their own — ``backtest.py``
exists so you can check whether any of them ever paid for its costs on the
symbol you care about.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable

import pandas as pd


@dataclass(frozen=True)
class Signal:
    name: str
    direction: str       # bullish | bearish
    fired: bool
    detail: str
    # How many bars ago it last fired; None if it never has in the window.
    bars_since: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SignalRule = Callable[[pd.DataFrame], pd.Series]


def golden_cross(f: pd.DataFrame) -> pd.Series:
    return (f["sma_50"] > f["sma_200"]) & (f["sma_50"].shift(1) <= f["sma_200"].shift(1))


def death_cross(f: pd.DataFrame) -> pd.Series:
    return (f["sma_50"] < f["sma_200"]) & (f["sma_50"].shift(1) >= f["sma_200"].shift(1))


def macd_bullish_cross(f: pd.DataFrame) -> pd.Series:
    return (f["macd"] > f["macd_signal"]) & (f["macd"].shift(1) <= f["macd_signal"].shift(1))


def macd_bearish_cross(f: pd.DataFrame) -> pd.Series:
    return (f["macd"] < f["macd_signal"]) & (f["macd"].shift(1) >= f["macd_signal"].shift(1))


def rsi_oversold_exit(f: pd.DataFrame) -> pd.Series:
    """RSI climbing back out of oversold — the reversal, not the extreme itself."""
    return (f["rsi_14"] > 30) & (f["rsi_14"].shift(1) <= 30)


def rsi_overbought_entry(f: pd.DataFrame) -> pd.Series:
    return (f["rsi_14"] > 70) & (f["rsi_14"].shift(1) <= 70)


def bollinger_squeeze(f: pd.DataFrame) -> pd.Series:
    """Band width in its lowest decile of the past year: compression precedes expansion."""
    floor = f["bb_width"].rolling(252, min_periods=120).quantile(0.10)
    return f["bb_width"] <= floor


def breakout_20d(f: pd.DataFrame) -> pd.Series:
    prior_high = f["high"].rolling(20, min_periods=20).max().shift(1)
    return (f["close"] > prior_high) & (f["volume_ratio"] > 1.2)


def breakdown_20d(f: pd.DataFrame) -> pd.Series:
    prior_low = f["low"].rolling(20, min_periods=20).min().shift(1)
    return (f["close"] < prior_low) & (f["volume_ratio"] > 1.2)


def trend_reclaim(f: pd.DataFrame) -> pd.Series:
    return (f["close"] > f["sma_200"]) & (f["close"].shift(1) <= f["sma_200"].shift(1))


RULES: list[tuple[str, str, SignalRule, str]] = [
    ("Golden cross", "bullish", golden_cross, "50-day average crossed above the 200-day"),
    ("Death cross", "bearish", death_cross, "50-day average crossed below the 200-day"),
    ("MACD bullish cross", "bullish", macd_bullish_cross, "MACD crossed above its signal line"),
    ("MACD bearish cross", "bearish", macd_bearish_cross, "MACD crossed below its signal line"),
    ("RSI exiting oversold", "bullish", rsi_oversold_exit, "RSI recovered back above 30"),
    ("RSI entering overbought", "bearish", rsi_overbought_entry, "RSI pushed above 70"),
    ("Bollinger squeeze", "bullish", bollinger_squeeze, "Band width in its lowest decile — expansion often follows"),
    ("20-day breakout", "bullish", breakout_20d, "Closed above the 20-day high on above-average volume"),
    ("20-day breakdown", "bearish", breakdown_20d, "Closed below the 20-day low on above-average volume"),
    ("Reclaimed 200-day", "bullish", trend_reclaim, "Price crossed back above the 200-day average"),
]


def evaluate(frame: pd.DataFrame, lookback: int = 10) -> list[Signal]:
    """Report which rules fired within the last ``lookback`` bars."""
    signals: list[Signal] = []
    for name, direction, rule, detail in RULES:
        try:
            series = rule(frame).fillna(False).astype(bool)
        except KeyError:
            continue

        fired_idx = series.to_numpy().nonzero()[0]
        if len(fired_idx) == 0:
            signals.append(Signal(name, direction, False, detail, None))
            continue

        bars_since = len(series) - 1 - int(fired_idx[-1])
        signals.append(Signal(name, direction, bars_since <= lookback, detail, bars_since))
    return signals


def score(signals: list[Signal]) -> dict[str, Any]:
    """Net bullish/bearish tally of the currently-fired signals.

    A count, not a forecast: five bullish signals on a symbol where none of them
    has ever backtested well is still five bad reasons to buy.
    """
    bullish = [s for s in signals if s.fired and s.direction == "bullish"]
    bearish = [s for s in signals if s.fired and s.direction == "bearish"]
    net = len(bullish) - len(bearish)
    if net >= 2:
        bias = "bullish"
    elif net <= -2:
        bias = "bearish"
    else:
        bias = "mixed"
    return {
        "bullish_count": len(bullish),
        "bearish_count": len(bearish),
        "net": net,
        "bias": bias,
        "fired": [s.name for s in bullish + bearish],
    }


def composite_position(frame: pd.DataFrame) -> pd.Series:
    """A simple long/flat trend-following rule, for the backtester to evaluate.

    Long when price is above the 200-day average *and* the 50-day is above the
    200-day; flat otherwise. Deliberately plain — it is a yardstick, not advice.
    """
    long = (frame["close"] > frame["sma_200"]) & (frame["sma_50"] > frame["sma_200"])
    return long.fillna(False).astype(float)
