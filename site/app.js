/* League analytics front end: Alpine state + ECharts chart builders.
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

  Alpine.data('app', () => ({
    meta: null, history: null, seasons: {}, season: null,
    page: 'standings', loading: true, error: null, themeTick: 0,
    teamSel: null, ownerSel: null, luckTeam: 'all', luckFilter: 'all', includeCurrent: false,
    live: null, backtest: null, weekMid: null,
    pages: [
      { id: 'week', label: 'This Week' },
      { id: 'standings', label: 'Standings' },
      { id: 'luck', label: 'Luck Lab' },
      { id: 'coaching', label: 'Coaching' },
      { id: 'cap', label: 'Games Cap' },
      { id: 'franchises', label: 'Franchises' },
    ],
    fmt, tableHtml,

    async init() {
      const route = () => {
        const home = this.live?.active ? 'week' : 'standings';
        const p = location.hash.replace('#/', '') || home;
        this.page = this.pages.some((x) => x.id === p) ? p : home;
        if (this.page === 'cap') this.loadAll();
      };
      addEventListener('hashchange', route);
      matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => this.themeTick++);
      try {
        const get = (f) => fetch(`data/${f}`, { cache: 'no-cache' }).then((r) => { if (!r.ok) throw new Error(f); return r.json(); });
        const optional = (f) => get(f).catch(() => null);
        [this.meta, this.history, this.live, this.backtest] = await Promise.all(
          [get('meta.json'), get('history.json'), optional('live.json'), optional('backtest.json')]);
        if (!this.live?.active) this.pages = this.pages.filter((p) => p.id !== 'week');
        this.weekMid = this.live?.matchups?.[0]?.matchup_id ?? null;
        const cur = this.meta.current_season;
        await this.loadSeason(cur);
        const hasGames = this.seasons[cur].weeks.some((w) => w.result && !w.playoff);
        await this.selectSeason(hasGames ? cur : Math.max(...this.meta.seasons.filter((s) => s !== cur)));
        const ranked = [...this.history.all_time_complete].sort((a, b) => b.win_pct - a.win_pct);
        this.ownerSel = ranked[0]?.owner_id;
      } catch (e) {
        this.error = `Couldn't load league data (${e.message}). Run python pipeline/build.py first.`;
      }
      this.loading = false;
      route();
    },
    async loadSeason(s) {
      if (!this.seasons[s]) {
        const r = await fetch(`data/season_${s}.json`, { cache: 'no-cache' });
        this.seasons[s] = await r.json();
      }
      return this.seasons[s];
    },
    loadAll() { return Promise.all((this.meta?.seasons || []).map((s) => this.loadSeason(s))); },
    async selectSeason(s) {
      s = +s;
      await this.loadSeason(s);
      this.season = s;
      const ts = this.seasons[s].teams;
      if (!ts.some((t) => t.team_id === this.teamSel)) {
        this.teamSel = [...ts].sort((a, b) => (a.seed ?? 99) - (b.seed ?? 99))[0]?.team_id;
      }
      this.luckTeam = 'all';
    },
    toggleTheme() {
      document.documentElement.dataset.theme = isDark() ? 'light' : 'dark';
      try { localStorage.setItem('theme', document.documentElement.dataset.theme); } catch (e) { /* storage blocked */ }
      this.themeTick++;
    },
    get dark() { void this.themeTick; return isDark(); },

    // ---------- season accessors
    get S() { return this.seasons[this.season]; },
    get teams() { return this.S ? [...this.S.teams].sort((a, b) => (a.seed ?? 99) - (b.seed ?? 99) || b.w - a.w || b.pf - a.pf) : []; },
    team(id) { return this.S?.teams.find((t) => t.team_id === id); },
    tname(id) { return this.team(id)?.name ?? '—'; },
    get regWeeks() { return this.S ? this.S.weeks.filter((w) => w.result && !w.playoff) : []; },
    get doneMps() { return [...new Set(this.regWeeks.map((w) => w.mp))].sort((a, b) => a - b); },
    cap(mp) { return this.S?.caps.find((c) => c.mp === mp); },
    wk(mp) { return `Wk ${mp}`; },
    wkDates(mp) { const c = this.cap(mp); return c ? `${fmt.date(c.start)} – ${fmt.date(c.end)}` : ''; },
    owner(id) { return this.history?.owners[id] ?? 'Unknown'; },
    get hasData() { return this.regWeeks.length > 0; },
    get updated() {
      if (!this.meta) return '';
      return new Date(this.meta.updated_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    },

    // ---------- stat tiles
    get tiles() {
      const T = this.S?.teams.filter((t) => t.w + t.l + t.t > 0) || [];
      if (!T.length) return [];
      const by = (k, dir = -1) => [...T].sort((a, b) => dir * ((a[k] ?? -1e9) - (b[k] ?? -1e9)))[0];
      const best = [...T].sort((a, b) => b.w - a.w || b.pf - a.pf)[0];
      const pf = by('pf'), lucky = by('luck'), unlucky = by('luck', 1), coach = by('efficiency');
      return [
        { label: 'Best record', value: fmt.rec(best), sub: best.name },
        { label: 'Most points', value: fmt.n(pf.pf), sub: pf.name },
        { label: 'Luckiest', value: fmt.signed(lucky.luck) + ' W', sub: lucky.name },
        { label: 'Unluckiest', value: fmt.signed(unlucky.luck) + ' W', sub: unlucky.name },
        { label: 'Top coach', value: fmt.pct(coach.efficiency), sub: coach.name },
      ];
    },

    // ---------- Standings
    winsChart() {
      void this.themeTick;
      const T = [...(this.S?.teams || [])].sort((a, b) => a.w - b.w || a.exp_w - b.exp_w);
      return {
        option: {
          ...base(),
          legend: { ...base().legend, data: ['Actual wins', 'Expected wins'] },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const t = T[ps[0].dataIndex]; return `<b>${esc(t.name)}</b>` + tipRow('Actual', fmt.rec(t)) + tipRow('Expected', fmt.n(t.exp_w, 1)) + tipRow('Luck', fmt.signed(t.luck) + ' wins'); } },
          xAxis: axis({ type: 'value', minInterval: 1 }),
          yAxis: catAxis(T.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
          series: [
            { type: 'custom', silent: true, tooltip: { show: false }, data: T.map((t, i) => [t.w, t.exp_w, i]), encode: { x: [0, 1], y: 2 },
              renderItem: (p, api) => { const a = api.coord([api.value(0), api.value(2)]), b = api.coord([api.value(1), api.value(2)]);
                return { type: 'line', shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style: { stroke: TOK('--axis'), lineWidth: 2 } }; } },
            { type: 'scatter', name: 'Expected wins', data: T.map((t, i) => [t.exp_w, i]), symbolSize: 12,
              itemStyle: { color: TOK('--surface'), borderColor: TOK('--muted'), borderWidth: 2 } },
            { type: 'scatter', name: 'Actual wins', data: T.map((t, i) => [t.w, i]), symbolSize: 12,
              itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 } },
          ],
        },
        table: { cols: [{ label: 'Team' }, { label: 'Actual W', num: 1 }, { label: 'Expected W', num: 1 }, { label: 'Luck', num: 1 }],
                 rows: [...T].reverse().map((t) => [t.name, t.w, fmt.n(t.exp_w, 1), fmt.signed(t.luck)]) },
      };
    },
    scoresChart() {
      void this.themeTick;
      const W = (this.S?.weeks || []).filter((w) => w.team_id === this.teamSel && w.result).sort((a, b) => a.mp - b.mp);
      const name = this.tname(this.teamSel);
      return {
        option: {
          ...base(),
          legend: { ...base().legend, data: [name, 'League average'] },
          grid: { ...base().grid, right: 16 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: TOK('--axis') } },
            formatter: (ps) => { const w = W[ps[0].dataIndex];
              return `<b>${this.wk(w.mp)}</b> <span style="color:${TOK('--muted')}">${this.wkDates(w.mp)}${w.playoff ? ' · Playoffs' : ''}</span>`
                + tipRow(esc(name), fmt.n(w.pts)) + tipRow('vs ' + esc(this.tname(w.opp_id)), fmt.n(w.opp_pts))
                + tipRow('Result', `${w.result} (${fmt.signed(w.margin, 0)})`) + tipRow('Weekly rank', `${w.rank} of ${w.n_others + 1}`)
                + tipRow('League avg', fmt.n(w.week_avg)); } },
          xAxis: catAxis(W.map((w) => this.wk(w.mp)), { boundaryGap: false }),
          yAxis: axis({ type: 'value', scale: true }),
          series: [
            { type: 'line', name: 'League average', data: W.map((w) => w.week_avg), symbol: 'none',
              lineStyle: { width: 2, color: TOK('--muted') }, itemStyle: { color: TOK('--muted') } },
            { type: 'line', name, data: W.map((w) => w.pts), symbol: 'circle', symbolSize: 8,
              lineStyle: { width: 2, color: TOK('--s1') }, itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 } },
          ],
        },
        table: { cols: [{ label: 'Week' }, { label: 'Score', num: 1 }, { label: 'Opponent' }, { label: 'Opp score', num: 1 }, { label: 'Result' }, { label: 'League avg', num: 1 }],
                 rows: W.map((w) => [this.wk(w.mp), fmt.n(w.pts), this.tname(w.opp_id), fmt.n(w.opp_pts), w.result, fmt.n(w.week_avg)]) },
      };
    },

    // ---------- Luck Lab
    divBars(T, key, { label, invert = false, digits = 1, suffix = '' }) {
      const [neg, , pos] = RAMPS.div[this.dark ? 'dark' : 'light'];
      const D = [...T].sort((a, b) => a[key] - b[key]);
      return {
        ...base(),
        grid: { ...base().grid, top: 8, left: 8, right: 48 },
        tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
          formatter: (ps) => { const t = D[ps[0].dataIndex]; return `<b>${esc(t.name)}</b>` + tipRow(label, fmt.signed(t[key], digits) + suffix); } },
        // pad both ends so the outside value labels never collide with the team names
        xAxis: { ...labeledValueAxis(), min: (v) => Math.min(0, v.min * 1.35), max: (v) => Math.max(0, v.max * 1.2) },
        yAxis: catAxis(D.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
        series: [{
          type: 'bar', barWidth: 14,
          data: D.map((t) => { const v = t[key]; const good = invert ? v < 0 : v > 0;
            return { value: v, itemStyle: { color: good ? pos : neg, borderRadius: v >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] },
                     label: { position: v < 0 ? 'left' : 'right' } }; }),
          label: { show: true, color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.signed(p.value, digits) },
        }],
      };
    },
    luckChart() {
      void this.themeTick;
      const T = this.S?.teams || [];
      return {
        option: this.divBars(T, 'luck', { label: 'Luck', suffix: ' wins' }),
        table: { cols: [{ label: 'Team' }, { label: 'Record' }, { label: 'Expected W', num: 1 }, { label: 'Luck', num: 1 }, { label: 'Bad beats', num: 1 }, { label: 'Lucky wins', num: 1 }],
                 rows: [...T].sort((a, b) => b.luck - a.luck).map((t) => [t.name, fmt.rec(t), fmt.n(t.exp_w, 1), fmt.signed(t.luck), t.bad_beats, t.lucky_wins]) },
      };
    },
    paChart() {
      void this.themeTick;
      const T = (this.S?.teams || []).map((t) => ({ ...t, pa_pg: t.pa_vs_avg / Math.max(1, t.w + t.l + t.t) }));
      return {
        option: this.divBars(T, 'pa_pg', { label: 'Opponents vs avg', invert: true, digits: 0, suffix: ' pts/wk' }),
        table: { cols: [{ label: 'Team' }, { label: 'Points against', num: 1 }, { label: 'Vs avg opponent, per week', num: 1 }],
                 rows: [...T].sort((a, b) => b.pa_pg - a.pa_pg).map((t) => [t.name, fmt.n(t.pa), fmt.signed(t.pa_pg, 0)]) },
      };
    },
    heatmap({ xs, ys, cell, ramp, domain, label, tooltip, highlight }) {
      const stops = RAMPS[ramp][this.dark ? 'dark' : 'light'];
      const [lo, hi] = domain;
      const data = [];
      ys.forEach((y, yi) => xs.forEach((x, xi) => {
        const c = cell(x, y);
        if (c == null) return;
        const fill = rampColor(stops, hi === lo ? 0.5 : (c.v - lo) / (hi - lo));
        data.push({ value: [xi, yi, c.v], raw: c,
          itemStyle: { color: fill, borderColor: highlight?.(x, y) ? TOK('--ink') : TOK('--surface'), borderWidth: highlight?.(x, y) ? 2 : 2, borderRadius: 4 },
          label: { color: inkOn(fill) } });
      }));
      return {
        ...base(),
        grid: { ...base().grid, top: 8, right: 8 },
        tooltip: { ...base().tooltip, formatter: (p) => tooltip(xs[p.value[0]], ys[p.value[1]], p.data.raw) },
        xAxis: catAxis(xs.map((x) => x.label), { position: 'top', axisLine: { show: false }, axisLabel: { color: TOK('--muted'), fontSize: 11, interval: 0 } }),
        yAxis: catAxis(ys.map((y) => y.label), { inverse: true, axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
        series: [{ type: 'heatmap', data, label: { show: true, fontSize: 11, formatter: (p) => label(p.data.raw) },
                   emphasis: { itemStyle: { borderColor: TOK('--ink'), borderWidth: 2 } } }],
      };
    },
    swapChart() {
      void this.themeTick;
      if (!this.S) return null;
      const T = this.teams, M = this.S.schedule_swap;
      const xs = T.map((t) => ({ id: t.team_id, label: t.abbrev || t.name.slice(0, 4) }));
      const ys = T.map((t) => ({ id: t.team_id, label: t.name }));
      const vals = T.flatMap((a) => T.map((b) => M[a.team_id]?.[b.team_id]?.[0] ?? 0));
      return {
        option: this.heatmap({
          xs, ys, ramp: 'seq', domain: [Math.min(...vals), Math.max(...vals)],
          cell: (x, y) => { const r = M[y.id]?.[x.id]; return r ? { v: r[0], w: r[0], l: r[1] } : null; },
          label: (c) => `${c.w}-${c.l}`,
          highlight: (x, y) => x.id === y.id,
          tooltip: (x, y, c) => `<b>${esc(y.label)}</b> with ${esc(this.tname(x.id))}'s schedule` + tipRow('Record', `${c.w}-${c.l}`)
            + tipRow('Actual record', fmt.rec(this.team(y.id))),
        }),
        table: { cols: [{ label: 'Team ↓ / schedule of →' }, ...T.map((t) => ({ label: t.abbrev || t.name, num: 1 }))],
                 rows: T.map((a) => [a.name, ...T.map((b) => (M[a.team_id]?.[b.team_id] || []).join('-'))]) },
      };
    },
    absenceChart() {
      void this.themeTick;
      const T = [...(this.S?.teams || [])].sort((a, b) => a.missed_pts - b.missed_pts);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 8, right: 56 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const t = T[ps[0].dataIndex]; return `<b>${esc(t.name)}</b>` + tipRow('Points lost to absences', fmt.n(t.missed_pts)); } },
          xAxis: labeledValueAxis(),
          yAxis: catAxis(T.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
          series: [{ type: 'bar', barWidth: 14, data: T.map((t) => t.missed_pts),
                     itemStyle: { color: TOK('--s1'), borderRadius: [0, 4, 4, 0] },
                     label: { show: true, position: 'right', color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.n(p.value) } }],
        },
        table: { cols: [{ label: 'Team' }, { label: 'Points lost to absences', num: 1 }],
                 rows: [...T].reverse().map((t) => [t.name, fmt.n(t.missed_pts)]) },
      };
    },
    get luckRows() {
      if (!this.S) return [];
      return this.S.weeks
        .filter((w) => w.result && w.opp_id != null && !w.playoff)
        .filter((w) => this.luckTeam === 'all' || w.team_id === +this.luckTeam)
        .filter((w) => this.luckFilter === 'all' || (this.luckFilter === 'bad' ? w.bad_beat : w.lucky_win))
        .sort((a, b) => b.mp - a.mp || b.pts - a.pts);
    },
    get badBeats() {
      return this.regWeeks.filter((w) => w.bad_beat).sort((a, b) => a.rank - b.rank || b.pts - a.pts).slice(0, 5);
    },

    // ---------- Coaching
    effChart() {
      void this.themeTick;
      const T = [...(this.S?.teams || [])].filter((t) => t.efficiency != null).sort((a, b) => a.efficiency - b.efficiency);
      const min = Math.floor((Math.min(...T.map((t) => t.efficiency)) - 0.02) * 50) / 50;
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 8, right: 56 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const t = T[ps[0].dataIndex];
              return `<b>${esc(t.name)}</b>` + tipRow('Lineup efficiency', fmt.pct(t.efficiency)) + tipRow('Points left on bench', fmt.n(t.bench_lost))
                + tipRow('Coaching losses', t.coaching_losses); } },
          xAxis: axis({ type: 'value', min: Math.max(0, min), max: 1, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => Math.round(v * 100) + '%' } }),
          yAxis: catAxis(T.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
          series: [{ type: 'scatter', symbolSize: 13, data: T.map((t, i) => [t.efficiency, i]),
                     itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 },
                     label: { show: true, position: 'right', color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.pct(p.value[0]) } }],
        },
        table: { cols: [{ label: 'Team' }, { label: 'Efficiency', num: 1 }, { label: 'Bench points lost', num: 1 }, { label: 'Coaching losses', num: 1 }],
                 rows: [...T].reverse().map((t) => [t.name, fmt.pct(t.efficiency), fmt.n(t.bench_lost), t.coaching_losses]) },
      };
    },
    effHeatChart() {
      void this.themeTick;
      if (!this.S) return null;
      const mps = this.doneMps, T = this.teams;
      const by = {}; this.regWeeks.forEach((w) => { by[`${w.team_id}-${w.mp}`] = w; });
      const vals = this.regWeeks.map((w) => w.efficiency).filter((v) => v != null);
      return {
        option: this.heatmap({
          xs: mps.map((m) => ({ mp: m, label: String(m) })), ys: T.map((t) => ({ id: t.team_id, label: t.name })),
          ramp: 'seq', domain: [Math.min(0.8, ...vals), 1],
          cell: (x, y) => { const w = by[`${y.id}-${x.mp}`]; return w && w.efficiency != null ? { v: w.efficiency, w } : null; },
          label: (c) => Math.round(c.v * 100),
          tooltip: (x, y, c) => `<b>${esc(y.label)}</b> · ${this.wk(x.mp)}` + tipRow('Efficiency', fmt.pct(c.v))
            + tipRow('Scored', fmt.n(c.w.pts)) + tipRow('Best possible', fmt.n(c.w.opt_pts))
            + (c.w.missed_starts?.length ? `<div style="margin-top:4px;color:${TOK('--muted')}">Missed: ${c.w.missed_starts.map((m) => `${esc(m.name)} (${fmt.n(m.pts)})`).join(', ')}</div>` : ''),
        }),
        table: { cols: [{ label: 'Team' }, ...mps.map((m) => ({ label: 'Wk ' + m, num: 1 }))],
                 rows: T.map((t) => [t.name, ...mps.map((m) => { const w = by[`${t.team_id}-${m}`]; return w ? fmt.pct(w.efficiency, 0) : '–'; })]) },
      };
    },
    benchChart() {
      void this.themeTick;
      const T = [...(this.S?.teams || [])].sort((a, b) => a.bench_lost - b.bench_lost);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 8, right: 56 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const t = T[ps[0].dataIndex]; return `<b>${esc(t.name)}</b>` + tipRow('Points left on bench', fmt.n(t.bench_lost)); } },
          xAxis: labeledValueAxis(),
          yAxis: catAxis(T.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
          series: [{ type: 'bar', barWidth: 14, data: T.map((t) => t.bench_lost), itemStyle: { color: TOK('--s1'), borderRadius: [0, 4, 4, 0] },
                     label: { show: true, position: 'right', color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.n(p.value) } }],
        },
        table: { cols: [{ label: 'Team' }, { label: 'Points left on bench', num: 1 }], rows: [...T].reverse().map((t) => [t.name, fmt.n(t.bench_lost)]) },
      };
    },
    get coachingLosses() {
      return (this.S?.weeks || []).filter((w) => w.coaching_loss).sort((a, b) => (b.opt_pts - b.opp_pts) - (a.opt_pts - a.opp_pts));
    },

    // ---------- Games cap
    capChart() {
      void this.themeTick;
      const C = this.S?.caps || [];
      const counts = {}; C.forEach((c) => { counts[c.cap] = (counts[c.cap] || 0) + 1; });
      const usual = +Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0];
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 24, right: 8 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const c = C[ps[0].dataIndex];
              return `<b>${this.wk(c.mp)}</b>${c.playoff ? ' · Playoffs' : ''} <span style="color:${TOK('--muted')}">${fmt.date(c.start)} – ${fmt.date(c.end)}</span>`
                + tipRow('Games cap', c.cap) + tipRow('NBA game days', `${c.game_days} of ${c.days}`); } },
          xAxis: catAxis(C.map((c) => String(c.mp)), { axisLabel: { color: TOK('--muted'), fontSize: 11, interval: 0 } }),
          yAxis: axis({ type: 'value' }),
          series: [{ type: 'bar', barMaxWidth: 28,
            data: C.map((c) => ({ value: c.cap, itemStyle: { color: TOK('--s1'), opacity: c.playoff ? 0.5 : 1, borderRadius: [4, 4, 0, 0] } })),
            label: { show: true, position: 'top', color: TOK('--ink2'), fontSize: 11, formatter: (p) => (p.value !== usual ? p.value : '') } }],
        },
        table: { cols: [{ label: 'Week' }, { label: 'Dates' }, { label: 'Days', num: 1 }, { label: 'NBA game days', num: 1 }, { label: 'Cap', num: 1 }],
                 rows: C.map((c) => [this.wk(c.mp) + (c.playoff ? ' (playoffs)' : ''), `${fmt.date(c.start)} – ${fmt.date(c.end)}`, c.days, c.game_days, c.cap]) },
      };
    },
    unusedChart() {
      void this.themeTick;
      if (!this.S) return null;
      const mps = this.doneMps, T = this.teams;
      const by = {}; this.regWeeks.forEach((w) => { by[`${w.team_id}-${w.mp}`] = w; });
      const unused = (w) => Math.max(0, (w.cap ?? 0) - (w.gp ?? 0));
      const vals = this.regWeeks.map(unused);
      return {
        option: this.heatmap({
          xs: mps.map((m) => ({ mp: m, label: String(m) })), ys: T.map((t) => ({ id: t.team_id, label: t.name })),
          ramp: 'seq', domain: [0, Math.max(4, ...vals)],
          cell: (x, y) => { const w = by[`${y.id}-${x.mp}`]; return w ? { v: unused(w), w } : null; },
          label: (c) => (c.v ? c.v : '·'),
          tooltip: (x, y, c) => `<b>${esc(y.label)}</b> · ${this.wk(x.mp)}` + tipRow('Games used', `${fmt.n(c.w.gp)} of ${c.w.cap}`)
            + tipRow('Unused games', c.v) + tipRow('Roster could fill', fmt.n(c.w.max_games))
            + (c.w.over_cap ? `<div style="margin-top:4px">⚠ Over the cap</div>` : ''),
        }),
        table: { cols: [{ label: 'Team' }, ...mps.map((m) => ({ label: 'Wk ' + m, num: 1 }))],
                 rows: T.map((t) => [t.name, ...mps.map((m) => { const w = by[`${t.team_id}-${m}`]; return w ? `${fmt.n(w.gp)}/${w.cap}` : '–'; })]) },
      };
    },
    get capLedger() {
      void Object.keys(this.seasons).length;
      const rows = [];
      for (const s of Object.keys(this.seasons).map(Number).sort((a, b) => b - a)) {
        const S = this.seasons[s];
        const nm = Object.fromEntries(S.teams.map((t) => [t.team_id, t.name]));
        S.weeks.filter((w) => w.over_cap).forEach((w) => rows.push({ season: s, mp: w.mp, team: nm[w.team_id], owner: this.owner(w.owner_id),
          gp: w.gp, cap: w.cap, over: w.gp - w.cap, result: w.result, margin: w.margin }));
      }
      return rows;
    },

    // ---------- Franchises
    get allTime() {
      const A = this.includeCurrent ? this.history?.all_time : this.history?.all_time_complete;
      return [...(A || [])].sort((a, b) => b.titles - a.titles || b.win_pct - a.win_pct);
    },
    get champions() { return (this.history?.seasons || []).filter((s) => s.champion).sort((a, b) => b.season - a.season); },
    get ownerSeasons() { return (this.history?.seasons || []).filter((s) => s.owner_id === this.ownerSel).sort((a, b) => a.season - b.season); },
    get ownerList() {
      return [...(this.history?.all_time || [])].sort((a, b) => this.owner(a.owner_id).localeCompare(this.owner(b.owner_id)));
    },
    finishChart() {
      void this.themeTick;
      const R = this.ownerSeasons.filter((s) => s.complete);
      const maxTeams = Math.max(8, ...(this.history?.seasons || []).map((s) => s.rank_final || 0));
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 16, right: 24, left: 8 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: TOK('--axis') } },
            formatter: (ps) => { const s = R[ps[0].dataIndex];
              return `<b>${fmt.seasonLabel(s.season)}</b> · ${esc(s.name)}` + tipRow('Finish', fmt.ord(s.rank_final) + (s.champion ? ' 🏆' : ''))
                + tipRow('Record', fmt.rec(s)) + tipRow('Luck', fmt.signed(s.luck) + ' W') + tipRow('Coaching', fmt.pct(s.efficiency)); } },
          xAxis: catAxis(R.map((s) => fmt.seasonLabel(s.season)), { boundaryGap: true }),
          yAxis: axis({ type: 'value', inverse: true, min: 1, max: maxTeams, interval: 1, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => fmt.ord(v) } }),
          series: [{ type: 'line', data: R.map((s) => s.rank_final), symbol: 'circle', symbolSize: 10,
                     lineStyle: { width: 2, color: TOK('--s1') }, itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 },
                     label: { show: true, position: 'top', color: TOK('--ink2'), fontSize: 11, formatter: (p) => (R[p.dataIndex].champion ? '🏆' : fmt.ord(p.value)) } }],
        },
        table: { cols: [{ label: 'Season' }, { label: 'Team' }, { label: 'Finish', num: 1 }, { label: 'Record' }],
                 rows: R.map((s) => [fmt.seasonLabel(s.season), s.name, fmt.ord(s.rank_final), fmt.rec(s)]) },
      };
    },
    h2hChart() {
      void this.themeTick;
      if (!this.history) return null;
      const H = this.history.h2h;
      const O = [...this.history.all_time].filter((a) => a.w + a.l + a.t > 0).sort((a, b) => b.win_pct - a.win_pct)
        .map((a) => ({ id: a.owner_id, label: this.owner(a.owner_id) }));
      return {
        option: this.heatmap({
          xs: O.map((o) => ({ ...o, label: o.label.length > 10 ? o.label.slice(0, 9) + '…' : o.label })), ys: O,
          ramp: 'div', domain: [0, 1],
          cell: (x, y) => { if (x.id === y.id) return null; const r = H[y.id]?.[x.id]; if (!r) return null;
            const g = r.w + r.l + r.t; return g ? { v: (r.w + 0.5 * r.t) / g, ...r } : null; },
          label: (c) => `${c.w}-${c.l}`,
          tooltip: (x, y, c) => `<b>${esc(y.label)}</b> vs ${esc(this.owner(x.id))}` + tipRow('All-time', `${c.w}-${c.l}${c.t ? '-' + c.t : ''}`)
            + tipRow('Win %', fmt.pct(c.v, 0)) + tipRow('Playoffs', `${c.playoff_w}-${c.playoff_l}`)
            + tipRow('Points', `${fmt.n(c.pf)} – ${fmt.n(c.pa)}`),
        }),
        table: { cols: [{ label: 'Franchise ↓ vs →' }, ...O.map((o) => ({ label: o.label, num: 1 }))],
                 rows: O.map((y) => [y.label, ...O.map((x) => { const r = H[y.id]?.[x.id]; return r ? `${r.w}-${r.l}` : '–'; })]) },
      };
    },
    recordLabel(r) { return `${fmt.seasonLabel(r.season)} · Wk ${r.mp}`; },

    // ---------- This Week (live predictor)
    get selMatchup() { return this.live?.matchups?.find((m) => m.matchup_id === this.weekMid) || this.live?.matchups?.[0]; },
    dayLabel(d) {
      const dt = new Date(d.date + 'T12:00:00');
      return { dow: dt.toLocaleDateString('en-US', { weekday: 'short' }), md: `${dt.getMonth() + 1}/${dt.getDate()}` };
    },
    get weekRange() {
      const D = this.live?.days || [];
      return D.length ? `${fmt.date(D[0].date)} – ${fmt.date(D[D.length - 1].date)}` : '';
    },
    get liveUpdated() {
      return this.live ? new Date(this.live.updated_at).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : '';
    },
    cellStyle(d) {
      if (d.state !== 'game' || !d.start) return '';
      return `background: color-mix(in srgb, var(--s1) ${Math.round(8 + d.start * 52)}%, transparent)`;
    },
    timelineChart() {
      void this.themeTick;
      const m = this.selMatchup;
      if (!m) return null;
      const id = String(m.matchup_id), hid = String(m.home.team_id);
      const S = (this.live.snapshots || []).filter((s) => s.probs[id]).map((s) => [s.ts * 1000, s.probs[id][hid]]);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 16, right: 16 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: TOK('--axis') } },
            formatter: (ps) => { const [ts, p] = ps[0].value;
              return `<b>${new Date(ts).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' })}</b>`
                + tipRow(esc(m.home.name), fmt.pct(p, 0)) + tipRow(esc(m.away.name), fmt.pct(1 - p, 0)); } },
          xAxis: axis({ type: 'time', splitLine: { show: false } }),
          yAxis: axis({ type: 'value', min: 0, max: 1, interval: 0.25, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => Math.round(v * 100) + '%' } }),
          series: [{ type: 'line', data: S, symbol: 'circle', symbolSize: S.length > 30 ? 0 : 8, showSymbol: S.length <= 30,
                     lineStyle: { width: 2, color: TOK('--s1') }, itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 },
                     markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: TOK('--axis'), type: 'solid', width: 1 }, data: [{ yAxis: 0.5 }] } }],
        },
        table: { cols: [{ label: 'Time' }, { label: m.home.name, num: 1 }, { label: m.away.name, num: 1 }],
                 rows: S.map(([ts, p]) => [new Date(ts).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' }), fmt.pct(p, 0), fmt.pct(1 - p, 0)]) },
      };
    },
    marginChart() {
      void this.themeTick;
      const m = this.selMatchup;
      if (!m) return null;
      const { edges, counts } = m.margin_hist;
      const total = counts.reduce((a, b) => a + b, 0) || 1;
      const mids = counts.map((_, i) => (edges[i] + edges[i + 1]) / 2);
      const s2 = TOK('--s2'), s1 = TOK('--s1');
      return {
        option: {
          ...base(),
          legend: { ...base().legend, data: [`${m.home.name} wins`, `${m.away.name} wins`] },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const i = ps[0].dataIndex; const lo = edges[i], hi = edges[i + 1];
              const who = mids[i] >= 0 ? m.home.name : m.away.name;
              return `<b>${esc(who)} by ${fmt.n(Math.abs(mids[i] >= 0 ? lo : hi))}–${fmt.n(Math.abs(mids[i] >= 0 ? hi : lo))}</b>` + tipRow('Share of simulations', fmt.pct(counts[i] / total)); } },
          xAxis: catAxis(mids.map((x) => fmt.n(x)), { axisLabel: { color: TOK('--muted'), fontSize: 11, interval: Math.ceil(mids.length / 8) } }),
          yAxis: axis({ type: 'value', axisLabel: { show: false } }),
          series: [
            { type: 'bar', name: `${m.home.name} wins`, stack: 'm', barCategoryGap: '8%', data: counts.map((c, i) => (mids[i] >= 0 ? c : '-')), itemStyle: { color: s1, borderRadius: [3, 3, 0, 0] } },
            { type: 'bar', name: `${m.away.name} wins`, stack: 'm', data: counts.map((c, i) => (mids[i] < 0 ? c : '-')), itemStyle: { color: s2, borderRadius: [3, 3, 0, 0] } },
          ],
        },
        table: { cols: [{ label: `Final margin (${m.home.name} − ${m.away.name})` }, { label: 'Share of simulations', num: 1 }],
                 rows: counts.map((c, i) => [`${fmt.n(edges[i])} to ${fmt.n(edges[i + 1])}`, fmt.pct(c / total)]) },
      };
    },
    calibChart() {
      void this.themeTick;
      const C = (this.backtest?.calibration || []).filter((c) => c.n > 0);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 28, right: 16 },
          legend: { ...base().legend, data: ['Actual win rate', 'Perfect calibration'] },
          tooltip: { ...base().tooltip, trigger: 'item',
            formatter: (p) => { const c = C[p.dataIndex]; return c ? tipRow('Predicted', fmt.pct(c.p_mean, 0)) + tipRow('Actually won', fmt.pct(c.won, 0)) + tipRow('Forecasts', fmt.n(c.n)) : ''; } },
          xAxis: axis({ type: 'value', min: 0, max: 1, name: 'Predicted', nameLocation: 'middle', nameGap: 24, nameTextStyle: { color: TOK('--muted') }, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => Math.round(v * 100) + '%' } }),
          yAxis: axis({ type: 'value', min: 0, max: 1, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => Math.round(v * 100) + '%' } }),
          series: [
            { type: 'line', name: 'Perfect calibration', data: [[0, 0], [1, 1]], symbol: 'none', silent: true, tooltip: { show: false },
              lineStyle: { width: 1, color: TOK('--axis') }, itemStyle: { color: TOK('--axis') } },
            { type: 'line', name: 'Actual win rate', data: C.map((c) => [c.p_mean, c.won]), symbol: 'circle', symbolSize: 9,
              lineStyle: { width: 2, color: TOK('--s1') }, itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 } },
          ],
        },
        table: { cols: [{ label: 'Predicted (avg)', num: 1 }, { label: 'Actually won', num: 1 }, { label: 'Forecasts', num: 1 }],
                 rows: C.map((c) => [fmt.pct(c.p_mean, 0), fmt.pct(c.won, 0), fmt.n(c.n)]) },
      };
    },
    byDayChart() {
      void this.themeTick;
      const D = (this.backtest?.by_day || []).filter((d) => d.n >= 20);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 16, right: 16 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const d = D[ps[0].dataIndex]; return `<b>Start of day ${d.day_idx + 1}</b>` + tipRow('Brier score', d.brier.toFixed(3)) + tipRow('Forecasts', d.n); } },
          xAxis: catAxis(D.map((d) => 'Day ' + (d.day_idx + 1))),
          yAxis: axis({ type: 'value', min: 0, max: 0.25 }),
          series: [{ type: 'bar', barMaxWidth: 28, data: D.map((d) => d.brier), itemStyle: { color: TOK('--s1'), borderRadius: [4, 4, 0, 0] },
                     markLine: { silent: true, symbol: 'none', lineStyle: { color: TOK('--muted'), type: 'solid', width: 1 },
                                 label: { formatter: 'Coin flip', color: TOK('--muted'), fontSize: 11, position: 'insideEndTop' }, data: [{ yAxis: 0.25 }] } }],
        },
        table: { cols: [{ label: 'Forecast made' }, { label: 'Brier score', num: 1 }, { label: 'Forecasts', num: 1 }],
                 rows: D.map((d) => ['Start of day ' + (d.day_idx + 1), d.brier.toFixed(3), d.n]) },
      };
    },
    get upsets() {
      return (this.backtest?.pregame_probs || []).filter((r) => r.season === this.season && r.won === 1)
        .sort((a, b) => a.p - b.p).slice(0, 5);
    },
  }));
});

// Apply stored theme before first paint of charts
try { const t = localStorage.getItem('theme'); if (t) document.documentElement.dataset.theme = t; } catch (e) { /* storage blocked */ }
