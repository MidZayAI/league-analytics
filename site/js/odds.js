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
};
