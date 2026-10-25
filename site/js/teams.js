/* Teams section: franchise cards and the per-team page. */

// Metrics shown as rank chips on a team page (higher is better for all of them)
const TEAM_METRICS = [
  { key: 'luck', label: 'Luck', fmt: (v) => fmt.signed(v) + ' W', info: 'Wins above or below what the team\'s weekly scores earned against the whole league (all-play).' },
  { key: 'efficiency', label: 'Coaching', fmt: (v) => fmt.pct(v), info: 'Points scored ÷ the best lineup possible from the same roster under the cap.' },
  { key: 'allplay_pct', label: 'All-play', fmt: (v) => fmt.pct(v), info: 'Share of all teams beaten across every week: the record if you played everyone every week.' },
  { key: 'breakouts', label: 'Breakouts', fmt: (v) => fmt.signed(v, 0), info: 'Points from players who were better or worse this season than expected going in.' },
  { key: 'moves', label: 'Roster moves', fmt: (v) => fmt.signed(v, 0), info: 'Expected points added by pickups and drops during the week.' },
  { key: 'availability', label: 'Availability', fmt: (v) => fmt.signed(v, 0), info: 'Expected points gained or lost from players playing more or less than expected (injuries, rest).' },
  { key: 'cap_use', label: 'Cap use', fmt: (v) => fmt.pct(v), info: 'Games used ÷ games allowed by the weekly caps.' },
  { key: 'hot_cold', label: 'Hot/cold', fmt: (v) => fmt.signed(v, 0), info: 'Week-to-week swings around each player\'s own season average (near zero over a season; timing matters).' },
];

window.Teams = {
  teamOwner: null,
  teamMetrics: TEAM_METRICS,

  get teamCards() {
    return this.teams.map((t) => ({ ...t, spark: this.sparkFor(t.team_id) }));
  },
  // weekly score vs the league average, as an SVG polyline (100 x 28 viewBox)
  sparkFor(teamId) {
    const W = this.regWeeks.filter((w) => w.team_id === teamId).sort((a, b) => a.mp - b.mp);
    if (W.length < 2) return null;
    const d = W.map((w) => w.pts - w.week_avg);
    const m = Math.max(1, ...d.map(Math.abs));
    const pts = d.map((v, i) => `${(i / (d.length - 1)) * 100},${14 - (v / m) * 12}`).join(' ');
    return { pts, last: d[d.length - 1] };
  },
  teamHref(ownerId) { return `#/team/${encodeURIComponent(ownerId)}`; },

  get T() { return this.S?.teams.find((t) => t.owner_id === this.teamOwner); },
  get teamRanks() {
    const T = this.S?.teams || [], me = this.T;
    if (!me) return [];
    const val = (t, k) => (k === 'cap_use' ? (t.cap_total ? t.gp_used / t.cap_total : null) : t[k]);
    return TEAM_METRICS.map((m) => {
      const v = val(me, m.key);
      const all = T.map((t) => val(t, m.key)).filter((x) => x != null);
      return { ...m, value: v, rank: v == null ? null : 1 + all.filter((x) => x > v + 1e-9).length, of: all.length };
    }).filter((m) => m.value != null);
  },
  rankClass(r) { return r.rank <= 2 ? 'up' : r.rank >= r.of - 1 ? 'down' : 'text-ink2'; },

  get teamLive() {
    const me = this.T;
    if (!me || !this.isLiveSeason) return null;
    return this.live.matchups.find((m) => m.home.team_id === me.team_id || m.away.team_id === me.team_id);
  },
  get gameLog() {
    const me = this.T;
    if (!me) return [];
    return this.S.weeks.filter((w) => w.team_id === me.team_id && w.result && w.opp_id != null).sort((a, b) => b.mp - a.mp).map((w) => {
      const o = this.S.weeks.find((x) => x.mp === w.mp && x.team_id === w.opp_id);
      const tags = o ? (w.result === 'W' ? this.tagsFor(w, o) : this.tagsFor(o, w)) : [];
      return { ...w, tags };
    });
  },
  get teamPlayers() {
    const me = this.T;
    return me ? (this.S.players_expected || []).filter((p) => p.team_id === me.team_id).sort((a, b) => b.pts - a.pts) : [];
  },
  seasonWaterfall() {
    const t = this.T;
    if (!t || t.e0 == null) return null;
    return this.waterfallChartFor({ e0: t.e0, availability: t.availability, moves: t.moves, lineup: t.lineup,
                                    breakouts: t.breakouts, hot_cold: t.hot_cold, actual: t.x_actual });
  },
};
