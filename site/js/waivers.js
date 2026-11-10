/* Waiver wire: best free agents by projected points per game (the weekly games cap makes per-game
   quality what matters), for the games left this week and next week, in league scoring. */

window.Waivers = {
  waivers: null, today: null, wvSort: 'fpg', wvPos: 'All', wvTeam: '', wvOpen: null, wvMode: 'week',

  async loadWaivers() {
    const get = (f) => fetch(`data/${f}`, { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    [this.waivers, this.today] = await Promise.all([get('waivers.json'), get('today.json')]);
  },
  // projected FP per game for the chosen window: both weeks, this week, or next week
  wvFpg(r, key = this.wvSort) { return key === 'fpg' ? r.fpg : r[key]?.fpg; },
  wvSorted(stash) {
    return (this.waivers?.players || [])
      .filter((r) => !!r.stash === stash && (this.wvPos === 'All' || r.pos === this.wvPos))
      .slice().sort((a, b) => (this.wvFpg(b) ?? -1) - (this.wvFpg(a) ?? -1));
  },
  get wvRows() { return this.wvSorted(false); },
  // today: betting-line FP only, free agents whose game hasn't started
  get todayFree() { return (this.today?.free || []).filter((r) => this.wvPos === 'All' || r.pos === this.wvPos); },
  tipTime(iso) { return iso ? new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : ''; },
  // long-term view: rest-of-season FP/g (with upside), short-term boosts stripped out
  get wvLong() {
    return (this.waivers?.longterm || []).filter((r) => r.lt && (this.wvPos === 'All' || r.pos === this.wvPos))
      .slice().sort((a, b) => b.lt.ros - a.lt.ros);
  },
  wvLongUpgrade(r) {
    const w = this.wvTeam ? (this.waivers?.weakest_lt?.[this.wvTeam] || [])[0] : null;
    return w ? { gain: r.lt.ros - w.ros, name: w.name, ros: w.ros } : null;
  },
  // range bar: rest-of-season to upside on a shared 10..50 FP/g scale
  wvBar(v) { return `${Math.max(0, Math.min(100, ((v - 10) / 40) * 100))}%`; },
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
