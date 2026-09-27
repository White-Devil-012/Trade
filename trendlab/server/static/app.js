import { lineChart, barChart, rangeBar, legend, fmtNum, fmtPct, fmtSignedPct } from './charts.js';

const S = { 1: 'var(--series-1)', 2: 'var(--series-2)', 3: 'var(--series-3)', 4: 'var(--series-4)' };
const $ = (id) => document.getElementById(id);

/* ---------------- theme ---------------- */
const savedTheme = (() => {
  try { return localStorage.getItem('trendlab-theme'); } catch { return null; }
})();
if (savedTheme) document.documentElement.setAttribute('data-theme', savedTheme);

$('theme').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme');
  const isDark = current
    ? current === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  const next = isDark ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('trendlab-theme', next); } catch { /* private mode */ }
  if (lastReport) render(lastReport); // SVG strokes resolve tokens at paint time
});

/* ---------------- symbol search ---------------- */
const escHtml = (t) => String(t).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const suggestBox = $('suggest');
let suggestions = [];
let activeIndex = -1;
let searchTimer = null;

function closeSuggest() {
  suggestBox.hidden = true;
  suggestBox.innerHTML = '';
  suggestions = [];
  activeIndex = -1;
  $('symbol').setAttribute('aria-expanded', 'false');
}

function renderSuggest() {
  if (!suggestions.length) {
    suggestBox.innerHTML = '<li class="s-empty">No match. Try the ticker itself, e.g. RELIANCE.NS</li>';
  } else {
    suggestBox.innerHTML = suggestions.map((r, i) => `
      <li role="option" data-i="${i}" aria-selected="${i === activeIndex}">
        <span class="s-sym">${escHtml(r.symbol)}</span>
        <span class="s-name">${escHtml(r.name)}</span>
        <span class="s-kind">${escHtml(r.kind === 'fund' ? 'mutual fund' : r.exchange || r.kind)}</span>
      </li>`).join('');
  }
  suggestBox.hidden = false;
  $('symbol').setAttribute('aria-expanded', 'true');
}

function choose(i) {
  const hit = suggestions[i];
  if (!hit) return;
  $('symbol').value = hit.symbol;
  closeSuggest();
  run();
}

suggestBox.addEventListener('mousedown', (e) => {
  // mousedown, not click: blur would close the list before click lands.
  const li = e.target.closest('li[data-i]');
  if (li) { e.preventDefault(); choose(Number(li.dataset.i)); }
});

$('symbol').addEventListener('input', () => {
  const q = $('symbol').value.trim();
  clearTimeout(searchTimer);
  if (q.length < 2) { closeSuggest(); return; }
  searchTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      if (!res.ok) return closeSuggest();
      suggestions = (await res.json()).results || [];
      activeIndex = -1;
      renderSuggest();
    } catch {
      closeSuggest();
    }
  }, 180);
});

$('symbol').addEventListener('keydown', (e) => {
  if (suggestBox.hidden) {
    if (e.key === 'Enter') run();
    return;
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const step = e.key === 'ArrowDown' ? 1 : -1;
    activeIndex = (activeIndex + step + suggestions.length) % (suggestions.length || 1);
    renderSuggest();
    suggestBox.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter') {
    e.preventDefault();
    activeIndex >= 0 ? choose(activeIndex) : (closeSuggest(), run());
  } else if (e.key === 'Escape') {
    closeSuggest();
  }
});

$('symbol').addEventListener('blur', () => setTimeout(closeSuggest, 120));

/* ---------------- helpers ---------------- */
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const title = (s) => String(s).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

function deltaClass(v) { return v > 0 ? 'up' : v < 0 ? 'down' : ''; }

const TREND_STATUS = {
  strong_uptrend: 'var(--good)', uptrend: 'var(--good)', range: 'var(--text-muted)',
  downtrend: 'var(--critical)', strong_downtrend: 'var(--critical)', unknown: 'var(--text-muted)',
};
const VOL_STATUS = {
  low: 'var(--good)', normal: 'var(--text-muted)', high: 'var(--warning)',
  extreme: 'var(--critical)', unknown: 'var(--text-muted)',
};

function statusDot(color) { return `<span class="dot" style="background:${color}"></span>`; }

/* ---------------- fetching ---------------- */
let lastReport = null;

async function run() {
  const symbol = $('symbol').value.trim();
  if (!symbol) return;

  $('run').disabled = true;
  $('report').hidden = true;
  $('status').innerHTML = `<div class="notice"><span class="icon">⟳</span>
    <span>Fetching ${esc(symbol)} and running walk-forward validation — this takes a few seconds.</span></div>`;

  const params = new URLSearchParams({
    symbol, period: $('period').value, provider: $('provider').value, horizon: $('horizon').value,
  });

  try {
    const res = await fetch(`/api/analyze?${params}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `request failed (${res.status})`);
    $('status').innerHTML = '';
    lastReport = body;
    render(body);
    $('report').hidden = false;
  } catch (err) {
    $('status').innerHTML = `<div class="notice error"><span class="icon">×</span>
      <span>${esc(err.message)}</span></div>`;
  } finally {
    $('run').disabled = false;
  }
}

$('run').addEventListener('click', run);

/* ---------------- rendering ---------------- */
function render(r) {
  const s = r.series;
  const fc = r.forecast;

  $('report').innerHTML = `
    ${r.data_warning ? `<div class="notice warn"><span class="icon">⚠</span><span>${esc(r.data_warning)}</span></div>` : ''}
    ${tilesHtml(r)}
    ${regimeHtml(r)}
    <section class="panel">
      <h2>${r.asset_kind === 'fund' ? 'NAV &amp; moving averages' : 'Price &amp; moving averages'}</h2>
      <p class="sub">${esc(r.display_name || r.symbol)} · ${s.dates.length} sessions to ${esc(r.as_of)}. Shaded band = Bollinger (20, 2σ).</p>
      <div class="legend" id="lg-price"></div>
      <div class="chart" id="ch-price"></div>
      ${r.has_volume ? '<h2 style="margin-top:18px">Volume</h2><div class="chart" id="ch-volume"></div>' : ''}
    </section>
    <div class="grid-2">
      <section class="panel">
        <h2>RSI (14)</h2>
        <p class="sub">Above 70 is stretched, below 30 is washed out — in a strong trend it can stay pinned for weeks.</p>
        <div class="chart" id="ch-rsi"></div>
      </section>
      <section class="panel">
        <h2>MACD (12, 26, 9)</h2>
        <p class="sub">Histogram is the gap between the MACD line and its signal.</p>
        <div class="legend" id="lg-macd"></div>
        <div class="chart" id="ch-macd"></div>
        <div class="chart" id="ch-macdh"></div>
      </section>
    </div>
    ${signalsHtml(r)}
    ${backtestHtml(r)}
    ${fc ? forecastHtml(fc) : forecastMissingHtml(r)}
    ${tableHtml(r)}
  `;

  drawPrice(s);
  if (r.has_volume) drawVolume(s);
  drawRsi(s);
  drawMacd(s);
  drawEquity(r.backtest.equity);
  if (fc) {
    rangeBar($('ch-range'), {
      low: fc.price_range.low, likelyLow: fc.price_range.likely_low, mid: fc.price_range.mid,
      likelyHigh: fc.price_range.likely_high, high: fc.price_range.high, last: fc.last_close,
    });
  }
}

/* ---------------- sections ---------------- */
function tilesHtml(r) {
  return `<div class="tiles">
    <div class="tile"><div class="label">Last close</div>
      <div class="value">${fmtNum(r.last_close)}</div>
      <div class="note">${r.asset_kind === 'fund' ? 'NAV · ' : ''}as of ${esc(r.as_of)}</div></div>
    <div class="tile"><div class="label">1 day</div>
      <div class="value ${deltaClass(r.change_1d)}">${fmtSignedPct(r.change_1d)}</div>
      <div class="note">session change</div></div>
    <div class="tile"><div class="label">1 month</div>
      <div class="value ${deltaClass(r.change_21d)}">${fmtSignedPct(r.change_21d)}</div>
      <div class="note">21 sessions</div></div>
    <div class="tile"><div class="label">Signal bias</div>
      <div class="value">${title(r.signal_score.bias)}</div>
      <div class="note">${r.signal_score.bullish_count} bullish · ${r.signal_score.bearish_count} bearish</div></div>
  </div>`;
}

function regimeHtml(r) {
  const g = r.regime;
  return `<section class="panel">
    <h2>Market condition</h2>
    <p class="sub">${esc(g.summary)}</p>
    <div class="badges">
      <span class="badge">${statusDot(TREND_STATUS[g.trend] || 'var(--text-muted)')}<span class="k">Trend</span><span class="v">${title(g.trend)}</span></span>
      <span class="badge">${statusDot(VOL_STATUS[g.volatility] || 'var(--text-muted)')}<span class="k">Volatility</span><span class="v">${title(g.volatility)}</span></span>
      <span class="badge"><span class="k">Momentum</span><span class="v">${title(g.momentum)}</span></span>
      ${g.participation === 'not_applicable' ? '' :
        `<span class="badge"><span class="k">Volume flow</span><span class="v">${title(g.participation)}</span></span>`}
      <span class="badge"><span class="k">Drawdown</span><span class="v">${title(g.drawdown_state)}</span></span>
    </div>
    <p class="small muted">Regime is descriptive, not predictive: it tells you which playbook the
    market is currently in, so a signal can be read in context rather than in isolation.</p>
  </section>`;
}

function signalsHtml(r) {
  const fired = r.signals.filter((s) => s.fired);
  const body = fired.length
    ? fired.map((s) => `<div class="signal">
        <span class="mark" style="color:${s.direction === 'bullish' ? 'var(--good)' : 'var(--critical)'}">${s.direction === 'bullish' ? '▲' : '▼'}</span>
        <span class="body"><span class="name">${esc(s.name)}</span>
        <div class="detail">${esc(s.detail)}</div></span>
        <span class="when">${s.bars_since === 0 ? 'today' : `${s.bars_since}d ago`}</span>
      </div>`).join('')
    : '<p class="small muted">No classic setup fired in the last 10 sessions.</p>';

  return `<section class="panel">
    <h2>Signals fired (last 10 sessions)</h2>
    <p class="sub">Textbook rules, stated explicitly. A count of signals is not an edge — check the backtest below.</p>
    ${body}
  </section>`;
}

function backtestHtml(r) {
  const m = r.backtest.metrics, b = r.backtest.benchmark_metrics;
  const row = (label, key, pct = true) => `<tr><td>${label}</td>
    <td>${pct ? fmtPct(m[key]) : fmtNum(m[key])}</td>
    <td>${pct ? fmtPct(b[key]) : fmtNum(b[key])}</td></tr>`;
  return `<section class="panel">
    <h2>Trend-following backtest</h2>
    <p class="sub">Long when price &gt; 200-day average and 50-day &gt; 200-day, flat otherwise.
    Entered on the next bar, after commission and slippage. A yardstick, not a recommendation.</p>
    <div class="legend" id="lg-equity"></div>
    <div class="chart" id="ch-equity"></div>
    <table style="margin-top:14px">
      <thead><tr><th>Metric</th><th>Strategy</th><th>Buy &amp; hold</th></tr></thead>
      <tbody>
        ${row('Total return', 'total_return')}
        ${row('CAGR', 'cagr')}
        ${row('Volatility (ann.)', 'volatility')}
        ${row('Sharpe', 'sharpe', false)}
        ${row('Sortino', 'sortino', false)}
        ${row('Max drawdown', 'max_drawdown')}
        ${row('Win rate (active days)', 'win_rate')}
        <tr><td>Trades</td><td>${r.backtest.trades}</td><td>1</td></tr>
      </tbody>
    </table>
  </section>`;
}

function forecastMissingHtml(r) {
  return `<section class="panel"><h2>Forecast</h2>
    <p class="sub">Unavailable: ${esc(r.forecast_error || 'not requested')}</p>
    <p class="small muted">Walk-forward validation needs a long history. Request more data and try again —
    a model trained on too little history tells you about noise, not about the symbol.</p>
  </section>`;
}

function forecastHtml(fc) {
  const ev = fc.evaluation;
  const cal = ev.calibration.length
    ? `<table><thead><tr><th>Stated probability</th><th>Predicted</th><th>Actually happened</th><th>N</th></tr></thead>
       <tbody>${ev.calibration.map((c) => `<tr><td>${c.bucket}</td><td>${fmtPct(c.predicted, 1)}</td>
       <td>${fmtPct(c.actual, 1)}</td><td>${c.count}</td></tr>`).join('')}</tbody></table>`
    : '<p class="small muted">Too few predictions per bucket to assess calibration.</p>';

  const feats = fc.feature_importance.length
    ? `<table><thead><tr><th>Feature</th><th>Importance</th></tr></thead><tbody>
       ${fc.feature_importance.map((f) => `<tr><td>${esc(f.feature)}</td><td>${fmtNum(f.importance, 4)}</td></tr>`).join('')}
       </tbody></table>`
    : '<p class="small muted">No feature contributed measurably above noise.</p>';

  return `<section class="panel">
    <h2>Forecast — ${fc.horizon_days}-day horizon</h2>
    <p class="sub">Probability that the close is higher in ${fc.horizon_days} sessions, from a model
    validated walk-forward (trained only on the past at every step).</p>

    <div class="prob-hero">
      <span class="figure">${fmtPct(fc.probability_up, 1)}</span>
      <span class="caption">chance of finishing higher.
      Confidence: <strong>${esc(fc.confidence)}</strong>.</span>
    </div>

    <div class="verdict ${ev.has_edge ? 'has-edge' : 'no-edge'}">
      <strong>${ev.has_edge ? '✓ Measurable edge' : '⚠ No demonstrated edge'}</strong> — ${esc(ev.verdict)}
    </div>

    <h2 style="margin-top:20px">Where the price plausibly lands</h2>
    <p class="sub">Dark band = the middle 50% of comparable historical moves; light band = the middle 90%.
    The dashed line is today's close.</p>
    <div class="chart" id="ch-range"></div>

    <div class="grid-2" style="margin-top:18px">
      <div>
        <h2>Out-of-sample scorecard</h2>
        <table>
          <tbody>
            <tr><td>Predictions evaluated</td><td>${ev.n_predictions}</td></tr>
            <tr><td>Accuracy</td><td>${fmtPct(ev.accuracy, 1)}</td></tr>
            <tr><td>Base rate to beat</td><td>${fmtPct(ev.baseline_accuracy, 1)}</td></tr>
            <tr><td><strong>Edge over base rate</strong></td><td><strong class="${deltaClass(ev.edge)}">${fmtSignedPct(ev.edge, 1)}</strong></td></tr>
            <tr><td>ROC AUC</td><td>${fmtNum(ev.auc, 3)}</td></tr>
            <tr><td>Brier score</td><td>${fmtNum(ev.brier, 4)}</td></tr>
            <tr><td>Brier, base rate only</td><td>${fmtNum(ev.baseline_brier, 4)}</td></tr>
          </tbody>
        </table>
        <p class="small muted" style="margin-top:8px">A Brier score above the base-rate row means the
        model's probabilities are worse than simply quoting the historical average.</p>
      </div>
      <div>
        <h2>Calibration</h2>
        <p class="sub">When it says 70%, does 70% happen?</p>
        ${cal}
        <h2 style="margin-top:16px">What the model leans on</h2>
        ${feats}
      </div>
    </div>

    <p class="small muted" style="margin-top:16px">${esc(fc.caveat)}</p>
  </section>`;
}

function tableHtml(r) {
  const s = r.series;
  const n = s.dates.length;
  const rows = [];
  for (let i = Math.max(0, n - 60); i < n; i++) {
    rows.push(`<tr><td>${s.dates[i]}</td><td>${fmtNum(s.close[i])}</td>
      <td>${fmtNum(s.sma_20?.[i])}</td><td>${fmtNum(s.sma_50?.[i])}</td>
      <td>${fmtNum(s.sma_200?.[i])}</td><td>${fmtNum(s.rsi_14?.[i], 1)}</td>
      ${r.has_volume ? `<td>${fmtNum(s.volume[i], 0)}</td>` : ''}</tr>`);
  }
  return `<section class="panel">
    <h2>Data table — last 60 sessions</h2>
    <p class="sub">The same numbers behind the charts, for readers who need values rather than shapes.</p>
    <div class="table-scroll"><table>
      <thead><tr><th>Date</th><th>Close</th><th>SMA 20</th><th>SMA 50</th><th>SMA 200</th><th>RSI</th>${r.has_volume ? '<th>Volume</th>' : ''}</tr></thead>
      <tbody>${rows.reverse().join('')}</tbody>
    </table></div>
  </section>`;
}

/* ---------------- charts ---------------- */
function drawPrice(s) {
  const series = [
    { label: 'Close', shortLabel: 'Close', values: s.close, color: S[1] },
    { label: 'SMA 20', shortLabel: 'SMA20', values: s.sma_20, color: S[2] },
    { label: 'SMA 50', shortLabel: 'SMA50', values: s.sma_50, color: S[3] },
    { label: 'SMA 200', shortLabel: 'SMA200', values: s.sma_200, color: S[4] },
  ];
  legend($('lg-price'), series);
  lineChart($('ch-price'), {
    dates: s.dates, series, height: 300,
    bands: [{ upper: s.bb_upper, lower: s.bb_lower, color: S[1], opacity: 0.08 }],
  });
}

function drawVolume(s) {
  barChart($('ch-volume'), {
    dates: s.dates, values: s.volume, height: 110, color: S[1],
    label: 'Volume', yFormat: (v) => fmtNum(v, 0),
  });
}

function drawRsi(s) {
  lineChart($('ch-rsi'), {
    dates: s.dates, series: [{ label: 'RSI 14', shortLabel: 'RSI', values: s.rsi_14, color: S[1] }],
    height: 190, width: 460, yMin: 0, yMax: 100, directLabels: false,
    refLines: [
      { value: 70, label: '70', color: 'var(--critical)' },
      { value: 50, label: '50' },
      { value: 30, label: '30', color: 'var(--good)' },
    ],
    yFormat: (v) => fmtNum(v, 0),
  });
}

function drawMacd(s) {
  const series = [
    { label: 'MACD', shortLabel: 'MACD', values: s.macd, color: S[1] },
    { label: 'Signal', shortLabel: 'Signal', values: s.macd_signal, color: S[2] },
  ];
  legend($('lg-macd'), series);
  lineChart($('ch-macd'), { dates: s.dates, series, height: 150, width: 460, yFormat: (v) => fmtNum(v, 2) });
  barChart($('ch-macdh'), {
    dates: s.dates, values: s.macd_hist, height: 90, width: 460,
    color: S[1], negativeColor: S[2], label: 'Histogram',
  });
}

function drawEquity(eq) {
  const series = [
    { label: 'Trend strategy', shortLabel: 'Strategy', values: eq.strategy, color: S[1] },
    { label: 'Buy & hold', shortLabel: 'B&H', values: eq.benchmark, color: S[2] },
  ];
  legend($('lg-equity'), series);
  lineChart($('ch-equity'), {
    dates: eq.dates, series, height: 240, yFormat: (v) => fmtNum(v, 0),
  });
}

run();
