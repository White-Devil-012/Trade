/* Minimal SVG chart toolkit.
 *
 * Hand-rolled rather than pulled from a CDN so the dashboard works fully
 * offline. Every chart here follows the same rules: one y-axis (never two
 * scales in one frame), 2px lines, a recessive grid, and a crosshair tooltip
 * on anything time-series shaped.
 */

const SVG_NS = 'http://www.w3.org/2000/svg';

function el(name, attrs = {}) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== null && v !== undefined) node.setAttribute(k, String(v));
  }
  return node;
}

export function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPct(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtSignedPct(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`;
}

/* Axis ticks at 1/2/5 x 10^n, so labels land on numbers people read easily. */
function niceTicks(min, max, count = 5) {
  if (!isFinite(min) || !isFinite(max) || min === max) return [min];
  const rawStep = (max - min) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
  const ticks = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step * 1e-9; t += step) ticks.push(t);
  return ticks;
}

function extent(seriesList) {
  let lo = Infinity, hi = -Infinity;
  for (const s of seriesList) {
    for (const v of s.values) {
      if (v === null || v === undefined || Number.isNaN(v)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  return [lo, hi];
}

/* Nudge overlapping direct labels apart, keeping them inside the plot band and
 * as close to their true series value as the spacing allows. */
function declutter(slots, top, bottom, gap = 13) {
  if (slots.length < 2) return slots;
  const sorted = [...slots].sort((a, b) => a.y - b.y);
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i].y - sorted[i - 1].y < gap) sorted[i].y = sorted[i - 1].y + gap;
  }
  // If pushing down ran past the bottom, walk back up from the last label.
  const overflow = sorted[sorted.length - 1].y - bottom;
  if (overflow > 0) {
    sorted[sorted.length - 1].y = bottom;
    for (let i = sorted.length - 2; i >= 0; i--) {
      sorted[i].y = Math.min(sorted[i].y, sorted[i + 1].y - gap);
    }
    if (sorted[0].y < top) sorted.forEach((s, i) => { s.y = top + i * gap; });
  }
  return sorted;
}

/**
 * Time-series line chart with a crosshair tooltip.
 * One shared y-scale for every series — callers that need a second scale get a
 * second chart, because dual axes invent correlations that are not in the data.
 */
export function lineChart(container, { dates, series, height = 260, bands = [],
                                        refLines = [], yFormat = fmtNum, width = 900,
                                        yMin = null, yMax = null, directLabels = true }) {
  container.innerHTML = '';
  const tooltip = document.createElement('div');
  tooltip.className = 'tooltip';
  container.appendChild(tooltip);

  const W = width, H = height;
  const M = { top: 12, right: directLabels ? 62 : 22, bottom: 26, left: 56 };
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;

  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  container.appendChild(svg);

  const scaleSources = [...series, ...bands.flatMap((b) => [
    { values: b.upper }, { values: b.lower },
  ])];
  let [lo, hi] = extent(scaleSources);
  for (const r of refLines) { lo = Math.min(lo, r.value); hi = Math.max(hi, r.value); }
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  if (yMin !== null) lo = yMin;
  if (yMax !== null) hi = yMax;
  const pad = (hi - lo) * 0.06 || 1;
  const y0 = yMin !== null ? yMin : lo - pad;
  const y1 = yMax !== null ? yMax : hi + pad;

  const n = dates.length;
  const X = (i) => M.left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const Y = (v) => M.top + plotH - ((v - y0) / (y1 - y0)) * plotH;

  // Grid and y labels first, so data marks always paint on top of the chrome.
  for (const t of niceTicks(y0, y1, 5)) {
    const yy = Y(t);
    if (yy < M.top - 1 || yy > M.top + plotH + 1) continue;
    svg.appendChild(el('line', { class: 'grid-line', x1: M.left, x2: M.left + plotW, y1: yy, y2: yy }));
    const label = el('text', { class: 'tick-label', x: M.left - 8, y: yy + 4, 'text-anchor': 'end' });
    label.textContent = yFormat(t);
    svg.appendChild(label);
  }

  for (const band of bands) {
    const top = band.upper.map((v, i) => (v === null ? null : [X(i), Y(v)]));
    const bottom = band.lower.map((v, i) => (v === null ? null : [X(i), Y(v)]));
    const pts = top.filter(Boolean).map(([x, y]) => `${x},${y}`)
      .concat(bottom.filter(Boolean).reverse().map(([x, y]) => `${x},${y}`));
    if (pts.length > 3) {
      svg.appendChild(el('polygon', {
        points: pts.join(' '), fill: band.color, 'fill-opacity': band.opacity ?? 0.09, stroke: 'none',
      }));
    }
  }

  for (const ref of refLines) {
    svg.appendChild(el('line', {
      class: 'grid-line', x1: M.left, x2: M.left + plotW, y1: Y(ref.value), y2: Y(ref.value),
      stroke: ref.color || 'var(--baseline)', 'stroke-dasharray': '4 4',
    }));
    if (ref.label) {
      const t = el('text', { class: 'tick-label', x: M.left + plotW + 4, y: Y(ref.value) + 4 });
      t.textContent = ref.label;
      svg.appendChild(t);
    }
  }

  // X labels: first, middle, last — enough to orient without crowding.
  for (const i of [0, Math.floor((n - 1) / 2), n - 1]) {
    if (i < 0 || i >= n) continue;
    const t = el('text', {
      class: 'tick-label', x: X(i), y: H - 8,
      'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle',
    });
    t.textContent = dates[i];
    svg.appendChild(t);
  }
  svg.appendChild(el('line', {
    class: 'axis-line', x1: M.left, x2: M.left + plotW, y1: M.top + plotH, y2: M.top + plotH,
  }));

  const labelSlots = [];
  for (const s of series) {
    // Break the path at gaps so a warm-up NaN never draws a false straight line.
    let d = '', pen = false;
    s.values.forEach((v, i) => {
      if (v === null || v === undefined || Number.isNaN(v)) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${X(i)},${Y(v)}`;
      pen = true;
    });
    if (!d) continue;
    svg.appendChild(el('path', {
      class: 'series-line', d, stroke: s.color,
      'stroke-width': s.width || 2, 'stroke-dasharray': s.dash || null,
    }));

    if (directLabels) {
      let last = -1;
      for (let i = s.values.length - 1; i >= 0; i--) {
        const v = s.values[i];
        if (v !== null && v !== undefined && !Number.isNaN(v)) { last = i; break; }
      }
      if (last >= 0) {
        labelSlots.push({ y: Y(s.values[last]), text: s.shortLabel || s.label, color: s.color });
      }
    }
  }

  for (const slot of declutter(labelSlots, M.top, M.top + plotH)) {
    const t = el('text', {
      class: 'direct-label', x: M.left + plotW + 6, y: slot.y + 4, fill: slot.color,
    });
    t.textContent = slot.text;
    svg.appendChild(t);
  }

  attachCrosshair({ svg, container, tooltip, dates, series, X, Y, M, plotW, plotH, n, yFormat, W });
  return svg;
}

/* Shared hover layer: a dashed crosshair, a dot per series, and a value tooltip. */
function attachCrosshair(ctx) {
  const { svg, container, tooltip, dates, series, X, Y, M, plotW, plotH, n, yFormat, W } = ctx;
  const vline = el('line', { class: 'crosshair', y1: M.top, y2: M.top + plotH, opacity: 0 });
  svg.appendChild(vline);
  const dots = series.map((s) => {
    const dot = el('circle', { class: 'hover-dot', r: 4.5, fill: s.color, opacity: 0 });
    svg.appendChild(dot);
    return dot;
  });

  const overlay = el('rect', {
    x: M.left, y: M.top, width: plotW, height: plotH, fill: 'transparent', style: 'cursor:crosshair',
  });
  svg.appendChild(overlay);

  const hide = () => {
    tooltip.style.opacity = '0';
    vline.setAttribute('opacity', '0');
    dots.forEach((d) => d.setAttribute('opacity', '0'));
  };

  const move = (evt) => {
    const box = svg.getBoundingClientRect();
    const point = (evt.touches ? evt.touches[0] : evt);
    // Map client px back into viewBox units before inverting the scale.
    const vx = ((point.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((vx - M.left) / plotW) * (n - 1))));

    vline.setAttribute('x1', X(i));
    vline.setAttribute('x2', X(i));
    vline.setAttribute('opacity', '1');

    let rows = '';
    series.forEach((s, k) => {
      const v = s.values[i];
      if (v === null || v === undefined || Number.isNaN(v)) {
        dots[k].setAttribute('opacity', '0');
        return;
      }
      dots[k].setAttribute('cx', X(i));
      dots[k].setAttribute('cy', Y(v));
      dots[k].setAttribute('opacity', '1');
      rows += `<div class="tt-row"><span class="tt-k"><span class="sw" style="background:${s.color}"></span>${s.label}</span><span class="tt-v">${yFormat(v)}</span></div>`;
    });

    tooltip.innerHTML = `<div class="tt-date">${dates[i]}</div>${rows}`;
    tooltip.style.opacity = '1';
    // Flip the tooltip to the left of the cursor near the right edge.
    const px = (X(i) / W) * box.width;
    const flip = px > box.width - tooltip.offsetWidth - 24;
    tooltip.style.left = `${Math.max(4, flip ? px - tooltip.offsetWidth - 14 : px + 14)}px`;
    tooltip.style.top = '8px';
  };

  overlay.addEventListener('mousemove', move);
  overlay.addEventListener('mouseleave', hide);
  overlay.addEventListener('touchmove', move, { passive: true });
  overlay.addEventListener('touchend', hide);
}

/** Bar chart for volume / histograms. Bars are anchored to the baseline. */
export function barChart(container, { dates, values, height = 110, color = 'var(--series-1)',
                                      negativeColor = null, yFormat = fmtNum, label = 'Value',
                                      width = 900 }) {
  container.innerHTML = '';
  const tooltip = document.createElement('div');
  tooltip.className = 'tooltip';
  container.appendChild(tooltip);

  const W = width, H = height;
  const M = { top: 8, right: 62, bottom: 20, left: 56 };
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;

  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  container.appendChild(svg);

  const clean = values.filter((v) => v !== null && !Number.isNaN(v));
  const hi = Math.max(0, ...clean);
  const lo = Math.min(0, ...clean);
  const span = hi - lo || 1;
  const Y = (v) => M.top + plotH - ((v - lo) / span) * plotH;
  const zeroY = Y(0);

  svg.appendChild(el('line', { class: 'axis-line', x1: M.left, x2: M.left + plotW, y1: zeroY, y2: zeroY }));
  for (const t of [hi, lo].filter((t, i, a) => a.indexOf(t) === i && t !== 0)) {
    const label_ = el('text', { class: 'tick-label', x: M.left - 8, y: Y(t) + 4, 'text-anchor': 'end' });
    label_.textContent = yFormat(t);
    svg.appendChild(label_);
  }

  const n = values.length;
  // A 2px surface gap between neighbouring bars, down to a 1px minimum width.
  const slot = plotW / n;
  const barW = Math.max(1, slot - (slot > 3 ? 2 : 0.5));

  values.forEach((v, i) => {
    if (v === null || Number.isNaN(v)) return;
    const x = M.left + i * slot + (slot - barW) / 2;
    const top = v >= 0 ? Y(v) : zeroY;
    const h = Math.max(1, Math.abs(Y(v) - zeroY));
    const rect = el('rect', {
      x, y: top, width: barW, height: h,
      fill: v < 0 && negativeColor ? negativeColor : color,
      rx: Math.min(2, barW / 2),
    });
    rect.addEventListener('mouseenter', () => {
      tooltip.innerHTML = `<div class="tt-date">${dates[i]}</div><div class="tt-row"><span class="tt-k"><span class="sw" style="background:${v < 0 && negativeColor ? negativeColor : color}"></span>${label}</span><span class="tt-v">${yFormat(v)}</span></div>`;
      tooltip.style.opacity = '1';
      const box = svg.getBoundingClientRect();
      const px = (x / W) * box.width;
      const flip = px > box.width - 170;
      tooltip.style.left = `${Math.max(4, flip ? px - 160 : px + 12)}px`;
      tooltip.style.top = '4px';
    });
    rect.addEventListener('mouseleave', () => { tooltip.style.opacity = '0'; });
    svg.appendChild(rect);
  });
  return svg;
}

/**
 * Horizontal range bar for the forecast: a 90% span, a 50% span inside it, and
 * a marked centre. Shows the width of the uncertainty instead of hiding it
 * behind a single number.
 */
export function rangeBar(container, { low, likelyLow, mid, likelyHigh, high, last, format = fmtNum }) {
  container.innerHTML = '';
  const W = 900, H = 96;
  const M = { left: 20, right: 20 };
  const plotW = W - M.left - M.right;

  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  container.appendChild(svg);

  const lo = Math.min(low, last), hi = Math.max(high, last);
  const X = (v) => M.left + ((v - lo) / (hi - lo || 1)) * plotW;
  const midY = 46;

  svg.appendChild(el('rect', {
    x: X(low), y: midY - 11, width: X(high) - X(low), height: 22,
    fill: 'var(--series-1)', 'fill-opacity': 0.13, rx: 4,
  }));
  svg.appendChild(el('rect', {
    x: X(likelyLow), y: midY - 11, width: X(likelyHigh) - X(likelyLow), height: 22,
    fill: 'var(--series-1)', 'fill-opacity': 0.3, rx: 4,
  }));
  svg.appendChild(el('line', {
    x1: X(mid), x2: X(mid), y1: midY - 17, y2: midY + 17,
    stroke: 'var(--series-1)', 'stroke-width': 3, 'stroke-linecap': 'round',
  }));
  // Today's close, so the whole range is read relative to where price is now.
  svg.appendChild(el('line', {
    x1: X(last), x2: X(last), y1: midY - 17, y2: midY + 17,
    stroke: 'var(--text-primary)', 'stroke-width': 2, 'stroke-dasharray': '3 3',
  }));

  const mark = (value, text, dy, anchor) => {
    const t = el('text', { class: 'tick-label', x: X(value), y: midY + dy, 'text-anchor': anchor });
    t.textContent = text;
    svg.appendChild(t);
  };
  mark(low, format(low), 34, 'start');
  mark(high, format(high), 34, 'end');
  mark(mid, `centre ${format(mid)}`, -24, 'middle');
  mark(last, `now ${format(last)}`, 34, 'middle');
  return svg;
}

export function legend(container, items) {
  container.innerHTML = items
    .map((i) => `<span class="item"><span class="swatch" style="background:${i.color}"></span>${i.label}</span>`)
    .join('');
}
