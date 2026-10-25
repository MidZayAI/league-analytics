/* League section: one sortable, rank-shaded table (databallr-style) with a column set per view. */

// dir: +1 higher is better, -1 lower is better, 0 = not shaded
const col = (key, label, fmtFn, dir = 1, info = '', get = null) => ({ key, label, fmt: fmtFn, dir, info, get: get || ((t) => t[key]) });
const perWeek = (t, k) => (t[k] == null ? null : t[k] / Math.max(1, t.w + t.l + t.t));
const signed0 = (v) => fmt.signed(v, 0);

const LEAGUE_COLUMNS = {
  standings: {
    sort: 'seed', asc: true,
    cols: [
      col('seed', 'Seed', (v) => v ?? '–', -1, 'Current playoff seed (ESPN standings order).'),
      col('rec', 'W-L', (v, t) => fmt.rec(t), 1, '', (t) => t.w + 0.5 * t.t),
      col('pf', 'PF', (v) => fmt.n(v), 1, 'Points for (regular season).'),
      col('pa', 'PA', (v) => fmt.n(v), -1, 'Points against: lower = easier schedule.'),
      col('exp_w', 'Exp W', (v) => fmt.n(v, 1), 1, 'Expected wins from all-play: what the weekly scores would earn against everyone.'),
      col('luck', 'Luck', (v) => fmt.signed(v), 1, 'Actual wins minus expected wins.'),
      col('allplay_pct', 'All-play', (v) => fmt.pct(v), 1, 'Share of all teams beaten across every week.'),
      col('efficiency', 'Coach', (v) => fmt.pct(v), 1, 'Coaching Rating: points ÷ best possible lineup under the cap.'),
    ],
  },
  luck: {
    sort: 'luck', asc: false,
    cols: [
      col('rec', 'W-L', (v, t) => fmt.rec(t), 1, '', (t) => t.w + 0.5 * t.t),
      col('exp_w', 'Exp W', (v) => fmt.n(v, 1), 1, 'Expected wins from all-play.'),
      col('luck', 'Luck', (v) => fmt.signed(v), 1, 'Actual wins minus expected wins.'),
      col('pa_pw', 'Opp vs avg', signed0, -1, 'How many points per week opponents scored vs. the league average that week. Higher = ran into hot teams.', (t) => perWeek(t, 'pa_vs_avg')),
      col('bad_beats', 'Bad beats', (v) => v ?? '–', -1, 'Losses with a top-half score that week.'),
      col('lucky_wins', 'Lucky wins', (v) => v ?? '–', 1, 'Wins with a bottom-half score that week.'),
      col('flips', 'H/C flips', (v) => fmt.signed(v, 0), 1, 'Games won minus lost because of hot/cold swings (decided differently at season-average level).', (t) => (t.flips_won ?? 0) - (t.flips_lost ?? 0)),
      col('missed_pts', 'Lost to absences', (v) => fmt.n(v), -1, 'Average points of top-10 players who sat out games their NBA team played.'),
    ],
  },
  coaching: {
    sort: 'efficiency', asc: false,
    cols: [
      col('efficiency', 'Coach', (v) => fmt.pct(v), 1, 'Points ÷ best possible lineup from the same roster under the cap.'),
      col('bench_lost', 'Bench pts', (v) => fmt.n(v), -1, 'Season total of best possible minus actual.'),
      col('coaching_losses', 'Coaching L', (v) => v ?? '–', -1, 'Losses the team\'s own best lineup would have won.'),
      col('lineup', 'Lineup & cap', signed0, 1, 'Expected-points step: games actually used vs. the best use of the cap.'),
      col('moves', 'Moves', signed0, 1, 'Expected points added by pickups and drops.'),
      col('over_cap_weeks', 'Over cap', (v) => v || '–', -1, 'Weeks over the games cap.'),
    ],
  },
  expected: {
    sort: 'x_actual', asc: false,
    cols: [
      col('e0', 'Pregame', (v) => fmt.n(v), 1, 'Season total of pregame expected points.'),
      col('availability', 'Avail', signed0, 1, 'Players playing more or less than expected.'),
      col('moves', 'Moves', signed0, 1, 'Pickups and drops.'),
      col('lineup', 'Lineup', signed0, 1, 'Games used vs. the best use of the cap.'),
      col('breakouts', 'Breakouts', signed0, 1, 'Players better or worse this season than expected going in.'),
      col('hot_cold', 'Hot/cold', signed0, 1, 'Week-to-week swings around each player\'s season level.'),
      col('x_actual', 'Actual', (v) => fmt.n(v), 1, 'Points scored.'),
      col('xrec', 'Exp W-L', (v, t) => `${fmt.n(t.x_w)}-${fmt.n(t.x_l)}`, 1, 'Record with every matchup decided at players\' season averages.', (t) => t.x_w),
    ],
  },
  cap: {
    sort: 'cap_use', asc: false,
    cols: [
      col('gp_used', 'Games', (v, t) => `${fmt.n(v)}/${fmt.n(t.cap_total)}`, 1, 'Games used / games allowed by the weekly caps.'),
      col('cap_use', 'Use %', (v) => fmt.pct(v), 1, '', (t) => (t.cap_total ? t.gp_used / t.cap_total : null)),
      col('cap_deficit', 'Unfillable', (v) => fmt.n(v), -1, 'Cap games the roster couldn\'t use (not enough players with NBA games).'),
      col('opp_cap_deficit', 'Opp unfill.', (v) => fmt.n(v), 1, 'Same for this team\'s opponents (higher = opponents were short).'),
      col('over_cap_weeks', 'Over cap', (v) => v || '–', -1, 'Weeks over the games cap.'),
    ],
  },
};

window.League = {
  leagueSort: null, leagueAsc: false, leagueCharts: false,

  get leagueSpec() { return LEAGUE_COLUMNS[this.page] || LEAGUE_COLUMNS.standings; },
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
    const rows = (this.S?.teams || []).filter((t) => t.w + t.l + t.t > 0);
    return [...rows].sort((a, b) => {
      const va = c.get(a), vb = c.get(b);
      if (va == null) return 1;
      if (vb == null) return -1;
      return asc ? va - vb : vb - va;
    });
  },
  get leagueSortKey() { const s = this.leagueSpec; return this.leagueSort && s.cols.some((c) => c.key === this.leagueSort) ? this.leagueSort : s.sort; },
  get leagueSortAsc() { return this.leagueSort === this.leagueSortKey ? this.leagueAsc : this.leagueSpec.asc; },

  // cell background by league rank: blue = best, red = worst, gray midpoint (diverging pair)
  cellBg(c, t) {
    void this.themeTick;
    if (!c.dir) return '';
    const vals = (this.S?.teams || []).filter((x) => x.w + x.l + x.t > 0).map((x) => c.get(x)).filter((v) => v != null);
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
