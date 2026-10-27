/* Injury report: injured NBA players, who benefits while they sit, free-agent pickup ideas. */

window.Injuries = {
  injuries: null, injScope: 'league',

  async loadInjuries() {
    this.injuries = await fetch('data/injuries.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  },
  // "In your league": rostered injured players, or ones whose absence helps someone (rostered or free agent)
  get injPlayers() {
    const P = this.injuries?.players || [];
    return this.injScope === 'all' ? P : P.filter((p) => p.owner != null || p.helps.length);
  },
  ownerName(teamId) { return teamId == null ? null : this.tname(teamId, this.live?.season || this.meta?.current_season); },
  ownerLogo(teamId) { return this.logoOf(teamId, this.live?.season || this.meta?.current_season); },
  injStatus(p) {
    const out = p.status === 'Out';
    const back = p.return ? `back ~${fmt.date(p.return)}` : (p.p_next != null ? `${fmt.pct(p.p_next, 0)} to play next game` : '');
    return { label: out ? 'OUT' : (p.status || '').toUpperCase(), cls: out ? 'down' : 'text-ink2', back };
  },
  minutesText(h) {
    return h.min_in != null && h.min_out != null ? `${Math.round(h.min_in)}→${Math.round(h.min_out)} min` : '';
  },
};
