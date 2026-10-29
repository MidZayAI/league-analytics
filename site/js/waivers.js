/* Waiver wire: best free agents by projected points per game (the weekly games cap makes per-game
   quality what matters), for the games left this week and next week, in league scoring. */

window.Waivers = {
  waivers: null, wvSort: 'fpg', wvPos: 'All', wvTeam: '', wvOpen: null,

  async loadWaivers() {
    this.waivers = await fetch('data/waivers.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  },
  // projected FP per game for the chosen window: both weeks, this week, or next week
  wvFpg(r, key = this.wvSort) { return key === 'fpg' ? r.fpg : r[key]?.fpg; },
  wvSorted(stash) {
    return (this.waivers?.players || [])
      .filter((r) => !!r.stash === stash && (this.wvPos === 'All' || r.pos === this.wvPos))
      .slice().sort((a, b) => (this.wvFpg(b) ?? -1) - (this.wvFpg(a) ?? -1));
  },
  get wvRows() { return this.wvSorted(false); },
  get wvStash() { return this.wvSorted(true); },
  get wvTeams() {
    const s = this.meta?.current_season;
    return (this.seasons[s]?.teams || []).map((t) => ({ id: t.team_id, name: t.name }));
  },
  // the selected team's healthy player with the lowest projected FP per game
  get wvWeakest() {
    return this.wvTeam ? (this.waivers?.weakest?.[this.wvTeam] || [])[0] || null : null;
  },
  wvUpgrade(r) {
    const w = this.wvWeakest;
    const mine = this.wvFpg(r);
    const theirs = w && (this.wvSort === 'fpg' ? w.fpg : w[this.wvSort]);
    return mine != null && theirs != null ? mine - theirs : null;
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
