/* Matchups section: live + past-week scoreboards and the head-to-head screen. */

window.Matchups = {
  _pregame: null,

  get isLiveSeason() { return !!this.live?.active && this.season === this.live.season; },
  get defaultWeek() {
    if (this.isLiveSeason) return this.live.mp;
    const done = (this.S?.weeks || []).filter((w) => w.result).map((w) => w.mp);
    return done.length ? Math.max(...done) : 1;
  },
  get isLiveWeek() { return this.isLiveSeason && this.weekSel === this.live.mp; },
  // next week's preview appears from the Saturday before it starts
  get nextWeek() {
    const n = this.live?.next;
    if (!this.isLiveSeason || !n) return null;
    return new Date().toLocaleDateString('en-CA') >= n.show_from ? n : null;
  },
  get isNextWeek() { return !!this.nextWeek && this.weekSel === this.nextWeek.mp; },
  get weekList() {
    if (!this.S) return [];
    const played = new Set(this.S.weeks.filter((w) => w.result).map((w) => w.mp));
    if (this.isLiveSeason) played.add(this.live.mp);
    if (this.nextWeek) played.add(this.nextWeek.mp);
    return this.S.caps.filter((c) => played.has(c.mp))
      .map((c) => ({ mp: c.mp, playoff: c.playoff, live: this.isLiveSeason && c.mp === this.live.mp,
                     next: !!this.nextWeek && c.mp === this.nextWeek.mp, dates: `${fmt.date(c.start)} – ${fmt.date(c.end)}` }));
  },
  get liveDayLabel() {
    const D = this.live?.days || [];
    return `Day ${D.findIndex((d) => d.sp === this.live?.sp_now) + 1} of ${D.length}`;
  },
  get liveUpdatedShort() {
    return this.live ? new Date(this.live.updated_at).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : '';
  },

  // pregame win chance from the backtest (2022-23 .. last completed season)
  pregameP(season, mp, teamId) {
    if (!this._pregame) {
      this._pregame = {};
      (this.backtest?.pregame_probs || []).forEach((r) => { this._pregame[`${r.season}-${r.mp}-${r.team_id}`] = r.p; });
    }
    return this._pregame[`${season}-${mp}-${teamId}`];
  },

  // past week games: pairs of team-week rows sharing a matchup id, winner first
  gamesFor(mp) {
    const rows = (this.S?.weeks || []).filter((w) => w.mp === mp && w.opp_id != null && w.result);
    const by = {};
    rows.forEach((w) => { (by[w.matchup_id] = by[w.matchup_id] || []).push(w); });
    return Object.entries(by).filter(([, p]) => p.length === 2).map(([id, p]) => {
      const [a, b] = p[0].result === 'L' ? [p[1], p[0]] : p;
      return { id: +id, a, b, playoff: a.playoff, tags: this.tagsFor(a, b) };
    }).sort((x, y) => (x.playoff - y.playoff) || (y.a.pts + y.b.pts) - (x.a.pts + x.b.pts));
  },
  tagsFor(win, lose) {
    const t = [];
    const p = this.pregameP(this.season, win.mp, win.team_id);
    if (p != null && p < 0.35 && win.result === 'W') t.push({ key: 'upset', label: `Upset · ${fmt.pct(p, 0)} pregame` });
    if (lose.bad_beat) t.push({ key: 'badbeat', label: 'Bad beat' });
    if (win.flipped) t.push({ key: 'flipped', label: 'Hot/cold flip' });
    if (lose.coaching_loss) t.push({ key: 'coachloss', label: 'Coaching loss' });
    if (win.over_cap || lose.over_cap) t.push({ key: 'cap', label: '⚠ Over cap' });
    return t;
  },
  get weekGames() { return this.isLiveWeek || this.isNextWeek ? [] : this.gamesFor(this.weekSel); },
  get weekHeader() {
    const c = this.cap(this.weekSel);
    return c ? { title: `Week ${c.mp}${c.playoff ? ' · Playoffs' : ''}`, dates: `${fmt.date(c.start)} – ${fmt.date(c.end)}`, cap: c.cap } : null;
  },
  matchupHref(id, mp = this.weekSel) { return `#/matchup/${this.season}/${mp}/${id}`; },

  // ---------- Live cards
  // this week's games: my matchup pinned first
  get liveCards() {
    const M = this.isLiveWeek ? this.live.matchups : [];
    return M.slice().sort((x, y) => this.hasMine(y) - this.hasMine(x));
  },
  get pastCards() { return this.weekGames.slice().sort((x, y) => this.hasMine(y) - this.hasMine(x)); },
  hasMine(m) {
    const ids = m.home ? [m.home.team_id, m.away.team_id] : [m.a.team_id, m.b.team_id];
    return ids.some((t) => this.isMine(t)) ? 1 : 0;
  },
  // games in progress right now for one side
  liveNow(s) { const now = this.live?.sp_now; return (s?.players || []).filter((p) => p.days.some((d) => d.sp === now && d.state === 'live')).length; },
  liveNowAll(m) { return this.liveNow(m.home) + this.liveNow(m.away); },
  // when the team behind is the favorite, say why: games left under the cap
  favoredWhy(m) {
    const [a, b] = [m.home, m.away];
    const behind = a.pts < b.pts ? a : b.pts < a.pts ? b : null;
    if (!behind || behind.win_prob <= 0.5) return '';
    const ahead = behind === a ? b : a;
    const short = (n) => n.split(' ')[0];
    const g = (s) => Math.round(s.exp_games ?? s.rem_cap);
    if (g(behind) > g(ahead)) {
      return `${short(behind.name)} trails by ${fmt.n(ahead.pts - behind.pts)} but has ~${g(behind)} games left vs ~${g(ahead)}`;
    }
    return `${short(behind.name)} trails by ${fmt.n(ahead.pts - behind.pts)} but projects ${fmt.n(behind.proj - behind.pts)} more pts vs ${fmt.n(ahead.proj - ahead.pts)}`;
  },
  // how much this game moves a team's playoff odds (its own win vs lose odds)
  stakeSwing(teamId) {
    const s = this.stakesOf(teamId);
    return s ? Math.round((s.playoff_w - s.playoff_l) * 100) : null;
  },
  scrollWeekIntoView(el) {
    this.$nextTick(() => el.querySelector('.is-current')?.scrollIntoView({ inline: 'center', block: 'nearest' }));
  },

  // ---------- week breakdown (live): expected -> four plain steps -> tonight -> the scoreboard
  sfScored(s) { const e = s?.expected_so_far; return e ? e.actual + (e.in_progress || 0) : null; },
  sfShort(n) { const p = String(n || '').split(' ').filter((w) => !/^(jr\.?|sr\.?|ii|iii|iv)$/i.test(w)); return p.slice(-1)[0] || n; },
  sfSteps(s) {
    const e = s?.expected_so_far;
    if (!e) return [];
    const L = (x) => this.sfShort(x.name);
    const perf = (e.players || []).filter((p) => p.games).map((p) => ({ ...p, d: p.pts - p.proj }));
    const over = perf.slice().sort((a, b) => b.d - a.d).filter((p) => p.d > 4).slice(0, 2);
    const under = perf.slice().sort((a, b) => a.d - b.d).filter((p) => p.d < -4).slice(0, 2);
    const steps = [
      { key: 'availability', label: 'Who played', info: 'Injuries and rest: week-start players sitting out (or playing more than expected) in games their NBA team played.',
        val: e.availability, pct: e.pct?.availability, kind: 'luck',
        why: (e.missed || []).slice(0, 3).map((m) => `${L(m)} out ${m.games} (${fmt.signed(-m.lost, 0)})`).join(' · ') || 'Everyone expected to play did' },
      { key: 'moves', label: 'Roster moves', info: 'Pickups and drops this week: games added by new players minus games lost by players let go.',
        val: e.moves, pct: (e.added || []).length || (e.dropped || []).length ? e.pct?.moves : null, kind: 'skill',
        why: [...(e.added || []).map((a) => `Added ${L(a)}: ${a.games} g, ${fmt.n(a.pts)} pts counted`), ...(e.dropped || []).map((d) => `Dropped ${L(d)} (${d.games} g left)`)].join(' · ') || 'No moves' },
      { key: 'lineup', label: 'Lineup & cap', info: 'Which games counted vs the best use of the games cap with what was known each morning.',
        val: e.lineup, pct: e.pct?.lineup, kind: 'skill',
        why: (e.benched || []).slice(0, 3).map((b) => `Benched ${L(b)} ${b.games}× (${fmt.n(b.pts)} pts)`).join(' · ') || 'Nobody useful left on the bench' },
      { key: 'perf', label: 'vs projection', info: "Points in the games that counted vs each game's pre-game projection (betting line + Sleeper + our model).",
        val: (e.breakouts || 0) + (e.hot_cold || 0), pct: e.pct?.perf, kind: 'luck',
        why: [...over.map((p) => `${L(p)} ${fmt.signed(p.d, 0)}`), ...under.map((p) => `${L(p)} ${fmt.signed(p.d, 0)}`)].join(' · ') || 'Right on projection' },
    ];
    if (e.in_progress) steps.push({ key: 'tonight', label: 'Still playing', info: 'Points from games in progress right now; they move into the steps above when final.',
      val: e.in_progress, pct: null, kind: 'live', why: e.live_games ? `${e.live_games} game${e.live_games > 1 ? 's' : ''} live` : 'Not final yet' });
    return steps;
  },
  sfPctText(st) {
    if (st.pct == null) return '';
    const n = Math.round(st.pct * 100);
    return st.kind === 'luck' ? `luckier than ${n}% of weeks` : `better than ${n}% of weeks`;
  },
  sfPctColor(p) { return p == null ? 'var(--muted)' : p >= 0.6 ? 'var(--good)' : p <= 0.4 ? 'var(--bad)' : 'var(--ink2)'; },
  sfChart(s) {
    void this.themeTick;
    const e = s?.expected_so_far;
    if (!e) return null;
    const steps = this.sfSteps(s);
    const cats = ['Expected', ...steps.map((x) => x.label), 'Scored'];
    const lift = [], up = [], down = [], ends = [];
    let run = e.e0;
    lift.push(0); up.push(null); down.push(null); ends.push(e.e0);
    steps.forEach((x) => {
      const next = run + x.val;
      lift.push(Math.min(run, next)); ends.push(null);
      if (x.val >= 0) { up.push(x.val); down.push(null); } else { up.push(null); down.push(-x.val); }
      run = next;
    });
    lift.push(0); up.push(null); down.push(null); ends.push(this.sfScored(s));
    const vals = [e.e0, this.sfScored(s), ...lift.filter((v) => v > 0)];
    const lo = Math.floor(Math.min(...vals) * 0.96 / 50) * 50;
    const hidden = { color: 'transparent' };
    return { option: {
      ...base(), grid: { ...base().grid, top: 4, right: 44, left: 4, bottom: 0 },
      tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (ps) => {
        const i = ps[0].dataIndex;
        if (i === 0) return `<b>Expected</b>` + tipRow('Points', fmt.n(e.e0));
        if (i === cats.length - 1) return `<b>Scored</b>` + tipRow('Points', fmt.n(this.sfScored(s)));
        const x = steps[i - 1];
        return `<b>${x.label}</b>` + tipRow('Points', fmt.signed(x.val, 0)) + (x.pct != null ? tipRow('Percentile', Math.round(x.pct * 100)) : ''); } },
      xAxis: axis({ type: 'value', min: lo, axisLabel: { show: false }, splitLine: { show: false } }),
      yAxis: catAxis(cats, { inverse: true, axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
      series: [
        { type: 'bar', stack: 'w', data: lift.map((v, i) => (ends[i] != null ? null : v)), itemStyle: hidden, emphasis: { disabled: true }, barWidth: 14 },
        { type: 'bar', stack: 'w', data: up, itemStyle: { color: TOK('--good'), borderRadius: 3 }, barWidth: 14,
          label: { show: true, position: 'right', color: TOK('--ink2'), fontSize: 11.5, formatter: (p) => (p.value ? fmt.signed(p.value, 0) : '') } },
        { type: 'bar', stack: 'w', data: down, itemStyle: { color: TOK('--bad'), borderRadius: 3 }, barWidth: 14,
          label: { show: true, position: 'right', color: TOK('--ink2'), fontSize: 11.5, formatter: (p) => (p.value ? fmt.signed(-p.value, 0) : '') } },
        { type: 'bar', stack: 'w', data: ends, itemStyle: { color: TOK('--data-2'), borderRadius: 3 }, barWidth: 14,
          label: { show: true, position: 'right', color: TOK('--ink'), fontSize: 12, fontWeight: 700, formatter: (p) => (p.value ? fmt.n(p.value) : '') } },
      ],
    } };
  },

  // ---------- head-to-head
  get h2hLive() { return this.isLiveWeek ? this.live.matchups.find((m) => m.matchup_id === this.weekMid) : null; },
  get h2hPast() { return this.isLiveWeek ? null : this.gamesFor(this.weekSel).find((g) => g.id === this.weekMid); },
  livePlayers(side) {
    const now = this.live?.sp_now;
    return (side?.players || []).map((p) => {
      const counted = p.days.filter((d) => (d.state === 'played' || d.state === 'live') && d.counted);
      const games = p.days.filter((d) => d.state === 'game');
      const today = p.days.find((d) => d.sp === now);
      const wk = counted.reduce((s, d) => s + d.pts, 0);
      // expected for the games he's played: projected average per finished game, prorated for a game in progress
      const exp = counted.reduce((s, d) => s + p.mu * (d.state === 'live' ? d.frac : 1), 0);
      const live = counted.find((d) => d.state === 'live');
      const startsLeft = games.reduce((s, d) => s + (d.start || 0), 0);  // expected starts left
      return {
        ...p,
        wk, exp, gp: counted.length,
        left: games.filter((d) => d.p > 0).length,
        // expected points still to come: rest of a game in progress + upcoming games under the cap
        more: (live?.rem || 0) + p.mu * startsLeft,
        today,
        // can't help this week: nothing counted so far and nothing left to play (out, or done)
        inactive: !counted.length && !games.some((d) => d.p > 0),
      };
    }).sort((a, b) => b.mu - a.mu);
  },
  statusLine(p) {
    // teammate usage bump on his next game, e.g. "+4 · Jalen Williams out"
    const next = p.days.find((d) => d.state === 'game' && d.boost >= 1.5);  // skip trivial bumps
    const parts = [p.pro, this.todayLabel(p), p.more >= 1 ? `~${fmt.n(p.more)} more` : '',
                   next ? `+${fmt.n(next.boost)} · ${next.why}` : ''];
    if (!p.more && p.today?.state !== 'live' && !p.left) parts.push('done for the week');
    return parts.filter(Boolean).join(' · ');
  },
  todayLabel(p) {
    const d = p.today;
    if (!d || d.state === 'none') return '';
    if (d.state === 'live') return `${d.label} · ${fmt.n(d.pts)} pts${d.counted ? '' : ' (bench)'}`;
    if (d.state === 'played') return d.counted ? `today ${fmt.n(d.pts)}` : `today ${fmt.n(d.pts)} (bench)`;
    if (d.state === 'missed') return 'DNP today';
    return d.p === 0 ? 'out today' : `tonight ${fmt.pct(d.start, 0)}`;
  },
};
