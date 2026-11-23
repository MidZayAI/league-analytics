/* History section: trophies, the all-time franchise table (rank-shaded), timelines, H2H, records. */

const HISTORY_COLS = [
  col('seasons', 'Seasons', (v) => v, 0),
  col('rec', 'W-L', (v, a) => fmt.rec(a), 1, 'Regular-season record.', (a) => a.win_pct),
  col('win_pct', 'Win %', (v) => fmt.pct(v), 1),
  col('titles', '🏆', (v) => v || '–', 1, 'Championships.'),
  col('finals', 'Finals', (v) => v || '–', 1, 'Championship-game appearances.', (a) => a.titles + a.runner_ups),
  col('playoff_apps', 'Playoffs', (v) => v, 1, 'Playoff appearances.'),
  col('avg_finish', 'Avg finish', (v) => fmt.n(v, 1), -1, 'Average final placement.'),
  col('allplay_pct', 'All-play', (v) => fmt.pct(v), 1, 'Share of all teams beaten across every week.'),
  col('luck', 'Luck', (v) => fmt.signed(v), 1, 'Career wins above or below what the weekly scores earned.'),
  col('efficiency', 'Coach', (v) => fmt.pct(v), 1, 'Career Coaching Rating.'),
];

const RECORD_CARDS = [
  { key: 'highest_score', title: 'Highest score', val: (r) => fmt.n(r.pts) },
  { key: 'lowest_score', title: 'Lowest score', val: (r) => fmt.n(r.pts) },
  { key: 'biggest_blowout', title: 'Biggest blowout', val: (r) => '+' + fmt.n(r.margin) },
  { key: 'closest_game', title: 'Closest game', val: (r) => '+' + fmt.n(r.margin) },
  { key: 'highest_losing_score', title: 'Highest losing score', val: (r) => fmt.n(r.pts) },
  { key: 'most_bench_points', title: 'Most left on the bench', val: (r) => fmt.n(r.bench_lost) },
];

window.History = {
  historyCols: HISTORY_COLS, recordCards: RECORD_CARDS,
  histSort: 'titles', histAsc: false,

  get allTimeRows() {
    const c = HISTORY_COLS.find((x) => x.key === this.histSort) || HISTORY_COLS[1];
    return [...this.allTime].sort((a, b) => {
      const d = this.histAsc ? c.get(a) - c.get(b) : c.get(b) - c.get(a);
      return d || b.win_pct - a.win_pct;
    });
  },
  histSortBy(key) {
    if (this.histSort === key) this.histAsc = !this.histAsc;
    else { this.histSort = key; this.histAsc = HISTORY_COLS.find((c) => c.key === key)?.dir === -1; }
  },
  histBg(c, a) { return this.shade(c, a, this.allTime); },
  ownerLogo(ownerId) { return this.profile(ownerId).logo || SILHOUETTE; },
  // record-book entries carry owner ids (franchises), so identity comes from the owner profile
  recordRows(key) { return (this.history?.records?.[key] || []).slice(0, 5); },
};

/* History > Luck & skill ranks: every team-season / team-week by breakdown percentile. */
const RANK_COLS = [
  { key: 'who', label: 'Who played', raw: (r) => r.availability, info: 'Injuries and rest: points gained or lost from players sitting out (or playing more than expected). Luck.' },
  { key: 'moves', label: 'Roster moves', raw: (r) => r.moves, info: 'Points from pickups and drops. A decision.' },
  { key: 'lineup', label: 'Lineup & cap', raw: (r) => r.lineup, info: 'Points from which games counted vs the best use of the games cap. A decision.' },
  { key: 'perf', label: 'vs projection', raw: (r) => r.breakouts + r.hot_cold, info: 'Points in the games that counted vs what those players were projected to score. Luck (plus breakouts).' },
  { key: 'luck', label: 'Luck', raw: (r) => r.availability + r.breakouts + r.hot_cold, info: 'Who played + vs projection, per game.', strong: true },
  { key: 'decisions', label: 'Decisions', raw: (r) => r.moves + r.lineup, info: 'Roster moves + lineup & cap, per game.', strong: true },
];
window.Ranks = {
  ranksData: null, rkKind: 'seasons', rkMode: 'all', rkSort: 'luck', rkAsc: false, rkTeam: 'all', rkSeason: 'all', rankCols: RANK_COLS,

  async loadRanks() {
    if (!this.ranksData) this.ranksData = await fetch('data/ranks.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  },
  get rkRows() {
    const R = (this.ranksData?.[this.rkKind] || []).filter((r) => (this.rkTeam === 'all' || r.owner_id === this.rkTeam) && (this.rkSeason === 'all' || r.season === +this.rkSeason));
    const k = this.rkSort, m = this.rkMode;
    return R.slice().sort((a, b) => (this.rkAsc ? 1 : -1) * ((a[m][k] ?? -1) - (b[m][k] ?? -1)));
  },
  get rkShown() { return this.rkRows.slice(0, this.rkKind === 'weeks' ? 100 : 200); },
  get rkSeasons() { return [...new Set((this.ranksData?.seasons || []).map((r) => r.season))].sort((a, b) => b - a); },
  get rkTeams() {
    const by = {};
    (this.ranksData?.seasons || []).slice().sort((a, b) => a.season - b.season).forEach((r) => { by[r.owner_id] = r.name; });
    return Object.entries(by).map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  },
  rkSortBy(k) { if (this.rkSort === k) this.rkAsc = !this.rkAsc; else { this.rkSort = k; this.rkAsc = false; } },
  rkPreset(k, asc = false) { this.rkSort = k; this.rkAsc = asc; },
  rkCell(p) {
    void this.themeTick;
    if (p == null) return '';
    const hex = rampColor(RAMPS.div[this.dark ? 'dark' : 'light'], p);
    const strength = Math.round(Math.abs(p - 0.5) * 2 * 55);
    return strength ? `background: color-mix(in srgb, ${hex} ${strength}%, transparent)` : '';
  },
  rkFinish(r) {
    if (this.rkKind === 'weeks') return `Wk ${r.mp} · ${r.result} ${fmt.n(r.pts)}–${fmt.n(r.opp_pts)} vs ${this.tname(r.opp_id, r.season) || ''}`;
    const rec = `${r.w}-${r.l}${r.t ? '-' + r.t : ''}`;
    if (r.champion) return `${rec} · 🏆 Champion`;
    if (r.runner_up) return `${rec} · Runner-up`;
    if (r.rank_final) return `${rec} · Finished ${fmt.ord(r.rank_final)}`;
    return `${rec}${r.seed ? ' · Seed ' + r.seed : ''}${r.complete ? '' : ' · in progress'}`;
  },
  rkHref(r) {
    return this.rkKind === 'weeks' ? `#/matchup/${r.season}/${r.mp}/${r.matchup_id}` : `#/team/${encodeURIComponent(r.owner_id)}`;
  },
  async rkOpen(r) {
    if (this.rkKind === 'seasons' && r.season !== this.season) await this.selectSeason(r.season);
    location.hash = this.rkHref(r);
  },
};
