"""Technical indicators.

Every function here is causal: the value at bar ``t`` uses only bars ``<= t``.
That is what makes the feature matrix in ``features.py`` safe to train on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def returns(series: pd.Series, periods: int = 1) -> pd.Series:
    return series.pct_change(periods)


def log_returns(series: pd.Series, periods: int = 1) -> pd.Series:
    return np.log(series).diff(periods)


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI. Returns 0-100; 50 means no net pressure."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing is an EMA with alpha = 1/window.
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    # A window with no losses at all is a pegged-high RSI, not a missing value.
    return out.where(avg_loss.ne(0.0) | avg_gain.isna(), 100.0)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, window: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    mid = sma(close, window)
    sd = close.rolling(window, min_periods=window).std(ddof=0)
    upper, lower = mid + n_std * sd, mid - n_std * sd
    width = (upper - lower) / mid.replace(0.0, np.nan)
    # Where in the band the price sits: 0 = lower band, 1 = upper band.
    pct_b = (close - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width, "bb_pct": pct_b}
    )


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.DataFrame:
    """Directional movement. ADX above ~25 is the usual 'trending' threshold."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    alpha = 1 / window
    atr_ = atr(high, low, close, window)
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(
        alpha=alpha, adjust=False, min_periods=window
    ).mean() / atr_.replace(0.0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(
        alpha=alpha, adjust=False, min_periods=window
    ).mean() / atr_.replace(0.0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return pd.DataFrame(
        {"plus_di": plus_di, "minus_di": minus_di,
         "adx": dx.ewm(alpha=alpha, adjust=False, min_periods=window).mean()}
    )


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, k_window: int = 14, d_window: int = 3
) -> pd.DataFrame:
    lowest = low.rolling(k_window, min_periods=k_window).min()
    highest = high.rolling(k_window, min_periods=k_window).max()
    k = 100 * (close - lowest) / (highest - lowest).replace(0.0, np.nan)
    return pd.DataFrame({"stoch_k": k, "stoch_d": k.rolling(d_window, min_periods=d_window).mean()})


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-balance volume: volume signed by the day's direction, accumulated."""
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum()


def realized_volatility(close: pd.Series, window: int = 20, annualize: bool = True) -> pd.Series:
    vol = log_returns(close).rolling(window, min_periods=window).std(ddof=0)
    return vol * np.sqrt(252) if annualize else vol


def rolling_drawdown(close: pd.Series) -> pd.Series:
    """Current drawdown from the running peak, as a negative fraction."""
    return close / close.cummax() - 1.0


def distance_from(series: pd.Series, reference: pd.Series) -> pd.Series:
    """Relative gap to a reference line, e.g. price vs its 200-day average."""
    return series / reference.replace(0.0, np.nan) - 1.0


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach the full indicator set to an OHLCV frame."""
    out = frame.copy()
    close, high, low, volume = out["close"], out["high"], out["low"], out["volume"]

    for w in (10, 20, 50, 200):
        out[f"sma_{w}"] = sma(close, w)
    for s in (12, 26):
        out[f"ema_{s}"] = ema(close, s)

    out["rsi_14"] = rsi(close, 14)
    out = out.join(macd(close))
    out = out.join(bollinger(close))
    out = out.join(adx(high, low, close))
    out = out.join(stochastic(high, low, close))

    out["atr_14"] = atr(high, low, close, 14)
    out["atr_pct"] = out["atr_14"] / close
    out["obv"] = obv(close, volume)
    out["vol_20"] = realized_volatility(close, 20)
    out["vol_60"] = realized_volatility(close, 60)
    out["drawdown"] = rolling_drawdown(close)
    out["dist_sma_50"] = distance_from(close, out["sma_50"])
    out["dist_sma_200"] = distance_from(close, out["sma_200"])
    out["volume_ratio"] = volume / volume.rolling(20, min_periods=20).mean()
    out["ret_1"] = returns(close, 1)
    for w in (5, 21, 63, 126):
        out[f"ret_{w}"] = returns(close, w)
    return out
