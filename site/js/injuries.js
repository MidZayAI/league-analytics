/* Injury report: injured NBA players, who benefits while they sit, free-agent pickup ideas. */

window.Injuries = {
  injuries: null, injScope: 'league', injOpen: false,

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
  // free agents who gain while an injured teammate sits (best opening per player)
  get injOpenings() {
    const best = {};
    (this.injuries?.players || []).forEach((p) => (p.helps || []).forEach((h) => {
      if (h.owner != null || h.bump < 1.5) return;
      if (!best[h.id] || h.bump > best[h.id].bump) {
        best[h.id] = { id: h.id, name: h.name, bump: h.bump, because: p.name, because_id: p.id, status: this.injStatus(p).label === 'OUT' ? 'out' : (p.status || '').toLowerCase() };
      }
    }));
    return Object.values(best).sort((a, b) => b.bump - a.bump).slice(0, 6);
  },
  minutesText(h) {
    return h.min_in != null && h.min_out != null ? `${Math.round(h.min_in)}→${Math.round(h.min_out)} min` : '';
  },
};
