/* Teams > Power: power rankings (now / playoff push, with coaching / roster only) and management grades. */

window.Power = {
  powerData: null, pwView: 'now', pwRoster: false, pwOpen: null,

  async loadPower() {
    if (!this.powerData) {
      this.powerData = await fetch('data/power.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    }
  },
  get pwKey() { return this.pwView === 'playoff' ? 'playoff' : this.pwRoster ? 'power_roster' : 'power'; },
  pwTierOf(t) { return this.pwView === 'playoff' ? t.tier_playoff : this.pwRoster ? t.tier_roster : t.tier; },
  get pwTeams() {
    const k = this.pwKey;
    return (this.powerData?.teams || []).slice().sort((a, b) => a.ranks[k] - b.ranks[k]);
  },
  get pwTiers() {
    const T = this.pwTeams;
    return (this.powerData?.tiers || []).map((name) => ({ name, teams: T.filter((t) => this.pwTierOf(t) === name) })).filter((g) => g.teams.length);
  },
  pwLogo(t) { return this.logoOf(t.team_id, this.powerData?.season); },
  pwRec(t) { return `${t.w ?? 0}-${t.l ?? 0}${t.t ? '-' + t.t : ''}`; },
  // movement: vs a week ago (with-coaching ranking), or vs the with-coaching ranking when roster-only
  pwMove(t) {
    if (this.pwView === 'now' && this.pwRoster) return t.ranks.power - t.ranks.power_roster;
    if (this.pwView === 'now' && t.rank_7d) return t.rank_7d - t.ranks.power;
    if (this.pwView === 'playoff') return t.ranks.power - t.ranks.playoff;
    return 0;
  },
  pwMoveLabel() {
    return this.pwView === 'playoff' ? 'vs now' : this.pwRoster ? 'vs with coaching' : 'vs a week ago';
  },
  pwSpark(t) {
    const pts = (t.trend || []).map((x) => x[1]);
    if (pts.length < 2) return '';
    const n = (this.powerData?.teams || []).length || 8, W = 56, H = 20;
    const xy = pts.map((r, i) => `${(i / (pts.length - 1) * W).toFixed(1)},${((r - 1) / Math.max(n - 1, 1) * (H - 4) + 2).toFixed(1)}`).join(' ');
    return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true"><polyline points="${xy}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
  },
  pwScoreLabel() { return this.pwView === 'playoff' ? 'pts / playoff week' : 'pts / week'; },
  pwPct(x) { return x == null ? '–' : fmt.pct(x, 0); },

  // talent (roster with perfect lineups) vs what they've actually scored: above the line = getting more than the roster's worth
  pwScatter() {
    void this.themeTick;
    const T = (this.powerData?.teams || []).filter((t) => t.results != null);
    if (!T.length) return null;
    const xs = T.map((t) => t.talent), ys = T.map((t) => t.results);
    const lo = Math.floor((Math.min(...xs, ...ys) - 15) / 10) * 10, hi = Math.ceil((Math.max(...xs, ...ys) + 15) / 10) * 10;
    return { option: {
      ...base(), grid: { ...base().grid, top: 16, right: 20, bottom: 26, left: 8 },
      tooltip: { ...base().tooltip, trigger: 'item', formatter: (p) => { const t = T[p.dataIndex];
        return `<b>${esc(t.name)}</b>` + tipRow('Roster talent', fmt.n(t.talent) + '/wk') + tipRow('Actually scored', fmt.n(t.results) + '/wk')
          + tipRow('Best possible lineups', fmt.n(t.optimal) + '/wk'); } },
      xAxis: axis({ type: 'value', min: lo, max: hi, name: 'Roster talent (pts/wk)', nameLocation: 'middle', nameGap: 22, nameTextStyle: { color: TOK('--muted'), fontSize: 11 } }),
      yAxis: axis({ type: 'value', min: lo, max: hi, name: 'Scored', nameTextStyle: { color: TOK('--muted'), fontSize: 11 } }),
      series: [{ type: 'scatter', symbolSize: 30, data: T.map((t) => ({ value: [t.talent, t.results], symbol: `image://${this.pwLogo(t)}` })),
        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: TOK('--axis'), type: 'dashed' }, data: [[{ coord: [lo, lo] }, { coord: [hi, hi] }]] } }],
    } };
  },
  pwMgmtChips(t) {
    const m = t.mgmt, p = m.pct || {};
    return [
      ['Lineups', m.efficiency == null ? '–' : fmt.pct(m.efficiency, 0) + ' of best', p.efficiency],
      ['Bad starts', m.bad_starts.n ? `${m.bad_starts.n} (${fmt.n(m.bad_starts.cost)} pts)` : 'None', p.bad_starts],
      ['Waivers', m.waiver_par == null ? '–' : `${fmt.signed(m.waiver_par, 0)}/wk`, p.waivers],
      ['Dead spots', m.dead_spots.length ? `${m.dead_spots.length} (${fmt.n(m.dead_spots.reduce((s, d) => s + d.gain, 0))}/wk)` : 'None', p.dead_spots],
    ];
  },
  pwChipStyle(pct) {
    if (pct == null) return 'background: var(--wash)';
    const c = pct >= 0.6 ? '--good' : pct <= 0.35 ? '--bad' : '--s1';
    return `background: color-mix(in srgb, var(${c}) ${pct >= 0.6 || pct <= 0.35 ? 18 : 8}%, var(--wash))`;
  },
};
