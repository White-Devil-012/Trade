"""Vectorized long/flat backtester with realistic frictions.

The one rule that matters here: a position decided from bar ``t``'s data is
entered at bar ``t+1``'s open and earns bar ``t+1``'s return onward. Anything
else silently trades on information you did not have, which is how a strategy
looks brilliant in a notebook and loses money live.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd

from .config import BacktestConfig

TRADING_DAYS = 252


@dataclass(frozen=True)
class BacktestResult:
    equity: pd.Series
    returns: pd.Series
    position: pd.Series
    trades: int
    metrics: dict[str, float]
    benchmark_equity: pd.Series
    benchmark_metrics: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics,
            "benchmark_metrics": self.benchmark_metrics,
            "trades": self.trades,
            "equity": {
                "dates": [d.strftime("%Y-%m-%d") for d in self.equity.index],
                "strategy": [round(float(v), 2) for v in self.equity.to_numpy()],
                "benchmark": [round(float(v), 2) for v in self.benchmark_equity.to_numpy()],
            },
        }


def compute_metrics(returns: pd.Series, equity: pd.Series) -> dict[str, float]:
    """Standard performance statistics from a series of periodic returns."""
    r = returns.dropna()
    if len(r) < 2 or equity.empty:
        return {k: 0.0 for k in
                ("total_return", "cagr", "volatility", "sharpe", "sortino",
                 "max_drawdown", "calmar", "win_rate", "best_day", "worst_day")}

    years = len(r) / TRADING_DAYS
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0) if years > 0 else 0.0

    vol = float(r.std(ddof=0) * np.sqrt(TRADING_DAYS))
    mean_annual = float(r.mean() * TRADING_DAYS)
    sharpe = mean_annual / vol if vol > 0 else 0.0

    downside = r[r < 0]
    downside_vol = float(downside.std(ddof=0) * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else 0.0
    sortino = mean_annual / downside_vol if downside_vol > 0 else 0.0

    max_dd = float((equity / equity.cummax() - 1.0).min())
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    # Win rate over *active* days only — flat days are not won or lost.
    active = r[r != 0]
    win_rate = float((active > 0).mean()) if len(active) else 0.0

    return {
        "total_return": round(total_return, 4),
        "cagr": round(cagr, 4),
        "volatility": round(vol, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_drawdown": round(max_dd, 4),
        "calmar": round(calmar, 3),
        "win_rate": round(win_rate, 4),
        "best_day": round(float(r.max()), 4),
        "worst_day": round(float(r.min()), 4),
    }


def run(
    frame: pd.DataFrame,
    position: pd.Series,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Backtest a target-position series (0..1 long, negative only if shorting).

    ``position`` is indexed like ``frame`` and holds the position *decided* on
    that bar. The lag is applied here, so callers pass the undelayed decision.
    """
    cfg = config or BacktestConfig()
    close = frame["close"].astype(float)
    if close.isna().all():
        # Usually an index mismatch upstream: returning a flat zero curve here
        # would look like a legitimate "never traded" result.
        raise ValueError("every close is NaN - check that the price frame is aligned to its index")
    bar_return = close.pct_change().fillna(0.0)

    target = position.reindex(frame.index).fillna(0.0).astype(float)
    if not cfg.allow_short:
        target = target.clip(lower=0.0)

    # The decision made on bar t governs exposure from bar t+lag onward.
    held = target.shift(cfg.execution_lag_bars).fillna(0.0)

    gross = held * bar_return

    # Costs are charged on the bar the position actually changes.
    turnover = held.diff().abs().fillna(held.abs())
    cost_rate = (cfg.commission_bps + cfg.slippage_bps) / 10_000.0
    costs = turnover * cost_rate

    net = gross - costs
    equity = cfg.initial_equity * (1.0 + net).cumprod()

    trades = int((held.diff().fillna(held) != 0).sum())

    benchmark_equity = cfg.initial_equity * (1.0 + bar_return).cumprod()

    return BacktestResult(
        equity=equity,
        returns=net,
        position=held,
        trades=trades,
        metrics=compute_metrics(net, equity),
        benchmark_equity=benchmark_equity,
        benchmark_metrics=compute_metrics(bar_return, benchmark_equity),
    )


def buy_and_hold(frame: pd.DataFrame, config: BacktestConfig | None = None) -> BacktestResult:
    ones = pd.Series(1.0, index=frame.index)
    return run(frame, ones, config)
