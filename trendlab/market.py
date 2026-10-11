"""Data for the consumer-facing screens: market overview, stock pages, returns.

Everything here is fast. The slow walk-forward forecast stays in analysis.py,
so a stock page can show its price and chart immediately and fill in the
forecast when it is ready.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable

import pandas as pd

from . import explain, regime
from .config import AppConfig
from .data import get_provider, resolve_provider_name
from .data.base import DataError, normalize
from .indicators import enrich
from .search import lookup_name

INDICES = ["^NSEI", "^BSESN", "^NSEBANK"]
POPULAR_STOCKS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "BHARTIARTL.NS",
    "SBIN.NS", "ITC.NS", "LT.NS", "HINDUNILVR.NS", "BAJFINANCE.NS", "MARUTI.NS",
    "SUNPHARMA.NS", "TITAN.NS", "WIPRO.NS", "ADANIENT.NS",
]

# Calendar offsets, not bar counts. Indian markets trade ~248 days a year, so a
# 1,260-bar "5 years" would overshoot five calendar years of real data and the
# 5Y figure would never appear.
RANGES = {
    "1W": pd.DateOffset(weeks=1), "1M": pd.DateOffset(months=1), "3M": pd.DateOffset(months=3),
    "6M": pd.DateOffset(months=6), "1Y": pd.DateOffset(years=1), "3Y": pd.DateOffset(years=3),
    "5Y": pd.DateOffset(years=5),
}
ANNUALISED = {"3Y": 3, "5Y": 5}
# A window still counts if history starts within this long after its start
# date: "5 years ago" is often a weekend or a holiday.
RANGE_SLACK = pd.Timedelta(days=7)

OVERVIEW_TTL = 600  # seconds; the home screen does not need tick-by-tick prices
_overview_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_overview_lock = threading.Lock()


# ------------------------------------------------------------ asset class
def asset_profile(symbol: str, frame: pd.DataFrame) -> tuple[str, bool]:
    """Return (kind, has_volume) for a price frame.

    Volume counts only if most bars actually have some. A fund's NaN volume and
    the all-zero volume Yahoo reports for many Indian indices are both
    "no volume": volume features computed from zeros come out as 0/0, every
    feature row goes incomplete, and the model is left with nothing to learn.
    """
    volume = frame["volume"].fillna(0.0)
    has_volume = bool(len(volume) and (volume > 0).mean() > 0.5)

    key = str(symbol).strip().upper()
    if key.startswith("^"):
        kind = "index"
    elif key.startswith("MF:") or key.isdigit() or not has_volume:
        kind = "fund"
    else:
        kind = "equity"
    return kind, has_volume


def exchange_label(symbol: str, kind: str) -> str:
    if kind == "fund":
        return "Mutual fund"
    if kind == "index":
        return "Index"
    key = str(symbol).upper()
    if key.endswith(".NS"):
        return "NSE"
    if key.endswith(".BO"):
        return "BSE"
    return ""


def display_name(source: Any, symbol: str) -> str:
    """The name people know a stock or fund by, falling back to its ticker."""
    known = lookup_name(symbol)
    if known:
        return known
    if getattr(source, "name", "") == "amfi":
        try:
            return source.meta(symbol).get("scheme_name") or symbol.upper()
        except Exception:
            pass
    root = str(symbol).upper().split(".")[0].lstrip("^").removeprefix("MF:")
    return root


# ------------------------------------------------------------- numbers
def _f(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), 2)


def quote_summary(frame: pd.DataFrame, kind: str, has_volume: bool) -> dict[str, Any]:
    """The figures a stock page leads with. Fields that do not apply are None."""
    close = frame["close"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else last
    year = frame[frame.index > frame.index[-1] - RANGES["1Y"]]

    # A fund prices once a day: its "open", "high" and "low" are just the NAV
    # again, so showing them would only invent precision.
    intraday = kind != "fund"
    high_52w = float(year["high" if intraday else "close"].max())
    low_52w = float(year["low" if intraday else "close"].min())

    return {
        "price": _f(last),
        "prev_close": _f(prev),
        "change": _f(last - prev),
        "change_pct": round((last / prev - 1.0), 6) if prev else 0.0,
        "open": _f(frame["open"].iloc[-1]) if intraday else None,
        "day_high": _f(frame["high"].iloc[-1]) if intraday else None,
        "day_low": _f(frame["low"].iloc[-1]) if intraday else None,
        "high_52w": _f(high_52w),
        "low_52w": _f(low_52w),
        "below_52w_high": round(last / high_52w - 1.0, 6) if high_52w else None,
        "volume": int(frame["volume"].iloc[-1]) if has_volume and not pd.isna(frame["volume"].iloc[-1]) else None,
        "as_of": frame.index[-1].strftime("%Y-%m-%d"),
    }


def period_returns(close: pd.Series) -> dict[str, dict[str, Any] | None]:
    """Return over each standard range; 3Y and 5Y annualised, as funds quote them.

    None where the history is too short, rather than a figure over a shorter
    window wearing a longer window's label.
    """
    out: dict[str, dict[str, Any] | None] = {}
    last = float(close.iloc[-1])
    end = close.index[-1]
    for label, offset in RANGES.items():
        target = end - offset
        if close.index[0] > target + RANGE_SLACK:
            out[label] = None
            continue
        # The last close on or before the window's start date.
        start = float(close[close.index <= target].iloc[-1]) if (close.index <= target).any() \
            else float(close.iloc[0])
        total = last / start - 1.0
        years = ANNUALISED.get(label)
        if years:
            out[label] = {"value": round((1.0 + total) ** (1.0 / years) - 1.0, 6), "annualised": True}
        else:
            out[label] = {"value": round(total, 6), "annualised": False}
    return out


# --------------------------------------------------------- batch fetch
def split_download(raw: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Split a multi-ticker ``yfinance.download`` frame into per-symbol frames.

    yfinance puts tickers on either column level depending on ``group_by`` and
    version, so the ticker level is detected rather than assumed. A ticker that
    failed upstream comes back as all-NaN columns and is simply left out.
    """
    if raw is None or len(raw) == 0:
        return {}

    out: dict[str, pd.DataFrame] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = {str(v) for v in raw.columns.get_level_values(0)}
        ticker_level = 0 if any(s in level0 for s in symbols) else 1
        for symbol in symbols:
            try:
                sub = raw.xs(symbol, axis=1, level=ticker_level)
                out[symbol] = normalize(sub, symbol)
            except (KeyError, DataError):
                continue
    elif len(symbols) == 1:
        try:
            out[symbols[0]] = normalize(raw, symbols[0])
        except DataError:
            pass
    return out


def fetch_many(source: Any, symbols: Iterable[str], period: str = "3mo") -> dict[str, pd.DataFrame]:
    """History for several symbols. One failure never sinks the rest."""
    symbols = list(symbols)
    if hasattr(source, "history_many"):
        try:
            return source.history_many(symbols, period=period)
        except Exception:
            pass

    def one(symbol: str) -> tuple[str, pd.DataFrame | None]:
        try:
            return symbol, source.history(symbol, period=period)
        except Exception:
            return symbol, None

    # A handful of workers: enough to be quick, few enough not to trip Yahoo.
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, symbols))
    return {s: f for s, f in results if f is not None and len(f)}


# ------------------------------------------------------------- screens
def _tile(symbol: str, frame: pd.DataFrame, source: Any) -> dict[str, Any]:
    kind, has_volume = asset_profile(symbol, frame)
    q = quote_summary(frame, kind, has_volume)
    spark = frame["close"].tail(30)
    return {
        "symbol": symbol,
        "name": display_name(source, symbol),
        "kind": kind,
        "exchange": exchange_label(symbol, kind),
        "price": q["price"],
        "change": q["change"],
        "change_pct": q["change_pct"],
        "as_of": q["as_of"],
        "spark": [round(float(v), 2) for v in spark],
    }


def market_overview(provider: str = "auto", config: AppConfig | None = None,
                    use_cache: bool = True) -> dict[str, Any]:
    """Indices and popular stocks for the home screen, cached for ten minutes."""
    cfg = config or AppConfig()
    key = provider
    now = time.time()
    if use_cache:
        with _overview_lock:
            hit = _overview_cache.get(key)
            if hit and now - hit[0] < OVERVIEW_TTL:
                return hit[1]

    # "auto" means Yahoo here: every symbol on the home screen is an equity or index.
    name = "synthetic" if provider == "synthetic" else "yahoo"
    source = get_provider(name, **({"cache_dir": cfg.cache_dir} if name == "yahoo" else {}))

    wanted = INDICES + POPULAR_STOCKS
    frames = fetch_many(source, wanted, period="3mo")

    indices = [_tile(s, frames[s], source) for s in INDICES if s in frames]
    stocks = [_tile(s, frames[s], source) for s in POPULAR_STOCKS if s in frames]
    if not indices and not stocks:
        raise DataError("could not load any market prices")

    result: dict[str, Any] = {
        "indices": indices,
        "stocks": stocks,
        "missing": [s for s in wanted if s not in frames],
        "demo": source.name == "synthetic",
    }
    with _overview_lock:
        _overview_cache[key] = (now, result)
    return result


def stock_snapshot(symbol: str, provider: str = "auto", period: str = "5y",
                   config: AppConfig | None = None) -> dict[str, Any]:
    """Everything a stock or fund page shows before the forecast arrives."""
    cfg = config or AppConfig()
    provider_name = resolve_provider_name(symbol, provider)
    kwargs = {"cache_dir": cfg.cache_dir} if provider_name in ("yahoo", "amfi") else {}
    source = get_provider(provider_name, **kwargs)

    raw = source.history(symbol, period=period)
    kind, has_volume = asset_profile(symbol, raw)
    frame = enrich(raw)
    current = regime.classify(frame, has_volume=has_volume)
    quote = quote_summary(raw, kind, has_volume)

    return {
        "symbol": symbol.upper(),
        "name": display_name(source, symbol),
        "kind": kind,
        "exchange": exchange_label(symbol, kind),
        "demo": source.name == "synthetic",
        "quote": quote,
        "returns": period_returns(raw["close"]),
        "history": {
            "dates": [d.strftime("%Y-%m-%d") for d in raw.index],
            "close": [round(float(v), 2) for v in raw["close"]],
        },
        "mood": explain.mood(current.to_dict(), quote["below_52w_high"]),
    }
