/* Waiver wire: best free agents for the games left this week and next week, in league scoring. */

window.Waivers = {
  waivers: null, wvSort: 'total', wvPos: 'All', wvTeam: '', wvOpen: null,

  async loadWaivers() {
    this.waivers = await fetch('data/waivers.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  },
  get wvRows() {
    const key = this.wvSort;
    const pts = (r) => (key === 'total' ? r.total : r[key]?.pts ?? 0);
    return (this.waivers?.players || [])
      .filter((r) => this.wvPos === 'All' || r.pos === this.wvPos)
      .slice().sort((a, b) => pts(b) - pts(a));
  },
  get wvTeams() {
    const s = this.meta?.current_season;
    return (this.seasons[s]?.teams || []).map((t) => ({ id: t.team_id, name: t.name }));
  },
  // the selected team's weakest healthy player over the same games
  get wvWeakest() {
    return this.wvTeam ? (this.waivers?.weakest?.[this.wvTeam] || [])[0] || null : null;
  },
  wvUpgrade(r) {
    const w = this.wvWeakest;
    return w ? r.total - (w.this + w.next) : null;
  },
  wvWeekLabel(w) {
    const x = this.waivers?.weeks?.[w];
    return x ? `${w === 'this' ? 'Rest of week' : 'Week'} ${x.mp}` : '';
  },
  wvInj(r) {
    const t = String(r.inj || r.sleeper_inj || '').toUpperCase().replace(/[- ]/g, '_');
    return { DAY_TO_DAY: 'DTD', INJURY_RESERVE: 'IR', SUSPENSION: 'SUSP', QUESTIONABLE: 'Q', DOUBTFUL: 'D' }[t] || t;
  },
  games(n) { return `${n || 0} game${n === 1 ? '' : 's'}`; },
};
