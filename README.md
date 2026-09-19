# TrendLab

A local web app and CLI for analysing price trends, reading market conditions, and
producing **probabilistic** forecasts with an honest track record attached.

Works with anything Yahoo Finance covers — US tickers (`AAPL`), Indian ones
(`RELIANCE.NS`, `TCS.NS`, `INFY.NS`), indices (`^NSEI`, `^GSPC`), FX and crypto.

---

## Read this first

**Nobody can predict a stock's price.** Anything that claims otherwise is selling
something. What *is* achievable is a small, measurable statistical edge that shows
up over hundreds of trades — and most of the time, on most symbols, even that is
not there.

TrendLab is built around that reality:

- It forecasts a **probability** and a **range**, never a price target.
- It validates **walk-forward** — trained only on the past at every step — because a
  shuffled train/test split on time-series data leaks the future into the past and
  inflates every metric it reports.
- It compares itself to a **base rate** (just guessing the more common outcome) and
  reports the difference. If the model cannot beat that, the dashboard says so in
  plain language instead of showing you a confident-looking number.
- Its backtester charges **commission and slippage** and enters on the **next** bar,
  so a signal never earns the move it was derived from.

The forecast panel is designed to be able to tell you "this is worthless." That is
the feature, not a shortcoming.

**This is research tooling, not financial advice.** Nothing here is a recommendation
to buy or sell. Validate anything you intend to risk money on.

---

## Install

```bash
git clone <this repo> && cd Trade
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.11+.

## Run the dashboard

```bash
python -m trendlab serve
# → http://127.0.0.1:8000
```

Enter a symbol, pick a history window and a forecast horizon, hit **Analyze**.

No network? Switch **Source** to *Synthetic (demo)* — it generates random
regime-switching bars so you can explore every panel offline. Those bars describe no
real market, and the app labels them as such.

## Use the CLI

```bash
python -m trendlab analyze RELIANCE.NS                  # full report
python -m trendlab analyze AAPL --horizon 21            # 21-day forecast
python -m trendlab analyze TSLA --no-forecast           # skip the slow part
python -m trendlab screen AAPL MSFT NVDA INFY.NS        # compare several
python -m trendlab --provider synthetic analyze DEMO    # offline demo
python -m trendlab analyze AAPL --json                  # machine-readable
```

## Use it as a library

```python
from trendlab.analysis import analyze

report = analyze("INFY.NS", period="5y")
print(report["regime"]["summary"])
print(report["forecast"]["evaluation"]["verdict"])
```

---

## What it gives you

### Market condition
Five labels computed from bars up to today only: **trend** (from the 200-day average,
its slope, and ADX), **volatility** (where 20-day realized vol sits in its own
two-year history), **momentum** (RSI), **volume flow** (OBV confirming or diverging
from price), and **drawdown state**.

Context beats prediction. RSI 30 in a quiet uptrend and RSI 30 in a high-volatility
bear market are not the same reading, and the regime panel is what lets you tell
them apart.

### Signals
Ten classic rules — golden/death cross, MACD crosses, RSI extremes, Bollinger
squeeze, 20-day breakouts and breakdowns, 200-day reclaims — each stated explicitly
so you can see exactly what fired and why. A count of signals is *not* an edge, which
is what the backtest panel is for.

### Backtest
A plain trend-following rule (long when price > 200-day **and** 50-day > 200-day,
flat otherwise) run against buy & hold, after costs. It is a yardstick: it tells you
whether trend-following has historically been worth anything *on this symbol*, and
often the answer is no.

Reported: total return, CAGR, volatility, Sharpe, Sortino, max drawdown, Calmar, win
rate on active days, and trade count.

### Forecast
- **P(higher in N sessions)** from a gradient-boosted classifier over 22 scale-free
  features (oscillators, distances to moving averages, volatility ratios, volume flow).
- **A plausible range** — the middle 50% and 90% of comparable historical moves,
  drawn from one probability-weighted mixture so the quantiles and the centre stay
  mutually consistent.
- **An out-of-sample scorecard**: accuracy vs base rate, the edge between them, ROC
  AUC, and Brier score against a base-rate-only Brier.
- **A calibration table**: when it says 70%, did 70% happen?
- **Permutation feature importance**: what the model actually leans on.

The edge gate is deliberately conjunctive — beating the base rate is not enough if
the probabilities are worse-calibrated than simply quoting that base rate. Both must
hold, or the dashboard reports no edge.

---

## How lookahead bias is kept out

Lookahead bias is silent. It does not raise an error or produce NaNs; it just makes a
strategy look profitable in a way it can never be live. Four structural defences:

1. **Every indicator is causal.** A value at bar `t` uses only bars `<= t`.
   `tests/test_indicators.py` recomputes each indicator on a truncated history and
   asserts the earlier values are unchanged.
2. **The forward return is the only forward-looking operation**, and it exists solely
   to create labels. The last `horizon` bars have no label and are never trained on.
3. **Walk-forward validation with a purge gap.** Training data ends at least
   `horizon` bars before each test block, so no training label overlaps the window
   being predicted. `tests/test_no_lookahead.py` monkeypatches `fit()` and asserts
   directly on the index range of every slice the model ever sees.
4. **Execution lag in the backtester.** A position decided on bar `t` is entered at
   `t+1`. There is a test with a single explosive day that fails if this slips.

There is also a canary: the synthetic provider generates a random walk, so a large
reported edge on synthetic data means the validation is leaking, not that something
was discovered. `test_reports_no_edge_on_random_data` enforces that.

## Tests

```bash
pip install pytest httpx
python -m pytest tests/ -q     # 57 tests
```

## Layout

```
trendlab/
  config.py         tunable defaults (costs, horizon, retrain cadence)
  data/             providers: yahoo (cached) + synthetic (offline)
  indicators.py     SMA/EMA/RSI/MACD/Bollinger/ATR/ADX/Stochastic/OBV — all causal
  features.py       22-feature matrix + forward-return target
  regime.py         market-condition classification
  signals.py        ten explicit rule-based setups
  backtest.py       vectorized long/flat backtester with costs and execution lag
  model.py          walk-forward validation, calibration, forecasting
  analysis.py       orchestration — one symbol in, one report out
  cli.py            command-line interface
  server/           FastAPI backend + dependency-free SVG dashboard
tests/              57 tests, concentrated on lookahead and backtest integrity
```

The dashboard's charts are hand-rolled SVG with no CDN dependency, so the app works
fully offline. Colours come from a CVD-validated categorical palette and the dark
theme is separately stepped rather than an automatic inversion.

## Extending it

- **Another data source:** implement `history(symbol, period, interval)` returning the
  canonical OHLCV frame and register it in `trendlab/data/yahoo.py::get_provider`.
  `normalize()` handles column naming, sorting and de-duplication.
- **Another indicator:** add a causal function to `indicators.py`, wire it into
  `enrich()`, then add it to `FEATURE_COLUMNS` in `features.py` if the model should
  see it.
- **Another strategy:** write a function returning a position series indexed like the
  price frame and pass it to `backtest.run`. Do not apply your own lag — the
  backtester does that.

## Known limits

- **Daily bars only.** No intraday, no order-book data, no tick data.
- **No fundamentals**, news, earnings dates or corporate actions beyond the
  split/dividend adjustment Yahoo applies.
- **Long/flat by default.** Shorting exists in the backtester but is off, and it
  models no borrow cost.
- **Single-asset.** No portfolio construction, position sizing or correlation.
- **Yahoo Finance is unofficial** and rate-limits. Responses are cached for six hours.
- **Costs are estimates.** The defaults (5bps commission + 5bps slippage per side)
  are deliberately pessimistic but will not match your broker.
