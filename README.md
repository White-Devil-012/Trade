# TrendLab

A local web app and CLI for analysing price trends, reading market conditions, and
producing **probabilistic** forecasts with an honest track record attached.

Built for the Indian market: NSE/BSE equities, Indian indices, and mutual fund NAVs
from AMFI. Search by name — you don't need to know the ticker. Anything else Yahoo
Finance covers (US tickers, FX, crypto) works too.

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

## Hosting it

Everything ships in one container. `/api/health` is the health check, `$PORT` is
honoured, and price data is cached on a `/data` volume.

### On your own machine

```bash
docker compose up --build        # http://localhost:8000
```

### On the internet

| Option | Cost | Good for | Catch |
|---|---|---|---|
| **Render** | Free tier | Easiest: `render.yaml` is included | Sleeps after ~15 idle min; first visit then takes ~1 min |
| **Hugging Face Spaces** (Docker) | Free | Generous CPU and RAM for an ML demo | Public by default; set `app_port: 8000` in the Space's README |
| **Railway / Fly.io** | ~$5/mo | Always on, no cold starts | Needs a card |
| **Any VPS** | ~$5/mo | Full control | You patch and secure it yourself |

**Render, step by step:** push to GitHub, then on render.com choose *New → Blueprint*,
pick this repo, and set `TRENDLAB_PASSWORD` when prompted. Render rebuilds on every push
to `main`.

**Not suitable:** Vercel, Netlify and GitHub Pages. They run serverless functions or
static files; a forecast needs ~10 seconds of CPU and a disk cache.

### Lock it down before it is public

Every forecast costs about 10 seconds of CPU and a share of Yahoo's rate limit, and
there are no user accounts. Set a password and the whole app sits behind HTTP Basic
Auth:

```bash
docker run -p 8000:8000 -e TRENDLAB_PASSWORD='choose-one' trendlab
# username defaults to "trendlab"; change it with TRENDLAB_USER
```

`/api/health` stays open so the platform's probes keep working.

### The biggest risk: Yahoo and cloud IPs

Yahoo throttles datacenter IP ranges far harder than home connections. The same
`RELIANCE.NS` request that works on your laptop can return `429 Too Many Requests`, or
nothing at all, from a cloud host. The six-hour disk cache absorbs repeat requests,
but a busy instance on a shared IP may still be refused. Test a few symbols right
after deploying. If Yahoo refuses the host, the dependable fix is a keyed data
provider. Mutual funds are unaffected; AMFI and mfapi do not throttle this way.

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | Port to listen on; hosting platforms set it |
| `TRENDLAB_PASSWORD` | unset (open) | Turns on HTTP Basic Auth |
| `TRENDLAB_USER` | `trendlab` | Basic Auth username |
| `TRENDLAB_CACHE_DIR` | `.cache/trendlab` | Where fetched prices are cached |

## Use the CLI

```bash
python -m trendlab search hdfc                          # find a symbol or fund
python -m trendlab analyze RELIANCE.NS                  # full report
python -m trendlab analyze MF:118834                    # a mutual fund, by AMFI code
python -m trendlab analyze ^NSEI --horizon 21           # 21-day forecast on NIFTY 50
python -m trendlab analyze TCS.NS --no-forecast         # skip the slow part
python -m trendlab screen RELIANCE.NS INFY.NS TCS.NS    # compare several
python -m trendlab --provider synthetic analyze DEMO    # offline demo
python -m trendlab analyze RELIANCE.NS --json           # machine-readable
```

## Use it as a library

```python
from trendlab.analysis import analyze

report = analyze("INFY.NS", period="5y")
print(report["regime"]["summary"])
print(report["forecast"]["evaluation"]["verdict"])
```

---

## Where the data comes from

| Asset | Source | Notes |
|---|---|---|
| NSE/BSE equities, indices | Yahoo Finance via `yfinance` | Unofficial. No API key, no SLA; rate-limits, and has broken before when Yahoo changed things. |
| Indian mutual funds | AMFI (scheme list) + `api.mfapi.in` (NAV history) | AMFI is the official source but publishes only the current day; mfapi mirrors it with history. |
| Offline demo & tests | Built-in synthetic generator | Random regime-switching bars. Describes no real market. |

Symbol search works across all of them. A built-in index of the NIFTY 50 and the major
Indian indices is matched locally, so searching "reliance" or "nifty" returns an answer
even when Yahoo's unofficial search endpoint is down.

```
python -m trendlab search hdfc bank
SYMBOL          KIND     WHERE   NAME
HDFCBANK.NS     equity   NSE     HDFC Bank
^NSEBANK        index    INDEX   NIFTY Bank
MF:118834       fund     AMFI    HDFC Top 100 Fund - Growth
```

### Funds are not stocks

A mutual fund prices once a day at a single NAV. There is no open, high, low or traded
volume. Rather than fabricate an OHLC bar, every price column is set to the NAV and
volume is left missing — and the pipeline drops the volume-derived features for that
asset instead of training on nothing.

This matters more than it sounds: leaving those features in makes every row incomplete
and the training set comes back **empty**. `tests/test_funds.py` asserts both halves —
that the volume feature set erases a fund's dataset, and that the price-only set does
not.

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
python -m pytest tests/ -q     # 114 tests
```

## Layout

```
trendlab/
  config.py         tunable defaults (costs, horizon, retrain cadence)
  data/             providers: yahoo (cached), amfi (fund NAVs), synthetic (offline)
  search.py         symbol + scheme search, with an offline Indian index
  indicators.py     SMA/EMA/RSI/MACD/Bollinger/ATR/ADX/Stochastic/OBV — all causal
  features.py       22-feature matrix + forward-return target
  regime.py         market-condition classification
  signals.py        ten explicit rule-based setups
  backtest.py       vectorized long/flat backtester with costs and execution lag
  model.py          walk-forward validation, calibration, forecasting
  analysis.py       orchestration — one symbol in, one report out
  cli.py            command-line interface
  server/           FastAPI backend + dependency-free SVG dashboard
tests/              114 tests, concentrated on lookahead and backtest integrity
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
- **Fund NAVs lag.** AMFI publishes end-of-day; there is no intraday NAV to have.
- **`api.mfapi.in` is a community mirror**, not an official AMFI endpoint. It is the
  practical source for NAV history, but it is someone's free service.
- **Yahoo Finance is unofficial** and rate-limits. Responses are cached for six hours.
- **Costs are estimates.** The defaults (5bps commission + 5bps slippage per side)
  are deliberately pessimistic but will not match your broker.
