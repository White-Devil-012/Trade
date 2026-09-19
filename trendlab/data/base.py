"""Provider interface.

Every provider returns the same frame so the rest of the package never needs to
know where the bars came from: a DatetimeIndex named ``date`` plus the columns
``open``, ``high``, ``low``, ``close``, ``volume``, sorted ascending and free of
duplicate dates.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

PriceHistory = pd.DataFrame


class DataError(RuntimeError):
    """Raised when a provider cannot return usable bars."""


@runtime_checkable
class DataProvider(Protocol):
    name: str

    def history(self, symbol: str, period: str = "5y", interval: str = "1d") -> PriceHistory:
        ...


def normalize(frame: pd.DataFrame, symbol: str) -> PriceHistory:
    """Coerce a raw provider frame into the canonical shape."""
    if frame is None or len(frame) == 0:
        raise DataError(f"no price data returned for {symbol!r}")

    out = frame.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]

    missing = [c for c in OHLCV_COLUMNS if c not in out.columns]
    if missing:
        raise DataError(f"{symbol!r} is missing columns {missing}")

    out = out[OHLCV_COLUMNS].astype("float64")
    out.index = pd.to_datetime(out.index).tz_localize(None)
    out.index.name = "date"
    out = out[~out.index.duplicated(keep="last")].sort_index()

    # Rows with no close are unusable; forward-filling a price would invent data.
    out = out[out["close"].notna() & (out["close"] > 0)]
    if len(out) == 0:
        raise DataError(f"{symbol!r} returned only empty or non-positive closes")
    return out
