/* Shared formatting, color and ECharts helpers + the x-chart directive.
   Every chart builder returns {option, table} so each chart has a table-view twin. */

const TOK = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
};

// Sequential (one hue, light -> dark; near-zero recedes toward the surface) and diverging ramps
const RAMPS = {
  seq: { light: ['#cde2fb', '#86b6ef', '#3987e5', '#1c5cab', '#0d366b'],
         dark:  ['#1b2c42', '#1c5cab', '#2a78d6', '#6da7ec', '#b7d3f6'] },
  div: { light: ['#e34948', '#f0efec', '#2a78d6'],
         dark:  ['#e66767', '#383835', '#3987e5'] },
};

const fmt = {
  n: (x, d = 0) => (x == null || Number.isNaN(+x)) ? '–'
    : Number(x).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }),
  pct: (x, d = 1) => x == null ? '–' : (x * 100).toFixed(d) + '%',
  signed: (x, d = 1) => x == null ? '–' : (x > 0 ? '+' : x < 0 ? '−' : '') + Math.abs(x).toFixed(d),
  rec: (t) => `${t.w ?? 0}-${t.l ?? 0}${t.t ? '-' + t.t : ''}`,
  date: (iso) => iso ? new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '',
  ord: (n) => n == null ? '–' : n + (['th', 'st', 'nd', 'rd'][(n % 100 - 20) % 10] || ['th', 'st', 'nd', 'rd'][n % 100] || 'th'),
  seasonLabel: (s) => `${s - 1}-${String(s).slice(2)}`,
};

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function tableHtml(spec) {
  if (!spec || !spec.table) return '';
  const { cols, rows } = spec.table;
  return `<div class="tbl-wrap"><table class="tbl"><thead><tr>${cols.map((c) =>
    `<th class="${c.num ? 'num' : ''}">${esc(c.label)}</th>`).join('')}</tr></thead><tbody>${rows.map((r) =>
    `<tr>${cols.map((c, i) => `<td class="${c.num ? 'num' : ''}">${esc(r[i])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

// ---- color helpers (for per-cell heatmap fills + readable labels)
const hex2rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
function rampColor(stops, t) {
  t = Math.max(0, Math.min(1, t));
  const seg = (stops.length - 1) * t, i = Math.min(Math.floor(seg), stops.length - 2), f = seg - i;
  const a = hex2rgb(stops[i]), b = hex2rgb(stops[i + 1]);
  return '#' + a.map((v, k) => Math.round(v + (b[k] - v) * f).toString(16).padStart(2, '0')).join('');
}
function inkOn(hex) {
  const [r, g, b] = hex2rgb(hex).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.38 ? '#0b0b0b' : '#ffffff';
}

// ---- ECharts base styling from tokens
function base() {
  return {
    animationDuration: 350,
    textStyle: { fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: TOK('--ink2') },
    grid: { left: 4, right: 28, top: 36, bottom: 4, containLabel: true },
    tooltip: {
      confine: true, backgroundColor: TOK('--surface'), borderColor: TOK('--axis'), borderWidth: 1,
      textStyle: { color: TOK('--ink'), fontSize: 12 }, padding: [8, 10],
      extraCssText: 'border-radius:10px;box-shadow:0 8px 28px rgba(0,0,0,.14);',
    },
    legend: { top: 0, left: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 12, itemGap: 16,
              textStyle: { color: TOK('--ink2'), fontSize: 12 } },
  };
}
const axis = (o = {}) => ({
  axisLine: { lineStyle: { color: TOK('--axis') } }, axisTick: { show: false },
  axisLabel: { color: TOK('--muted'), fontSize: 11 }, splitLine: { lineStyle: { color: TOK('--grid') } }, ...o,
});
const catAxis = (data, o = {}) => axis({ type: 'category', data, splitLine: { show: false }, ...o });
// value axis for bar charts whose bars carry direct value labels: gridlines only, no tick labels
const labeledValueAxis = () => axis({ type: 'value', axisLabel: { show: false } });
const tipRow = (label, value) => `<div style="display:flex;justify-content:space-between;gap:16px"><span style="color:${TOK('--ink2')}">${label}</span><b>${value}</b></div>`;


document.addEventListener('alpine:init', () => {
  // x-chart="spec()" renders spec.option; re-runs whenever reactive inputs change
  Alpine.directive('chart', (el, { expression }, { effect, evaluateLater, cleanup }) => {
    const get = evaluateLater(expression);
    let chart = null;
    const ro = new ResizeObserver(() => chart && chart.resize());
    ro.observe(el);
    effect(() => get((spec) => {
      if (!spec) return;
      if (!chart) chart = echarts.init(el, null, { renderer: 'svg' });
      chart.setOption(spec.option, true);
    }));
    cleanup(() => { ro.disconnect(); chart && chart.dispose(); });
  });
});
