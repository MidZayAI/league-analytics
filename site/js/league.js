/* League section: one sortable, rank-shaded table (databallr-style) with a column set per view. */

// dir: +1 higher is better, -1 lower is better, 0 = not shaded
const col = (key, label, fmtFn, dir = 1, info = '', get = null) => ({ key, label, fmt: fmtFn, dir, info, get: get || ((t) => t[key]) });
const perWeek = (t, k) => (t[k] == null ? null : t[k] / Math.max(1, t.w + t.l + t.t));
const signed0 = (v) => fmt.signed(v, 0);

// shading only where higher/lower is clearly better (dir 0 = plain): not seed, not points against
const oddsCell = (k) => (v, t) => {
  if (!t.odds) return '–';
  if (t.odds.eliminated) return '—';
  if ((k === 'playoff' && t.odds.clinched_playoffs) || (k === 'bye' && t.odds.clinched_bye)) return '✓';
  return fmt.pct(v, 0);
};
const LEAGUE_COLUMNS = {
  standings: {
    sort: 'seed', asc: true,
    cols: [
      col('seed', 'Seed', (v) => v ?? '–', 0, 'Current playoff seed (ESPN standings order). Seeds 1–2 get a first-round bye; 1–6 make the playoffs.'),
      col('rec', 'W-L', (v, t) => fmt.rec(t), 1, 'Wins and losses.', (t) => t.w + 0.5 * t.t),
      col('pf', 'Pts for', (v) => fmt.n(v), 1, 'Total points scored (regular season).'),
      col('pa', 'Pts vs', (v) => fmt.n(v), 0, 'Total points opponents scored against this team. Not a skill: just who you happened to face.'),
      col('allplay_pct', 'All-play', (v) => fmt.pct(v), 1, 'If you played every team every week: the share you would have beaten. The best measure of how good the scores were.'),
      col('luck', 'Luck', (v) => fmt.signed(v), 1, 'Wins minus the wins your scores earned in all-play. + means the schedule helped.'),
      col('proj_w', 'Proj W', (v) => (v == null ? '–' : fmt.n(v, 1)), 1, 'Projected final wins from 10,000 simulations of the rest of the season.', (t) => t.odds?.proj_w),
      col('o_playoff', 'Playoffs', oddsCell('playoff'), 1, 'Chance to make the playoffs (top 6).', (t) => t.odds?.playoff),
      col('o_bye', 'Bye', oddsCell('bye'), 1, 'Chance of a top-2 seed and a first-round bye.', (t) => t.odds?.bye),
      col('o_title', 'Title', oddsCell('title'), 1, 'Chance to win the championship.', (t) => t.odds?.title),
    ],
  },
  luck: {
    sort: 'luck', asc: false,
    cols: [
      col('rec', 'W-L', (v, t) => fmt.rec(t), 1, 'Wins and losses.', (t) => t.w + 0.5 * t.t),
      col('exp_w', 'Exp W', (v) => fmt.n(v, 1), 1, 'Wins your weekly scores earned in all-play (against every team, every week).'),
      col('luck', 'Luck', (v) => fmt.signed(v), 1, 'Wins minus expected wins. + means the schedule helped.'),
      col('pa_pw', 'Opp vs avg', signed0, -1, 'Points per week your opponents scored above the league average that week. Higher = ran into hot teams.', (t) => perWeek(t, 'pa_vs_avg')),
      col('bad_beats', 'Bad beats', (v) => v ?? '–', -1, 'Losses with a top-half score that week.'),
      col('lucky_wins', 'Lucky wins', (v) => v ?? '–', 1, 'Wins with a bottom-half score that week.'),
      col('flips', 'H/C flips', (v) => fmt.signed(v, 0), 1, 'Games won minus lost because players ran hot or cold that week (at season-average play the other team wins).', (t) => (t.flips_won ?? 0) - (t.flips_lost ?? 0)),
      col('breakouts', 'Breakouts', signed0, 1, 'Points from players being better (or worse) this season than expected going in.'),
      col('hot_cold', 'Hot/cold', signed0, 1, "Points from week-to-week swings around each player's season level."),
      col('xrec', 'Exp W-L', (v, t) => `${fmt.n(t.x_w)}-${fmt.n(t.x_l)}`, 1, "Record with every matchup decided at players' season averages.", (t) => t.x_w),
      col('missed_pts', 'Lost to absences', (v) => fmt.n(v), -1, 'Points lost to top-10 players sitting out games their NBA team played.'),
    ],
  },
  coach: {
    sort: 'efficiency', asc: false,
    cols: [
      col('efficiency', 'Coach', (v) => fmt.pct(v), 1, 'Coaching rating: points scored divided by the best possible lineup from the same roster under the cap.'),
      col('bench_lost', 'Bench pts', (v) => fmt.n(v), -1, 'Season total of points left on the bench (best possible minus actual).'),
      col('coaching_losses', 'Coaching L', (v) => v ?? '–', -1, "Losses the team's own best lineup would have won."),
      col('lineup', 'Lineup & cap', signed0, 1, 'Points from how the games cap was used vs. the best use of it.'),
      col('moves', 'Moves', signed0, 1, 'Expected points added by pickups and drops.'),
      col('gp_used', 'Games', (v, t) => `${fmt.n(v)}/${fmt.n(t.cap_total)}`, 1, 'Games used / games allowed by the weekly caps.'),
      col('cap_use', 'Cap used', (v) => fmt.pct(v), 1, 'Share of the allowed games actually used.', (t) => (t.cap_total ? t.gp_used / t.cap_total : null)),
      col('cap_deficit', 'Unfillable', (v) => fmt.n(v), -1, "Cap games the roster couldn't use (not enough players with NBA games)."),
      col('opp_cap_deficit', 'Opp unfill.', (v) => fmt.n(v), 0, "Same for this team's opponents (luck, not skill)."),
      col('over_cap_weeks', 'Over cap', (v) => v || '–', -1, 'Weeks over the games cap (against league rules).'),
    ],
  },
};

window.League = {
  leagueSort: null, leagueAsc: false, leagueCharts: false, deepView: 'luck',

  get leagueKey() { return this.page === 'deep' ? this.deepView : 'standings'; },
  get leagueSpec() { return LEAGUE_COLUMNS[this.leagueKey]; },
  get leagueTitle() { return this.page === 'deep' ? 'Deep stats' : 'Standings'; },
  // teams with this season's playoff odds attached (Standings shows them as columns)
  get leagueTeams() {
    const withOdds = this.odds?.season === this.season;
    return (this.S?.teams || []).filter((t) => t.w + t.l + t.t > 0)
      .map((t) => ({ ...t, odds: withOdds ? this.oddsFor(t.team_id) : null }));
  },
  sortBy(key) {
    const spec = this.leagueSpec;
    if ((this.leagueSort || spec.sort) === key) this.leagueAsc = !this.leagueAsc;
    else { this.leagueSort = key; this.leagueAsc = spec.cols.find((c) => c.key === key)?.dir === -1; }
  },
  get leagueRows() {
    const spec = this.leagueSpec;
    const key = this.leagueSort && spec.cols.some((c) => c.key === this.leagueSort) ? this.leagueSort : spec.sort;
    const asc = this.leagueSort === key ? this.leagueAsc : spec.asc;
    const c = spec.cols.find((x) => x.key === key);
    const rows = this.leagueTeams;
    return [...rows].sort((a, b) => {
      const va = c.get(a), vb = c.get(b);
      if (va == null) return 1;
      if (vb == null) return -1;
      return asc ? va - vb : vb - va;
    });
  },
  get leagueSortKey() { const s = this.leagueSpec; return this.leagueSort && s.cols.some((c) => c.key === this.leagueSort) ? this.leagueSort : s.sort; },
  get leagueSortAsc() { return this.leagueSort === this.leagueSortKey ? this.leagueAsc : this.leagueSpec.asc; },

  cellBg(c, t) { return this.shade(c, t, this.leagueTeams); },
  // cell background by rank within `rows`: blue = best, red = worst, gray midpoint (diverging pair)
  shade(c, t, rows) {
    void this.themeTick;
    if (!c.dir) return '';
    const vals = rows.map((x) => c.get(x)).filter((v) => v != null);
    const v = c.get(t);
    if (v == null || vals.length < 2) return '';
    const better = vals.filter((x) => (c.dir > 0 ? x > v + 1e-9 : x < v - 1e-9)).length;
    const worse = vals.filter((x) => (c.dir > 0 ? x < v - 1e-9 : x > v + 1e-9)).length;
    const score = (worse + (vals.length - better - worse - 1) / 2) / (vals.length - 1);  // 0 worst .. 1 best
    const hex = rampColor(RAMPS.div[this.dark ? 'dark' : 'light'], score);
    const strength = Math.round(Math.abs(score - 0.5) * 2 * 55);
    return strength ? `background: color-mix(in srgb, ${hex} ${strength}%, transparent)` : '';
  },
};
