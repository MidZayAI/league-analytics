/* League > Draft & waivers: value above replacement (WAR-style) for draft picks and waiver pickups. */

window.Draft = {
  draftData: null, drTab: 'draft', drOpen: null, drWTeam: null,

  async loadDraft() {
    if (!this.draftData) {
      this.draftData = await fetch('data/draft.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    }
  },
  get DR() { return this.draftData?.seasons?.[this.season] || null; },
  drName(tid) { return this.DR?.team_names?.[tid] || ''; },
  drPick(pid, tid) { return (this.DR?.picks || []).find((p) => p.player_id === pid && (tid == null || p.team_id === tid)); },
  drPickLine(label, pid, tid) {
    const p = this.drPick(pid, tid);
    return p ? `${label}: ${p.name} #${p.overall} → #${p.rank || '–'} (${p.spots > 0 ? '+' : ''}${p.spots})` : '';
  },
  drGradeColor(g) { return TOK({ A: '--good', B: '--ink', C: '--ink2', D: '--warn', F: '--bad' }[g[0]] || '--ink2'); },
  get drSteals() { return (this.DR?.picks || []).slice().sort((a, b) => b.spots - a.spots).slice(0, 5); },
  get drBusts() { return (this.DR?.picks || []).slice().sort((a, b) => a.spots - b.spots).slice(0, 5); },
  // spots gained vs the league average, stacked by round group (rounds 1-3, 4-7, 8+)
  drChart() {
    void this.themeTick;
    const G = this.DR?.grades;
    if (!G?.length) return null;
    const keys = ['1-3', '4-7', '8+'];
    const avg = Object.fromEntries(keys.map((k) => [k, G.reduce((s, g) => s + g.by_round[k], 0) / G.length]));
    const rows = G.slice().sort((a, b) => a.vs_avg - b.vs_avg);
    const colors = ['--k1', '--k3', '--k5'];
    return { option: {
      ...base(), grid: { ...base().grid, top: 30, right: 24 },
      legend: { ...base().legend, data: keys.map((k) => `Rounds ${k}`) },
      tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps) => `<b>${ps[0].name}</b>` + ps.map((p) => tipRow(p.seriesName, fmt.signed(p.value, 0))).join('')
          + tipRow('Total vs league', fmt.signed(rows[ps[0].dataIndex].vs_avg, 0)) },
      yAxis: catAxis(rows.map((g) => this.drName(g.team_id))),
      xAxis: axis({ type: 'value' }),
      series: keys.map((k, i) => ({ type: 'bar', stack: 'all', name: `Rounds ${k}`, barWidth: 16,
        data: rows.map((g) => Math.round(g.by_round[k] - avg[k])), itemStyle: { color: TOK(colors[i]) } })),
    } };
  },
  drAdd(pid, tid) { return (this.DR?.adds || []).find((a) => a.player_id === pid && (tid == null || a.team_id === tid)); },
  // pick board: rounds x teams in real draft order (round 1 order = columns)
  get drBoard() {
    const D = this.DR;
    if (!D) return [];
    const rows = [];
    for (let r = 1; r <= D.rounds; r++) {
      rows.push({ r, cells: D.order.map((tid) => D.picks.find((p) => p.round === r && p.team_id === tid) || null) });
    }
    return rows;
  },
  get drRounds() {
    const D = this.DR;
    if (!D) return [];
    return Array.from({ length: D.rounds }, (_, i) => ({ r: i + 1, picks: D.picks.filter((p) => p.round === i + 1).sort((a, b) => a.spot - b.spot) }));
  },
  // cell color: value vs slot on a diverging blue (steal) / red (bust) ramp, scaled to the season's spread
  drCell(p) {
    void this.themeTick;
    if (!p) return '';
    const vals = this.DR.picks.map((x) => Math.abs(x.spots));
    const top = vals.sort((a, b) => b - a)[Math.floor(vals.length * 0.1)] || 1;
    const score = 0.5 + Math.max(-0.5, Math.min(0.5, p.spots / top / 2));
    const hex = rampColor(RAMPS.div[this.dark ? 'dark' : 'light'], score);
    const strength = Math.round(Math.abs(score - 0.5) * 2 * 60);
    return strength ? `background: color-mix(in srgb, ${hex} ${strength}%, transparent)` : '';
  },
  drStatus(p) {
    if (!p) return '';
    if (p.status === 'traded') return `traded ${fmt.date(p.left)}`;
    if (p.status === 'dropped') return `dropped ${fmt.date(p.left)}`;
    if (p.status === 'never rostered') return 'never rostered';
    return '';
  },
  // last name without suffixes (Jaren Jackson Jr. -> Jackson)
  drLast(name) {
    const parts = String(name || '').split(' ').filter((w) => !/^(jr\.?|sr\.?|ii|iii|iv)$/i.test(w));
    return parts.slice(-1)[0] || name;
  },
  get drTopAdds() { return (this.DR?.adds || []).slice(0, 15); },
  // pickups that were actually used (counted at least once) and came out furthest below replacement
  get drFlops() { return (this.DR?.adds || []).filter((a) => a.counted_gp > 0).slice().sort((a, b) => a.par - b.par).slice(0, 5); },
  drAddLine(label, pid, tid) {
    const a = this.drAdd(pid, tid);
    return a ? `${label}: ${a.name} ${a.par > 0 ? '+' : ''}${Math.round(a.par)} pts${a.pg_rank ? ` (played like #${a.pg_rank})` : ''}` : '';
  },
  // ---------- per-team pickup report
  drWOpen(tid) {
    this.drWTeam = this.drWTeam === tid ? null : tid;
    if (this.drWTeam != null) this.$nextTick(() => document.getElementById('dr-report')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  },
  get drWRow() { return (this.DR?.waivers || []).find((w) => w.team_id === this.drWTeam) || null; },
  get drTeamAdds() {
    return (this.DR?.adds || []).filter((a) => a.team_id === this.drWTeam)
      .slice().sort((a, b) => (b.added > a.added ? 1 : b.added < a.added ? -1 : b.par - a.par));
  },
  get drGotAway() { return this.DR?.got_away?.[this.drWTeam] || []; },
  drVerdictStyle(v) {
    const c = { 'Big win': '--good', Helped: '--s1', Wash: '--muted', Hurt: '--bad' }[v] || '--muted';
    return `background: color-mix(in srgb, var(${c}) 20%, var(--wash)); color: var(${c === '--muted' ? '--ink2' : c})`;
  },
  drSpan(a) {
    const end = a.dropped ? `dropped ${fmt.date(a.dropped)}` : 'still on roster';
    return a.preseason ? `Pre-season pickup · ${end}` : `Added ${fmt.date(a.added)} · ${end}`;
  },
  // FP/game bar widths (his counted FP/g vs the same-dates replacement), scaled to the bigger of the two
  drBarW(a, which) {
    const top = Math.max(a.counted_fpg || 0, a.base_fpg || 0, 1);
    return `${Math.round(((which === 'me' ? a.counted_fpg : a.base_fpg) || 0) / top * 100)}%`;
  },
  drWeekBarStyle(w, a) {
    const top = Math.max(...a.weeks.map((x) => Math.abs(x.par)), 1);
    const h = Math.max(3, Math.round(Math.abs(w.par) / top * 26));
    return `height: ${h}px; background: var(${w.par >= 0 ? '--good' : '--bad'}); ${w.par >= 0 ? 'margin-top: auto' : 'margin-bottom: auto'}`;
  },
  drWeekTip(w) {
    return `Week ${w.mp}: ${w.counted_gp} counted games, ${fmt.n(w.fp)} pts vs ${fmt.n(w.base, 1)}/g replacement → ${fmt.signed(w.par, 0)}`
      + (w.bench_gp ? ` · ${w.bench_gp} on bench (${fmt.n(w.bench_fp)} pts)` : '')
      + (w.swung === 'won' ? ` · won by ${fmt.n(w.margin)}: he made the difference` : w.swung === 'lost' ? ` · lost by ${fmt.n(-w.margin)}: cost the win` : '');
  },
  drWaiverRunChart() {
    void this.themeTick;
    const tid = this.drWTeam, S = this.DR?.waiver_series?.[tid];
    if (!S?.length) return null;
    const M = (this.DR.waiver_markers?.[tid] || []);
    const at = Object.fromEntries(S);
    return { option: {
      ...base(), grid: { ...base().grid, top: 30, right: 16 },
      tooltip: { ...base().tooltip, trigger: 'axis', formatter: (ps) => { const [dt, v] = ps[0].value, d = dt.slice(0, 10);
        const adds = M.filter((m) => m[0] === d).map((m) => m[1]);
        return `<b>${fmt.date(d)}</b>` + tipRow('Above replacement so far', fmt.signed(v, 0)) + (adds.length ? tipRow('Added', esc(adds.join(', '))) : ''); } },
      xAxis: axis({ type: 'time', splitLine: { show: false }, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => new Date(v).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) } }),
      yAxis: axis({ type: 'value' }),
      series: [{ type: 'line', data: S.map(([d, v]) => [d + 'T12:00:00', v]), symbol: 'none', lineStyle: { width: 2.5, color: TOK('--data') },
        areaStyle: { color: TOK('--data'), opacity: 0.1 },
        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: TOK('--axis'), type: 'solid' }, data: [{ yAxis: 0 }] },
        markPoint: { silent: true, data: M.filter((m) => at[m[0]] != null).map((m) => ({ coord: [m[0] + 'T12:00:00', at[m[0]]], symbol: 'circle', symbolSize: 8,
          itemStyle: { color: TOK('--data'), borderColor: TOK('--surface'), borderWidth: 2 },
          label: { show: true, position: 'top', formatter: this.drLast(m[1]), color: TOK('--ink2'), fontSize: 10 } })) } }],
    } };
  },
  drWaiverChart() {
    void this.themeTick;
    const W = this.DR?.waivers;
    if (!W?.length) return null;
    const rows = W.slice().sort((a, b) => a.vs_avg - b.vs_avg);
    return { option: {
      ...base(), grid: { ...base().grid, top: 8, right: 24 },
      tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps) => { const w = rows[ps[0].dataIndex];
          return `<b>${ps[0].name}</b>` + tipRow('vs league', fmt.signed(w.vs_avg, 0)) + tipRow('Above replacement', fmt.n(w.par, 0))
            + tipRow('Pickups paid off', `${w.hits} / ${w.adds}`); } },
      yAxis: catAxis(rows.map((w) => this.drName(w.team_id))),
      xAxis: axis({ type: 'value' }),
      series: [{ type: 'bar', barWidth: 16, data: rows.map((w) => ({ value: w.vs_avg,
        itemStyle: { color: TOK(w.vs_avg >= 0 ? '--s1' : '--bad') } })) }],
    } };
  },
};
