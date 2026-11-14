/* League > Draft & waivers: value above replacement (WAR-style) for draft picks and waiver pickups. */

window.Draft = {
  draftData: null, drTab: 'draft', drOpen: null,

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
  drGradeColor(g) { return TOK({ A: '--good', B: '--s1', C: '--ink2', D: '--s4', F: '--bad' }[g[0]] || '--ink2'); },
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
    const colors = ['--s1', '--s2', '--s3'];
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
};
