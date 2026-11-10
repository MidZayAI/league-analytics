/* Roster projections (team pages) and the trade analyzer (#/trades). */

window.Trades = {
  rosters: null, tradesData: null, rsMode: 'week', trA: '', trB: '', trPickA: [], trPickB: [],

  async loadRosters() {
    const get = (f) => fetch(`data/${f}`, { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    [this.rosters, this.tradesData] = await Promise.all([get('rosters.json'), get('trades.json')]);
  },
  rosterOf(teamId) { return this.isLiveSeason ? this.rosters?.teams?.[teamId] || null : null; },
  // today: betting-line projections for this team's players with a game left today (today.json)
  get teamToday() {
    const t = this.T && this.isLiveSeason ? this.today?.teams?.[this.T.team_id] : null;
    return (t?.players || []).slice().sort((a, b) => (b.fp ?? b.model ?? 0) - (a.fp ?? a.model ?? 0));
  },
  get teamRoster() {
    const r = this.T && this.rosterOf(this.T.team_id);
    if (!r) return null;
    const key = (p) => (this.rsMode === 'week' ? (p.week?.fpg ?? -1) : p.ros);
    return { ...r, players: r.players.slice().sort((a, b) => key(b) - key(a)) };
  },

  // cap-aware weekly points: best rest-of-season games up to the average cap (same as pipeline/rosters.fill)
  capFill(players) {
    let left = this.rosters?.cap_avg || 0, total = 0;
    for (const p of players.slice().sort((a, b) => b.ros - a.ros)) {
      const g = Math.min(left, p.gpw * p.avail);
      total += g * p.ros;
      left -= g;
      if (left <= 0) break;
    }
    return total;
  },
  trTeams() { return Object.entries(this.rosters?.teams || {}).map(([id, t]) => ({ id, name: t.name })); },
  trToggle(side, id) {
    const k = side === 'A' ? 'trPickA' : 'trPickB';
    this[k] = this[k].includes(id) ? this[k].filter((x) => x !== id) : [...this[k], id];
  },
  trLoad(trade) {
    const [a, b] = trade.teams;
    this.trA = String(a.team_id); this.trB = String(b.team_id);
    this.trPickA = a.out.map((p) => p.id); this.trPickB = b.out.map((p) => p.id);
    document.getElementById('trade-builder')?.scrollIntoView({ behavior: 'smooth' });
  },
  // what-if: weekly change from the cap fill, odds change estimated from every team's sensitivity
  get trResult() {
    const R = this.rosters?.teams;
    if (!R || !this.trA || !this.trB || this.trA === this.trB || (!this.trPickA.length && !this.trPickB.length)) return null;
    const A = R[this.trA], B = R[this.trB];
    const outA = A.players.filter((p) => this.trPickA.includes(p.id));
    const outB = B.players.filter((p) => this.trPickB.includes(p.id));
    const afterA = A.players.filter((p) => !this.trPickA.includes(p.id)).concat(outB);
    const afterB = B.players.filter((p) => !this.trPickB.includes(p.id)).concat(outA);
    const d = { [this.trA]: this.capFill(afterA) - this.capFill(A.players), [this.trB]: this.capFill(afterB) - this.capFill(B.players) };
    const shift = this.rosters.shift || 10;
    const odds = (tid, k) => Object.entries(d).reduce((s, [j, dj]) => s + (dj / shift) * (R[j].sens_on?.[tid]?.[k] || 0), 0);
    const side = (tid, ins, outs) => ({
      tid, name: R[tid].name, ins, outs, weekly: d[tid],
      ros: ins.reduce((s, p) => s + p.ros, 0) - outs.reduce((s, p) => s + p.ros, 0),
      playoff: odds(tid, 'playoff'), title: odds(tid, 'title'),
    });
    const sides = [side(this.trA, outB, outA), side(this.trB, outA, outB)];
    const best = sides.slice().sort((x, y) => (y.title + y.playoff / 4) - (x.title + x.playoff / 4))[0];
    return { sides, best };
  },
  // how the trade has played out: cumulative counted points (incoming players + roster-spot pickups)
  tradeCumChart(tr) {
    void this.themeTick;
    const sides = (tr.teams || []).filter((s) => s.so_far?.cum?.length);
    if (sides.length < 2) return null;
    const dates = sides[0].so_far.cum.map((c) => c.date);
    return { option: {
      ...base(), grid: { ...base().grid, top: 36, right: 40 },
      legend: { ...base().legend, data: sides.map((s) => s.name) },
      tooltip: { ...base().tooltip, trigger: 'axis',
        formatter: (ps) => `<b>${fmt.date(dates[ps[0].dataIndex])}</b>` + ps.map((p) => tipRow(p.seriesName, fmt.n(p.value, 0))).join('') },
      xAxis: catAxis(dates.map((d) => fmt.date(d)), { boundaryGap: false }),
      yAxis: axis({ type: 'value' }),
      series: sides.map((s, i) => ({ type: 'line', name: s.name, data: s.so_far.cum.map((c) => c.pts), symbol: 'none',
        lineStyle: { width: 2.5, color: this.tradeColor(i) }, itemStyle: { color: this.tradeColor(i) },
        endLabel: { show: true, color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.n(p.value, 0) } })),
    } };
  },
  tradeOddsChart(tr) {
    void this.themeTick;
    const o = tr.odds_series;
    if (!o || o.mps.length < 2) return null;
    const lbl = o.mps.map((m) => `Wk ${m}`);
    const series = [];
    tr.teams.forEach((s, i) => {
      const t = o.teams[s.team_id];
      if (!t) return;
      series.push({ type: 'line', name: `${s.name} · playoffs`, data: t.playoff, symbolSize: 6,
        lineStyle: { width: 2, color: this.tradeColor(i) }, itemStyle: { color: this.tradeColor(i) },
        markLine: series.length ? undefined : { silent: true, symbol: 'none', label: { formatter: 'Trade', color: TOK('--muted') },
          lineStyle: { color: TOK('--axis'), type: 'dashed' }, data: [{ xAxis: `Wk ${o.trade_mp}` }] } });
      series.push({ type: 'line', name: `${s.name} · title`, data: t.title, symbolSize: 5,
        lineStyle: { width: 1.5, type: 'dashed', color: this.tradeColor(i) }, itemStyle: { color: this.tradeColor(i) } });
    });
    return { option: {
      ...base(), grid: { ...base().grid, top: 56, right: 16 },
      legend: { ...base().legend, data: series.map((x) => x.name) },
      tooltip: { ...base().tooltip, trigger: 'axis',
        formatter: (ps) => `<b>${lbl[ps[0].dataIndex]}</b>` + ps.map((p) => tipRow(p.seriesName, p.value == null ? '–' : fmt.pct(p.value, 0))).join('') },
      xAxis: catAxis(lbl, { boundaryGap: false }),
      yAxis: axis({ type: 'value', min: 0, max: 1, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => Math.round(v * 100) + '%' } }),
      series,
    } };
  },
  // two clearly different series colors (franchise colors can be too close to tell apart)
  tradeColor(i) { return TOK(i === 0 ? '--s1' : '--s2'); },
  tradeScore(s) { return s.so_far ? s.so_far.counted + s.so_far.spot_counted : null; },
  pctPts(x) { return `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(100 * x).toFixed(1)}`; },
};
