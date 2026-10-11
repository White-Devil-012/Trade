import { priceChart, sparkline, fmtINR, fmtIN, fmtDate } from './charts.js';
import { renderAdvanced } from './advanced.js';

const $ = (id) => document.getElementById(id);
const view = $('view');

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const enc = encodeURIComponent;

/* Storage can be missing or throw (private windows, blocked site data). */
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};

/* ---------------- theme ---------------- */
const savedTheme = store.get('trendlab-theme');
if (savedTheme) document.documentElement.setAttribute('data-theme', savedTheme);
$('theme').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme');
  const isDark = current ? current === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  const next = isDark ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  store.set('trendlab-theme', next);
});

/* ---------------- demo mode ---------------- */
let demo = store.get('trendlab-demo') === '1';
const provider = () => (demo ? 'synthetic' : 'auto');

function syncDemoUI() {
  $('demo-banner').hidden = !demo;
  $('demo-toggle').checked = demo;
}
function setDemo(on) {
  demo = on;
  store.set('trendlab-demo', on ? '1' : '0');
  names.clear();
  syncDemoUI();
  route();
}
$('demo-toggle').addEventListener('change', (e) => setDemo(e.target.checked));
$('demo-off').addEventListener('click', () => setDemo(false));

/* ---------------- api ---------------- */
async function api(path, params = {}) {
  const res = await fetch(`${path}?${new URLSearchParams({ ...params, provider: provider() })}`);
  let body = {};
  try { body = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) {
    const err = new Error(body.detail || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return body;
}

/* Names seen on the home screen or in search, so a stock page can show the
   company name immediately instead of a ticker while it loads. */
const names = new Map();

/* ---------------- formatting ---------------- */
const money = (v, kind) => (kind === 'index' ? fmtIN(v) : fmtINR(v));

function direction(v) { return v > 0 ? 'up' : v < 0 ? 'down' : 'flat'; }

/** ▲ +12.30 (0.89%) — arrow and sign carry direction, colour only repeats it. */
function changeHtml(change, pct, suffix = '') {
  const dir = direction(change);
  const arrow = { up: '▲', down: '▼', flat: '●' }[dir];
  const sign = { up: '+', down: '−', flat: '' }[dir];
  const spoken = { up: 'up', down: 'down', flat: 'unchanged' }[dir];
  return `<span class="chg ${dir}"><span aria-hidden="true">${arrow}</span>`
    + `<span class="sr-only">${spoken}</span> ${sign}${fmtIN(Math.abs(change ?? 0))} `
    + `(${(Math.abs(pct ?? 0) * 100).toFixed(2)}%)${suffix}</span>`;
}

function pctHtml(v) {
  if (v === null || v === undefined) return '<span class="muted">—</span>';
  const dir = direction(v);
  const sign = { up: '+', down: '−', flat: '' }[dir];
  return `<span class="chg ${dir}">${sign}${(Math.abs(v) * 100).toFixed(2)}%</span>`;
}

const STOP = new Set(['limited', 'ltd', 'the', 'of', 'and', '&']);
function initials(name) {
  const words = String(name).replace(/[^A-Za-z0-9& ]/g, ' ').split(/\s+/).filter(Boolean);
  const pick = words.filter((w) => !STOP.has(w.toLowerCase()));
  const use = pick.length ? pick : words;
  if (use.length >= 2) return (use[0][0] + use[1][0]).toUpperCase();
  return (use[0] || '?').slice(0, 2).toUpperCase();
}

/* Colour follows the company, never its position on screen. */
function avatarHtml(symbol, name, size = '') {
  let h = 0;
  for (const c of String(symbol)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return `<span class="avatar ${size}" style="--av: var(--series-${(h % 8) + 1})" aria-hidden="true">${esc(initials(name))}</span>`;
}

const skeletons = (n, cls = 'skeleton') => Array.from({ length: n }, () => `<div class="${cls}"></div>`).join('');

/* Translate server errors into something a non-developer can act on. */
function friendly(err) {
  if (err.status === 502) {
    return "We couldn't reach the price feed. Your internet connection, or a firewall, may be blocking it.";
  }
  if (err.status === 404) return "We couldn't find prices for this. Try searching for it by name.";
  return 'Something went wrong while loading this.';
}

function errorHtml(title, err, back = '') {
  return `<div class="error-card">
    <h2>${esc(title)}</h2>
    <p>${esc(friendly(err))}</p>
    <div>
      <button class="btn" type="button" data-action="retry">Try again</button>
      ${demo ? '' : '<button class="btn secondary" type="button" data-action="demo">Use demo data instead</button>'}
      ${back}
    </div>
    <details class="small muted" style="margin-top:12px"><summary>Technical details</summary>${esc(err.message)}</details>
  </div>`;
}

view.addEventListener('click', (e) => {
  const action = e.target.closest('[data-action]')?.dataset.action;
  if (action === 'retry') route();
  if (action === 'demo') setDemo(true);
});

/* ---------------- router ---------------- */
let navToken = 0;

function route() {
  closeSuggest();
  const nav = ++navToken;
  const hash = location.hash || '#/';
  window.scrollTo(0, 0);
  if (hash.startsWith('#/s/')) return renderStock(decodeURIComponent(hash.slice(4)), nav);
  if (hash === '#/funds') return renderFunds(nav);
  return renderHome(nav);
}
window.addEventListener('hashchange', route);

const tabsHtml = (active) => `<nav class="tabs" aria-label="Browse">
  <a class="tab" href="#/" ${active === 'stocks' ? 'aria-current="page"' : ''}>Stocks</a>
  <a class="tab" href="#/funds" ${active === 'funds' ? 'aria-current="page"' : ''}>Mutual funds</a>
</nav>`;

/* ---------------- home ---------------- */
function indexCard(t) {
  return `<a class="index-card" href="#/s/${enc(t.symbol)}">
    <div class="name">${esc(t.name)}</div>
    <div class="value">${fmtIN(t.price)}</div>
    ${changeHtml(t.change, t.change_pct)}
  </a>`;
}

function stockCard(t) {
  return `<a class="stock-card" href="#/s/${enc(t.symbol)}">
    ${avatarHtml(t.symbol, t.name)}
    <div class="name">${esc(t.name)}</div>
    <div class="quote"><span class="price">${fmtINR(t.price)}</span>${changeHtml(t.change, t.change_pct)}</div>
    <div class="spark" data-spark="${t.spark.join(',')}"
         data-up="${t.spark[t.spark.length - 1] >= t.spark[0]}"></div>
  </a>`;
}

function fillSparks(root) {
  root.querySelectorAll('[data-spark]').forEach((node) => {
    sparkline(node, node.dataset.spark.split(',').map(Number), node.dataset.up === 'true');
  });
}

async function renderHome(nav) {
  document.title = 'TrendLab';
  view.innerHTML = `${tabsHtml('stocks')}
    <section class="section"><h2 class="section-title">Indices</h2><div class="index-row">${skeletons(3)}</div></section>
    <section class="section"><h2 class="section-title">Top movers today</h2><div class="stock-grid">${skeletons(4)}</div></section>
    <section class="section"><h2 class="section-title">Popular stocks</h2><div class="stock-grid">${skeletons(8)}</div></section>`;

  let data;
  try {
    data = await api('/api/market');
  } catch (err) {
    if (nav !== navToken) return;
    view.innerHTML = tabsHtml('stocks') + errorHtml("Couldn't load market prices", err);
    return;
  }
  if (nav !== navToken) return;

  for (const t of [...data.indices, ...data.stocks]) names.set(t.symbol, t.name);
  const gainers = data.stocks.filter((s) => s.change_pct > 0).sort((a, b) => b.change_pct - a.change_pct).slice(0, 4);
  const losers = data.stocks.filter((s) => s.change_pct < 0).sort((a, b) => a.change_pct - b.change_pct).slice(0, 4);
  const asOf = data.stocks[0]?.as_of || data.indices[0]?.as_of;

  view.innerHTML = `${tabsHtml('stocks')}
    <section class="section">
      <h2 class="section-title">Indices</h2>
      <div class="index-row">${data.indices.map(indexCard).join('')}</div>
    </section>
    <section class="section">
      <div class="section-head">
        <h2>Top movers today</h2>
        <div class="seg" role="group" aria-label="Show gainers or losers">
          <button type="button" data-mov="gainers" aria-pressed="true">Gainers</button>
          <button type="button" data-mov="losers" aria-pressed="false">Losers</button>
        </div>
      </div>
      <div class="stock-grid" id="movers"></div>
    </section>
    <section class="section">
      <h2 class="section-title">Popular stocks</h2>
      <div class="stock-grid">${data.stocks.map(stockCard).join('')}</div>
    </section>
    <p class="small muted">${asOf ? `Prices as of ${fmtDate(asOf)}. ` : ''}The line on each card shows the past month.
    Top movers are picked from the popular stocks above.
    ${data.missing.length ? `Couldn't load: ${data.missing.map(esc).join(', ')}.` : ''}</p>`;

  const showMovers = (which) => {
    const list = which === 'gainers' ? gainers : losers;
    const box = $('movers');
    box.innerHTML = list.length ? list.map(stockCard).join('')
      : `<div class="empty">None of these stocks ${which === 'gainers' ? 'rose' : 'fell'} on the last trading day.</div>`;
    fillSparks(box);
    view.querySelectorAll('[data-mov]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mov === which)));
  };
  view.querySelectorAll('[data-mov]').forEach((b) => b.addEventListener('click', () => showMovers(b.dataset.mov)));
  showMovers('gainers');
  fillSparks(view);
}

/* ---------------- mutual funds ---------------- */
const FUND_CHIPS = [
  ['Index funds', 'index'], ['Flexi cap', 'flexi cap'], ['Large cap', 'large cap'],
  ['Mid cap', 'mid cap'], ['Small cap', 'small cap'], ['ELSS · tax saving', 'elss'], ['Liquid', 'liquid'],
];

function renderFunds(nav) {
  document.title = 'Mutual funds · TrendLab';
  view.innerHTML = `${tabsHtml('funds')}
    <section class="section">
      <h2 class="section-title">Find a mutual fund</h2>
      <div class="fund-search">
        <input id="fund-q" type="search" autocomplete="off"
               placeholder="Search by fund name, e.g. “flexi cap” or a fund house"
               aria-label="Search mutual funds" />
      </div>
      <div class="chips" role="group" aria-label="Fund categories">
        ${FUND_CHIPS.map(([label, q]) => `<button class="chip" type="button" data-q="${esc(q)}" aria-pressed="false">${esc(label)}</button>`).join('')}
      </div>
      <ul class="fund-results" id="fund-results"></ul>
      <p class="small muted" id="fund-note">${demo
        ? 'Demo mode lists a few made-up funds so you can try the screens offline.'
        : 'Covers every scheme listed with AMFI. A fund’s price is its NAV, published once a day after markets close.'}</p>
    </section>`;

  const input = $('fund-q');
  const results = $('fund-results');
  let timer = null;

  const run = async (q) => {
    view.querySelectorAll('.chip').forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.q === q)));
    if (q.trim().length < 2) { results.innerHTML = ''; return; }
    results.innerHTML = '<li class="loading-line"><span class="spinner"></span>Searching…</li>';
    try {
      const body = await api('/api/search', { q, kind: 'fund', limit: 25 });
      if (nav !== navToken) return;
      for (const r of body.results) names.set(r.symbol, r.name);
      results.innerHTML = body.results.length
        ? body.results.map((r) => `<li><a href="#/s/${enc(r.symbol)}">
            ${avatarHtml(r.symbol, r.name)}
            <span><span class="f-name">${esc(r.name)}</span><br>
            <span class="f-meta">Mutual fund · scheme code ${esc(r.symbol.replace('MF:', ''))}</span></span>
          </a></li>`).join('')
        : `<li class="empty">No funds matched “${esc(q)}”. Try fewer words.</li>`;
    } catch (err) {
      if (nav !== navToken) return;
      results.innerHTML = `<li>${errorHtml("Couldn't search funds", err)}</li>`;
    }
  };

  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => run(input.value), 250);
  });
  view.querySelectorAll('.chip').forEach((chip) => chip.addEventListener('click', () => {
    input.value = chip.dataset.q;
    run(chip.dataset.q);
  }));
}

/* ---------------- stock / fund page ---------------- */
const RANGES = [
  ['1W', { days: 7 }], ['1M', { months: 1 }], ['3M', { months: 3 }], ['6M', { months: 6 }],
  ['1Y', { years: 1 }], ['3Y', { years: 3 }], ['5Y', { years: 5 }],
];
const SLACK_DAYS = 7; // "5 years ago" is often a weekend or holiday

function rangeStart(dates, spec) {
  const end = new Date(`${dates[dates.length - 1]}T00:00:00`);
  const target = new Date(end);
  if (spec.days) target.setDate(target.getDate() - spec.days);
  if (spec.months) target.setMonth(target.getMonth() - spec.months);
  if (spec.years) target.setFullYear(target.getFullYear() - spec.years);
  const first = new Date(`${dates[0]}T00:00:00`);
  if (first - target > SLACK_DAYS * 864e5) return -1; // not enough history
  const iso = target.toISOString().slice(0, 10);
  let idx = 0;
  for (let i = 0; i < dates.length && dates[i] <= iso; i++) idx = i;
  return idx;
}

function rangeBarHtml(label, low, high, value, kind) {
  if (low == null || high == null || value == null) return '';
  const pos = high > low ? Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100)) : 50;
  return `<div class="range-bar">
    <div class="ends"><span>Low<b>${money(low, kind)}</b></span><span style="text-align:right">High<b>${money(high, kind)}</b></span></div>
    <div class="track"><span class="marker" style="left:${pos.toFixed(1)}%" title="Current ${money(value, kind)}"></span></div>
    <div class="cap">${esc(label)}</div>
  </div>`;
}

function statHtml(label, value) {
  return `<div class="stat"><div class="lbl">${esc(label)}</div><div class="val">${value}</div></div>`;
}

function keyNumbersHtml(s) {
  const q = s.quote;
  if (s.kind === 'fund') {
    return rangeBarHtml('52-week range', q.low_52w, q.high_52w, q.price, s.kind)
      + `<div class="stats-grid">
        ${statHtml('NAV', fmtINR(q.price))}
        ${statHtml('NAV date', esc(fmtDate(q.as_of)))}
        ${statHtml('Previous NAV', fmtINR(q.prev_close))}
      </div>`;
  }
  return rangeBarHtml("Today's range", q.day_low, q.day_high, q.price, s.kind)
    + rangeBarHtml('52-week range', q.low_52w, q.high_52w, q.price, s.kind)
    + `<div class="stats-grid">
      ${statHtml('Open', money(q.open, s.kind))}
      ${statHtml('Previous close', money(q.prev_close, s.kind))}
      ${q.volume != null ? statHtml('Volume', fmtIN(q.volume, 0)) : ''}
    </div>`;
}

function returnsHtml(returns) {
  return `<div class="returns-grid">${RANGES.map(([label]) => {
    const r = returns[label];
    return `<div class="ret"><div class="lbl">${label}${r?.annualised ? ' <span class="pa">p.a.</span>' : ''}</div>
      <div class="val">${pctHtml(r ? r.value : null)}</div></div>`;
  }).join('')}</div>
  <p class="small muted" style="margin:10px 0 0">3Y and 5Y are yearly averages (p.a.), the way funds quote returns.</p>`;
}

function moodHtml(m) {
  const arrow = { up: '▲', down: '▼', flat: '●' }[m.tone] || '●';
  return `<span class="pill ${m.tone === 'up' ? 'up' : m.tone === 'down' ? 'down' : 'flat'}">
      <span aria-hidden="true">${arrow}</span> ${esc(m.headline)}</span>
    <p>${m.sentences.map(esc).join(' ')}</p>`;
}

function predictionHtml(p) {
  const tone = p.lean === 'up' ? 'up' : p.lean === 'down' ? 'down' : 'flat';
  const arrow = { up: '▲', down: '▼', flat: '●' }[tone];
  const prob = p.probability_up != null
    ? `<div class="take-prob"><span class="big ${p.reliable ? '' : 'muted'}">${Math.round(p.probability_up * 100)}%</span>
       <span class="lbl">chance it’s higher in ${p.horizon_days} trading days</span></div>` : '';
  return `<span class="pill ${tone}"><span aria-hidden="true">${arrow}</span> ${esc(p.headline)}</span>
    ${prob}
    <p>${p.sentences.map(esc).join(' ')}</p>`;
}

async function renderStock(symbol, nav) {
  const backHref = symbol.toUpperCase().startsWith('MF:') ? '#/funds' : '#/';
  const back = `<a class="back" href="${backHref}">← Back</a>`;
  const knownName = names.get(symbol);
  document.title = `${knownName || symbol} · TrendLab`;

  view.innerHTML = `${back}
    <div class="stock-layout"><div class="stock-main">
      <div class="stock-head">${avatarHtml(symbol, knownName || symbol, 'lg')}
        <div><h1>${esc(knownName || symbol)}</h1><p class="meta">Loading…</p></div></div>
      <div class="skeleton" style="min-height:340px"></div>
    </div><aside class="stock-side"><div class="skeleton" style="min-height:260px"></div></aside></div>`;

  let s;
  try {
    s = await api('/api/stock', { symbol });
  } catch (err) {
    if (nav !== navToken) return;
    view.innerHTML = back + errorHtml(`Couldn't load ${knownName || symbol}`, err);
    return;
  }
  if (nav !== navToken) return;

  names.set(symbol, s.name);
  document.title = `${s.name} · TrendLab`;
  const ticker = s.kind === 'equity' ? s.symbol.split('.')[0] : s.kind === 'fund' ? `Scheme ${s.symbol.replace('MF:', '')}` : '';
  const q = s.quote;
  const priceLabel = s.kind === 'fund' ? 'NAV as of ' : 'As of ';

  view.innerHTML = `${back}
    <div class="stock-layout">
      <div class="stock-main">
        <div class="o1">
          <div class="stock-head">${avatarHtml(s.symbol, s.name, 'lg')}
            <div><h1>${esc(s.name)}</h1><p class="meta">${esc([s.exchange, ticker].filter(Boolean).join(' · '))}</p></div>
          </div>
          <div class="price-row">
            <span class="price-big">${money(q.price, s.kind)}</span>
            ${changeHtml(q.change, q.change_pct, ' <span class="muted">1D</span>')}
            <span class="asof">${priceLabel}${esc(fmtDate(q.as_of))}</span>
          </div>
        </div>

        <div class="card chart-card o2">
          <div class="chart" id="price-chart"></div>
          <div class="range-row">
            <div class="ranges" role="group" aria-label="Chart range">
              ${RANGES.map(([label]) => `<button type="button" data-range="${label}" aria-pressed="false">${label}</button>`).join('')}
            </div>
            <span class="range-change" id="range-change" aria-live="polite"></span>
          </div>
        </div>

        <section class="card o4"><h2>Returns</h2>${returnsHtml(s.returns)}</section>
        <section class="card o5"><h2>${s.kind === 'fund' ? 'Fund details' : 'Key numbers'}</h2>${keyNumbersHtml(s)}</section>

        <details class="advanced o6" id="advanced">
          <summary><span><b>Advanced analysis</b><span>Indicators, signals, a strategy backtest and the model’s track record</span></span></summary>
          <div class="advanced-body" id="advanced-body"><div class="loading-line"><span class="spinner"></span>Running the analysis…</div></div>
        </details>
      </div>

      <aside class="stock-side">
        <section class="card take" id="take">
          <h2>Our take</h2>
          <div class="take-block"><h3>Right now</h3>${moodHtml(s.mood)}</div>
          <div class="take-block" id="take-forecast"><h3>Next 5 trading days</h3>
            <div class="loading-line"><span class="spinner"></span>Testing the forecast against past prices…</div></div>
          <p class="fine">This describes what the numbers show. It is not advice to buy or sell.</p>
        </section>
      </aside>
    </div>`;

  // Chart with range buttons.
  const dates = s.history.dates;
  const close = s.history.close;
  const drawRange = (label) => {
    const spec = RANGES.find(([l]) => l === label)[1];
    const start = rangeStart(dates, spec);
    if (start < 0) return;
    const d = dates.slice(start), v = close.slice(start);
    const change = v[v.length - 1] / v[0] - 1;
    priceChart($('price-chart'), { dates: d, values: v, up: change >= 0, format: (x) => money(x, s.kind) });
    $('range-change').innerHTML = `${pctHtml(change)} <span class="muted">in ${label}</span>`;
    view.querySelectorAll('[data-range]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.range === label)));
  };
  view.querySelectorAll('[data-range]').forEach((b) => {
    const spec = RANGES.find(([l]) => l === b.dataset.range)[1];
    if (rangeStart(dates, spec) < 0) { b.disabled = true; b.title = 'Not enough history'; }
    b.addEventListener('click', () => drawRange(b.dataset.range));
  });
  const defaultRange = ['1Y', '6M', '3M', '1M', '1W'].find((l) => !view.querySelector(`[data-range="${l}"]`).disabled);
  if (defaultRange) drawRange(defaultRange);

  const chartBox = $('price-chart');
  let lastWidth = chartBox.clientWidth;
  const resize = new ResizeObserver(() => {
    if (nav !== navToken) { resize.disconnect(); return; }
    if (Math.abs(chartBox.clientWidth - lastWidth) < 24) return;
    lastWidth = chartBox.clientWidth;
    const current = view.querySelector('[data-range][aria-pressed="true"]')?.dataset.range;
    if (current) drawRange(current);
  });
  resize.observe(chartBox);

  // The slow part: forecast and full analysis.
  let r;
  try {
    r = await api('/api/analyze', { symbol, period: '5y' });
  } catch (err) {
    if (nav !== navToken) return;
    $('take-forecast').innerHTML = `<h3>Next 5 trading days</h3><p>The forecast couldn’t be calculated: ${esc(friendly(err))}</p>`;
    $('advanced-body').innerHTML = errorHtml("Couldn't run the analysis", err);
    return;
  }
  if (nav !== navToken) return;

  const prediction = r.explain?.prediction;
  if (prediction) {
    $('take-forecast').innerHTML = `<h3>Next ${prediction.horizon_days || 5} trading days</h3>${predictionHtml(prediction)}`;
  }
  renderAdvanced($('advanced-body'), r);
}

/* ---------------- header search ---------------- */
const searchInput = $('search');
const suggestBox = $('suggest');
let suggestions = [];
let activeIndex = -1;
let searchTimer = null;
let searchSeq = 0;

function closeSuggest() {
  suggestBox.hidden = true;
  suggestBox.innerHTML = '';
  suggestions = [];
  activeIndex = -1;
  searchInput.setAttribute('aria-expanded', 'false');
}

function metaFor(r) {
  if (r.kind === 'fund') return 'Mutual fund';
  if (r.kind === 'index') return 'Index';
  return [r.exchange, r.symbol.split('.')[0]].filter(Boolean).join(' · ');
}

function renderSuggest() {
  suggestBox.innerHTML = suggestions.length
    ? suggestions.map((r, i) => `<li role="option" id="opt-${i}" data-i="${i}" aria-selected="${i === activeIndex}">
        ${avatarHtml(r.symbol, r.name)}
        <span class="s-text"><span class="s-name">${esc(r.name)}</span><span class="s-meta">${esc(metaFor(r))}</span></span>
      </li>`).join('')
    : '<li class="s-empty">Nothing matched. Try a company name like “Reliance” or “HDFC”.</li>';
  suggestBox.hidden = false;
  searchInput.setAttribute('aria-expanded', 'true');
  if (activeIndex >= 0) searchInput.setAttribute('aria-activedescendant', `opt-${activeIndex}`);
  else searchInput.removeAttribute('aria-activedescendant');
}

function choose(i) {
  const hit = suggestions[i];
  if (!hit) return;
  names.set(hit.symbol, hit.name);
  searchInput.value = '';
  closeSuggest();
  searchInput.blur();
  location.hash = `#/s/${enc(hit.symbol)}`;
}

suggestBox.addEventListener('mousedown', (e) => {
  // mousedown, not click: the input's blur would close the list first.
  const li = e.target.closest('li[data-i]');
  if (li) { e.preventDefault(); choose(Number(li.dataset.i)); }
});

searchInput.addEventListener('input', () => {
  const q = searchInput.value.trim();
  clearTimeout(searchTimer);
  if (q.length < 2) { closeSuggest(); return; }
  searchTimer = setTimeout(async () => {
    const seq = ++searchSeq;
    try {
      const body = await api('/api/search', { q, limit: 10 });
      if (seq !== searchSeq) return; // a newer keystroke already answered
      suggestions = body.results || [];
      activeIndex = -1;
      renderSuggest();
    } catch {
      closeSuggest();
    }
  }, 180);
});

searchInput.addEventListener('keydown', (e) => {
  if (suggestBox.hidden) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!suggestions.length) return;
    const step = e.key === 'ArrowDown' ? 1 : -1;
    activeIndex = (activeIndex + step + suggestions.length) % suggestions.length;
    renderSuggest();
    suggestBox.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter') {
    e.preventDefault();
    choose(activeIndex >= 0 ? activeIndex : 0);
  } else if (e.key === 'Escape') {
    closeSuggest();
  }
});
searchInput.addEventListener('blur', () => setTimeout(closeSuggest, 150));

/* ---------------- start ---------------- */
syncDemoUI();
route();
