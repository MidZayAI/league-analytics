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
  get weekList() {
    if (!this.S) return [];
    const played = new Set(this.S.weeks.filter((w) => w.result).map((w) => w.mp));
    if (this.isLiveSeason) played.add(this.live.mp);
    return this.S.caps.filter((c) => played.has(c.mp))
      .map((c) => ({ mp: c.mp, playoff: c.playoff, live: this.isLiveSeason && c.mp === this.live.mp, dates: `${fmt.date(c.start)} – ${fmt.date(c.end)}` }));
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
  get weekGames() { return this.isLiveWeek ? [] : this.gamesFor(this.weekSel); },
  get weekHeader() {
    const c = this.cap(this.weekSel);
    return c ? { title: `Week ${c.mp}${c.playoff ? ' · Playoffs' : ''}`, dates: `${fmt.date(c.start)} – ${fmt.date(c.end)}`, cap: c.cap } : null;
  },
  matchupHref(id, mp = this.weekSel) { return `#/matchup/${this.season}/${mp}/${id}`; },

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
