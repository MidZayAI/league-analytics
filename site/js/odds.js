/* Playoff & championship odds: League "Odds" view (stakes, rooting and team-page pieces come next). */

window.Odds = {
  odds: null, oddsHistory: null, oddsBacktest: null, oddsSort: 'playoff', oddsAsc: false,

  async loadOdds() {
    const get = (f) => fetch(`data/${f}`, { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    [this.odds, this.oddsHistory, this.oddsBacktest] = await Promise.all([get('odds.json'), get('odds_history.json'), get('odds_backtest.json')]);
  },
  get oddsCols() {
    return [
      { key: 'proj_w', label: 'Proj W', dir: 1, get: (r) => r.proj_w },
      { key: 'playoff', label: 'Playoffs', dir: 1, get: (r) => r.playoff },
      { key: 'bye', label: 'Bye', dir: 1, get: (r) => r.bye },
      { key: 'final', label: 'Final', dir: 1, get: (r) => r.final },
      { key: 'title', label: 'Title', dir: 1, get: (r) => r.title },
    ];
  },
  get oddsRows() {
    const rows = [...(this.odds?.teams || [])];
    const c = this.oddsCols.find((x) => x.key === this.oddsSort) || this.oddsCols[1];
    return rows.sort((a, b) => (this.oddsAsc ? c.get(a) - c.get(b) : c.get(b) - c.get(a)) || b.title - a.title);
  },
  oddsSortBy(key) {
    if (this.oddsSort === key) this.oddsAsc = !this.oddsAsc;
    else { this.oddsSort = key; this.oddsAsc = false; }
  },
  oddsBg(key, r) {
    const c = this.oddsCols.find((x) => x.key === key);
    return this.shade(c, r, this.odds?.teams || []);
  },
  // odds as text: avoid showing 0% / 100% for things that are merely very likely
  oddsPct(p, clinched = false, eliminated = false) {
    if (p == null) return '–';
    if (clinched) return '✓';
    if (eliminated || p === 0) return '—';
    if (p < 0.005) return '<1%';
    if (p > 0.995) return '>99%';
    return Math.round(p * 100) + '%';
  },
  // seed grid cell: one-hue sequential ramp, darker = more likely
  seedBg(p) {
    void this.themeTick;
    if (!p || p < 0.005) return '';
    const hex = rampColor(RAMPS.seq[this.dark ? 'dark' : 'light'], 0.15 + 0.85 * p);
    return `background: ${hex}; color: ${inkOn(hex)}`;
  },
  oddsBadge(r) {
    if (r.clinched_bye) return { label: 'Clinched bye', cls: 'up' };
    if (r.clinched_playoffs) return { label: 'Clinched', cls: 'up' };
    if (r.eliminated) return { label: 'Eliminated', cls: 'down' };
    return null;
  },
  // playoff odds week by week (current season) as an SVG polyline, 0-100% on a 100 x 28 box
  oddsSpark(teamId) {
    const h = this.oddsHistory?.[String(this.odds?.season)]?.[String(teamId)];
    if (!h || h.playoff.length < 2) return null;
    const v = h.playoff;
    return v.map((p, i) => `${(i / (v.length - 1)) * 100},${26 - p * 24}`).join(' ');
  },
  get oddsUpdated() { return this.odds ? this.ago(this.odds.updated_at) : ''; },

  // ---------- stakes, trends and rooting (current season)
  oddsFor(teamId) { return this.odds?.teams.find((r) => r.team_id === teamId) || null; },
  // "if you win / if you lose" for this week's game; null when the game is already decided
  stakesOf(teamId) {
    const s = this.oddsFor(teamId)?.stakes;
    return s && s.playoff_w != null && s.playoff_l != null ? s : null;
  },
  stakesLine(teamId) {
    const r = this.oddsFor(teamId), s = this.stakesOf(teamId);
    if (!r) return '';
    if (r.clinched_playoffs || r.eliminated) return r.clinched_playoffs ? 'Playoffs clinched' : 'Eliminated';
    if (!s) return `Playoffs ${this.oddsPct(r.playoff)}`;
    return `Playoffs: win ${this.oddsPct(s.playoff_w)} · lose ${this.oddsPct(s.playoff_l)}`;
  },
  // weekly series from odds_history.json: {after: [week...], playoff: [...], bye: [...], title: [...]}
  oddsSeries(teamId, season = this.season) { return this.oddsHistory?.[String(season)]?.[String(teamId)] || null; },
  // change vs last week's point for the live season
  oddsDelta(teamId, key) {
    const h = this.oddsSeries(teamId, this.odds?.season), r = this.oddsFor(teamId);
    if (!h || !r) return null;
    const i = h.after.findIndex((w) => w === this.odds.mp - 1);
    return i >= 0 ? r[key] - h[key][i] : null;
  },
  rootingFor(teamId, n = 3) {
    return (this.oddsFor(teamId)?.rooting || []).filter((g) => g.playoff_swing + g.title_swing >= 0.005).slice(0, n)
      .map((g) => ({ ...g, other: g.root_for === g.a ? g.b : g.a }));
  },
  oddsChart() {
    void this.themeTick;
    const t = this.T;
    const h = t && this.oddsSeries(t.team_id);
    if (!h || h.after.length < 1) return null;
    const label = (w) => (w === 0 ? 'Start' : `Wk ${w}`);
    return {
      option: {
        ...base(), grid: { ...base().grid, top: 36, right: 16 },
        legend: { ...base().legend, data: ['Playoffs', 'Title'] },
        tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: TOK('--axis') } },
          formatter: (ps) => { const i = ps[0].dataIndex;
            return `<b>${i === 0 && h.after[0] === 0 ? 'Before week 1' : 'After ' + label(h.after[i]).toLowerCase()}</b>`
              + tipRow('Playoffs', fmt.pct(h.playoff[i], 0)) + tipRow('Bye', fmt.pct(h.bye[i], 0)) + tipRow('Title', fmt.pct(h.title[i], 0)); } },
        xAxis: catAxis(h.after.map(label), { boundaryGap: false }),
        yAxis: axis({ type: 'value', min: 0, max: 1, interval: 0.25, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => Math.round(v * 100) + '%' } }),
        series: [
          { type: 'line', name: 'Playoffs', data: h.playoff, symbol: 'circle', symbolSize: 7, showSymbol: h.after.length < 25,
            lineStyle: { width: 2, color: TOK('--s1') }, itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 } },
          { type: 'line', name: 'Title', data: h.title, symbol: 'circle', symbolSize: 7, showSymbol: h.after.length < 25,
            lineStyle: { width: 2, color: TOK('--s2') }, itemStyle: { color: TOK('--s2'), borderColor: TOK('--surface'), borderWidth: 2 } },
        ],
      },
      table: { cols: [{ label: 'Week' }, { label: 'Playoffs', num: 1 }, { label: 'Bye', num: 1 }, { label: 'Title', num: 1 }],
               rows: h.after.map((w, i) => [label(w), fmt.pct(h.playoff[i], 0), fmt.pct(h.bye[i], 0), fmt.pct(h.title[i], 0)]) },
    };
  },
};
