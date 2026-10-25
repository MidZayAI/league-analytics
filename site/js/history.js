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
