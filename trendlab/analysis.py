"""High-level orchestration: one symbol in, one full report out.

This is the single entry point the CLI and the web API both call, so they can
never drift apart in what they compute or how they caveat it.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from . import backtest, explain, model, regime, signals
from .config import AppConfig
from .data import get_provider, resolve_provider_name
from .indicators import enrich

CHART_COLUMNS = [
    "open", "high", "low", "close", "volume",
    "sma_20", "sma_50", "sma_200",
    "bb_upper", "bb_lower",
    "rsi_14", "macd", "macd_signal", "macd_hist", "adx", "atr_pct", "volume_ratio",
]


def _series_payload(frame: pd.DataFrame, tail: int = 500) -> dict[str, Any]:
    """Trim to the charting window and JSON-ify, keeping NaN as null."""
    recent = frame.tail(tail)
    payload: dict[str, Any] = {"dates": [d.strftime("%Y-%m-%d") for d in recent.index]}
    for column in CHART_COLUMNS:
        if column not in recent.columns:
            continue
        payload[column] = [
            None if pd.isna(v) else round(float(v), 4) for v in recent[column]
        ]
    return payload


def analyze(
    symbol: str,
    period: str | None = None,
    provider: str = "auto",
    config: AppConfig | None = None,
    include_forecast: bool = True,
    chart_bars: int = 500,
) -> dict[str, Any]:
    """Run the full pipeline for one symbol.

    The forecast is optional and failure-tolerant: a symbol with a short history
    still gets indicators, regime and a backtest, with an explicit note saying
    why the forecast is absent rather than a fabricated one.
    """
    cfg = config or AppConfig()
    provider_name = resolve_provider_name(symbol, provider)
    kwargs = {"cache_dir": cfg.cache_dir} if provider_name in ("yahoo", "amfi") else {}
    source = get_provider(provider_name, **kwargs)

    raw = source.history(symbol, period=period or cfg.default_period)
    frame = enrich(raw)

    # Read the asset class off the data, not the provider: a NAV series and an
    # index with all-zero volume both lack usable volume, so their
    # volume-derived features and the accumulation/distribution label do not
    # apply. See market.asset_profile.
    from .market import asset_profile, display_name, quote_summary

    kind, has_volume = asset_profile(symbol, raw)

    current_regime = regime.classify(frame, has_volume=has_volume)
    fired = signals.evaluate(frame)

    trend_result = backtest.run(frame, signals.composite_position(frame), cfg.backtest)

    report: dict[str, Any] = {
        "symbol": symbol.upper(),
        "display_name": display_name(source, symbol),
        "asset_kind": kind,
        "has_volume": has_volume,
        "provider": source.name,
        "period": period or cfg.default_period,
        "bars": int(len(frame)),
        "as_of": frame.index[-1].strftime("%Y-%m-%d"),
        "last_close": round(float(frame["close"].iloc[-1]), 2),
        "change_1d": round(float(frame["ret_1"].iloc[-1]), 4),
        "change_21d": round(float(frame["ret_21"].iloc[-1]), 4) if not pd.isna(frame["ret_21"].iloc[-1]) else None,
        "regime": current_regime.to_dict(),
        "signals": [s.to_dict() for s in fired],
        "signal_score": signals.score(fired),
        "backtest": trend_result.to_dict(),
        "series": _series_payload(frame, chart_bars),
        "forecast": None,
        "forecast_error": None,
    }

    if source.name == "synthetic":
        report["data_warning"] = (
            "Synthetic data: these bars are randomly generated and describe no real "
            "market. Use this mode for demos and tests only."
        )

    if include_forecast:
        try:
            report["forecast"] = model.forecast(frame, cfg.model, has_volume).to_dict()
        except ValueError as exc:
            report["forecast_error"] = str(exc)

    quote = quote_summary(raw, kind, has_volume)
    report["explain"] = {
        "mood": explain.mood(report["regime"], quote["below_52w_high"]),
        "prediction": explain.prediction(report["forecast"], kind, report["forecast_error"])
        if include_forecast else None,
    }
    return report


def compare(symbols: list[str], period: str | None = None, provider: str = "auto",
            config: AppConfig | None = None) -> list[dict[str, Any]]:
    """Lightweight screen across several symbols (no forecast — that is the slow part)."""
    rows = []
    for symbol in symbols:
        try:
            report = analyze(symbol, period, provider, config, include_forecast=False, chart_bars=0)
            rows.append({
                "symbol": report["symbol"],
                "last_close": report["last_close"],
                "change_1d": report["change_1d"],
                "change_21d": report["change_21d"],
                "trend": report["regime"]["trend"],
                "volatility": report["regime"]["volatility"],
                "momentum": report["regime"]["momentum"],
                "bias": report["signal_score"]["bias"],
                "fired": report["signal_score"]["fired"],
            })
        except Exception as exc:
            rows.append({"symbol": symbol.upper(), "error": str(exc)})
    return rows
