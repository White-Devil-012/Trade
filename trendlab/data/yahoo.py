"""Yahoo Finance provider with a small on-disk cache.

Works for anything Yahoo covers: US tickers (``AAPL``), Indian ones
(``RELIANCE.NS``, ``TCS.NS``), indices (``^NSEI``, ``^GSPC``), FX and crypto.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd

from .base import DataError, PriceHistory, normalize

CACHE_TTL_SECONDS = 60 * 60 * 6  # intraday staleness is fine for daily bars


class YahooProvider:
    name = "yahoo"

    def __init__(self, cache_dir: str | os.PathLike[str] = ".cache/trendlab") -> None:
        self.cache_dir = Path(cache_dir)

    def _cache_path(self, symbol: str, period: str, interval: str) -> Path:
        safe = symbol.replace("/", "_").replace("^", "idx_")
        return self.cache_dir / f"{safe}__{period}__{interval}.parquet"

    def _read_cache(self, path: Path) -> PriceHistory | None:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        try:
            return pd.read_parquet(path)
        except Exception:
            return None

    def _write_cache(self, path: Path, frame: PriceHistory) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path)
        except Exception:
            # A cache miss is never worth failing a request over.
            pass

    def history(self, symbol: str, period: str = "5y", interval: str = "1d") -> PriceHistory:
        path = self._cache_path(symbol, period, interval)
        cached = self._read_cache(path)
        if cached is not None:
            return cached

        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise DataError("yfinance is not installed; run pip install -r requirements.txt") from exc

        raw = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        if raw is None or len(raw) == 0:
            # yfinance returns an empty frame for both a bad symbol and a
            # blocked connection, so the message has to cover both.
            raise DataError(
                f"Yahoo returned no data for {symbol!r}. Either the symbol is wrong "
                f"(Indian tickers need a .NS/.BO suffix, e.g. RELIANCE.NS) or the "
                f"connection to Yahoo is blocked. Switch Source to 'Demo data' to "
                f"check the app itself."
            )
        frame = normalize(raw, symbol)
        self._write_cache(path, frame)
        return frame


def resolve_provider_name(symbol: str, provider: str = "auto") -> str:
    """Pick a provider for a symbol.

    ``MF:118834`` or a bare AMFI scheme code routes to AMFI; everything else
    goes to Yahoo. Passing an explicit provider always wins.
    """
    if provider and provider != "auto":
        return provider
    token = str(symbol).strip().upper()
    if token.startswith("MF:") or token.isdigit():
        return "amfi"
    return "yahoo"


def get_provider(name: str = "yahoo", **kwargs):
    """Resolve a provider by name.

    ``synthetic`` exists so the app, its tests and the demo dashboard run with no
    network access at all. Synthetic bars are random — never read a signal off
    them as if it meant something about a real market.
    """
    key = (name or "yahoo").lower()
    if key == "yahoo":
        return YahooProvider(**kwargs)
    if key == "amfi":
        from .amfi import AmfiProvider

        return AmfiProvider(**kwargs)
    if key == "synthetic":
        from .synthetic import SyntheticProvider

        return SyntheticProvider()
    raise ValueError(f"unknown provider {name!r}; expected 'yahoo', 'amfi' or 'synthetic'")
