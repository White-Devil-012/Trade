"""Command-line interface: trendlab analyze / screen / backtest / serve."""

from __future__ import annotations

import argparse
import json
import sys

from .analysis import analyze, compare
from .config import AppConfig

DISCLAIMER = (
    "TrendLab is research tooling, not financial advice. Past patterns do not "
    "predict future prices. Never trade on a signal you have not validated yourself."
)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2%}"


def _print_analysis(report: dict) -> None:
    r = report
    print(f"\n{'=' * 64}")
    print(f"  {r['symbol']}  —  {r['last_close']}  ({_pct(r['change_1d'])} today)")
    print(f"  {r['bars']} bars through {r['as_of']}  ·  source: {r['provider']}")
    print("=" * 64)

    if r.get("data_warning"):
        print(f"\n  ⚠ {r['data_warning']}")

    reg = r["regime"]
    print(f"\n MARKET CONDITION\n  {reg['summary']}")
    print(f"  trend={reg['trend']}  volatility={reg['volatility']}  "
          f"momentum={reg['momentum']}  flow={reg['participation']}")

    score = r["signal_score"]
    print(f"\n SIGNALS (last 10 bars) — bias: {score['bias']} "
          f"({score['bullish_count']} bullish / {score['bearish_count']} bearish)")
    active = [s for s in r["signals"] if s["fired"]]
    if not active:
        print("  nothing fired recently")
    for s in active:
        mark = "▲" if s["direction"] == "bullish" else "▼"
        print(f"  {mark} {s['name']} ({s['bars_since']}d ago) — {s['detail']}")

    bt, bench = r["backtest"]["metrics"], r["backtest"]["benchmark_metrics"]
    print(f"\n TREND-FOLLOWING BACKTEST (after costs)   strategy   buy & hold")
    for label, key in (("CAGR", "cagr"), ("Sharpe", "sharpe"),
                       ("Max drawdown", "max_drawdown"), ("Total return", "total_return")):
        fmt = (lambda v: f"{v:>9.2f}") if key == "sharpe" else (lambda v: f"{v:>9.2%}")
        print(f"  {label:<22}{fmt(bt[key])}  {fmt(bench[key])}")
    print(f"  {'Trades':<22}{r['backtest']['trades']:>9}")

    fc = r.get("forecast")
    if r.get("forecast_error"):
        print(f"\n FORECAST\n  unavailable: {r['forecast_error']}")
    elif fc:
        ev = fc["evaluation"]
        print(f"\n FORECAST ({fc['horizon_days']}-day horizon)")
        print(f"  P(higher in {fc['horizon_days']} sessions): {fc['probability_up']:.1%}"
              f"   confidence: {fc['confidence']}")
        pr = fc["price_range"]
        print(f"  Likely range (50%): {pr['likely_low']} – {pr['likely_high']}")
        print(f"  Wide range  (90%): {pr['low']} – {pr['high']}   centre: {pr['mid']}")
        print(f"\n  Out-of-sample record over {ev['n_predictions']} predictions:")
        print(f"    accuracy {ev['accuracy']:.1%} vs {ev['baseline_accuracy']:.1%} base rate "
              f"(edge {ev['edge']:+.1%})   AUC {ev['auc']:.3f}")
        print(f"    {ev['verdict']}")

    print(f"\n  {DISCLAIMER}\n")


def _print_screen(rows: list[dict]) -> None:
    header = f"{'SYMBOL':<12}{'CLOSE':>10}{'1D':>9}{'1M':>9}  {'TREND':<17}{'MOMENTUM':<13}{'BIAS':<9}"
    print("\n" + header)
    print("-" * len(header))
    for row in rows:
        if "error" in row:
            print(f"{row['symbol']:<12}{'error: ' + row['error'][:50]}")
            continue
        print(f"{row['symbol']:<12}{row['last_close']:>10.2f}{_pct(row['change_1d']):>9}"
              f"{_pct(row['change_21d']):>9}  {row['trend']:<17}{row['momentum']:<13}{row['bias']:<9}")
    print(f"\n  {DISCLAIMER}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trendlab", description=__doc__)
    parser.add_argument("--provider", default="yahoo", choices=["yahoo", "synthetic"],
                        help="synthetic generates fake bars for offline demos")
    parser.add_argument("--period", default=None, help="history window, e.g. 1y 2y 5y 10y")
    parser.add_argument("--json", action="store_true", help="emit raw JSON instead of a report")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="full report for one symbol")
    p_analyze.add_argument("symbol")
    p_analyze.add_argument("--no-forecast", action="store_true",
                           help="skip the model (much faster)")
    p_analyze.add_argument("--horizon", type=int, default=None,
                           help="forecast horizon in trading days")

    p_screen = sub.add_parser("screen", help="compare several symbols at a glance")
    p_screen.add_argument("symbols", nargs="+")

    p_serve = sub.add_parser("serve", help="launch the web dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn
        print(f"\n  TrendLab dashboard → http://{args.host}:{args.port}\n  {DISCLAIMER}\n")
        uvicorn.run("trendlab.server.app:app", host=args.host, port=args.port)
        return 0

    cfg = AppConfig()
    if args.command == "analyze" and args.horizon:
        from dataclasses import replace
        cfg = replace(cfg, model=replace(cfg.model, horizon_days=args.horizon))

    try:
        if args.command == "analyze":
            report = analyze(args.symbol, args.period, args.provider, cfg,
                             include_forecast=not args.no_forecast)
            print(json.dumps(report, indent=2)) if args.json else _print_analysis(report)
        else:
            rows = compare(args.symbols, args.period, args.provider, cfg)
            print(json.dumps(rows, indent=2)) if args.json else _print_screen(rows)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
