"""Deterministic fake market data for offline runs and tests.

A regime-switching geometric random walk with volatility clustering: enough
structure that indicators, the backtester and the charts all exercise realistic
shapes, while staying genuinely unpredictable. If the forecaster shows a large
edge on synthetic data, that is a bug in the forecaster, not a discovery.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import PriceHistory, normalize

_PERIOD_TO_DAYS = {
    "1y": 252, "2y": 504, "3y": 756, "5y": 1260, "10y": 2520, "max": 2520,
}


class SyntheticProvider:
    name = "synthetic"

    def history(self, symbol: str, period: str = "5y", interval: str = "1d") -> PriceHistory:
        n = _PERIOD_TO_DAYS.get(period, 1260)
        # Seed from the symbol so the same ticker always yields the same series.
        rng = np.random.default_rng(abs(hash(symbol)) % (2**32))

        # Three regimes: bull drift, choppy, bear. Persistent, not i.i.d.
        drifts = np.array([0.0007, 0.0000, -0.0009])
        vols = np.array([0.010, 0.013, 0.022])
        switch_prob = 0.012

        regime = 0
        daily_ret = np.empty(n)
        vol_state = vols[regime]
        for i in range(n):
            if rng.random() < switch_prob:
                regime = int(rng.integers(0, 3))
            # GARCH-ish: volatility drifts toward its regime target.
            vol_state = 0.94 * vol_state + 0.06 * vols[regime]
            daily_ret[i] = rng.normal(drifts[regime], vol_state)

        start_price = 50.0 + (abs(hash(symbol)) % 400)
        close = start_price * np.exp(np.cumsum(daily_ret))

        # Build an OHLC bar around each close that respects high >= max(o,c) etc.
        prev_close = np.concatenate([[start_price], close[:-1]])
        open_ = prev_close * (1 + rng.normal(0, 0.002, n))
        body_hi = np.maximum(open_, close)
        body_lo = np.minimum(open_, close)
        high = body_hi * (1 + np.abs(rng.normal(0, 0.004, n)))
        low = body_lo * (1 - np.abs(rng.normal(0, 0.004, n)))
        volume = rng.lognormal(mean=14.0, sigma=0.35, size=n) * (1 + 3 * np.abs(daily_ret))

        index = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
        frame = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=index,
        )
        return normalize(frame, symbol)
