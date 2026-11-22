/* Matchups > Swings: day-by-day win chances for every matchup on record, drama, superlatives, clutch grades. */

const SW_DAY = (d) => new Date(d + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short' });
const SW_SHORT = (name) => (name || '').split(' ')[0].slice(0, 12);

window.Swings = {
  swingData: null, swSeason: null, swWeek: null, swHist: 'all', swTeam: 'all', swPlayoffs: true,

  async loadSwings() {
    if (!this.swingData) {
      this.swingData = await fetch('data/swings.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
      this._swIdx = null;
    }
    const D = this.swingData;
    if (D && this.swSeason == null) {
      // default: the latest week with a finished (or live) matchup
      const ms = D.matchups.filter((m) => m.season === D.current);
      const pool = ms.length ? ms : D.matchups;
      const last = pool[pool.length - 1];
      this.swSeason = last.season;
      this.swWeek = last.mp;
    }
    if (D) await Promise.all(D.seasons.map((s) => this.loadSeason(s)));
  },
  swFind(season, mp, mid) {
    if (!this.swingData) return null;
    if (!this._swIdx) {
      this._swIdx = {};
      this.swingData.matchups.forEach((m) => { this._swIdx[`${m.season}-${m.mp}-${m.matchup_id}`] = m; });
    }
    return this._swIdx[`${season}-${mp}-${mid}`] || null;
  },
  swName(tid, season) { return this.swingData?.team_names?.[season]?.[tid] || this.tname(tid, season); },
  swColor(tid, season) {
    const c = this.teamColor(tid, season);
    return c.startsWith('var(') ? TOK(c.slice(4, -1)) : c;
  },
  swWeekLabel(m) { return `${fmt.seasonLabel(m.season)} · ${m.playoff ? 'Playoffs wk ' : 'Week '}${m.mp}`; },

  // ---------- week view
  get swWeeks() {
    const ms = (this.swingData?.matchups || []).filter((m) => m.season === this.swSeason);
    const seen = new Map();
    ms.forEach((m) => { if (!seen.has(m.mp)) seen.set(m.mp, m.playoff); });
    return [...seen].map(([mp, playoff]) => ({ mp, playoff }));
  },
  get swWeekGames() {
    return (this.swingData?.matchups || []).filter((m) => m.season === this.swSeason && m.mp === this.swWeek)
      .sort((a, b) => (b.drama ?? 0) - (a.drama ?? 0));
  },
  get swFeatured() { return this.swWeekGames[0] || null; },
  get swOthers() { return this.swWeekGames.slice(1); },
  swPickSeason(s) {
    this.swSeason = +s;
    const w = this.swWeeks;
    this.swWeek = w.length ? w[w.length - 1].mp : null;
  },
  // a matchup's day-by-day chart lives on its page in Live
  swOpen(m) { if (m) location.hash = `#/matchup/${m.season}/${m.mp}/${m.matchup_id}`; },
  swDramaLabel(m) {
    if (!m?.result) return 'In progress';
    return m.drama >= 50 ? `More dramatic than ${m.drama}% of matchups ever` : `Drama ${m.drama} / 100`;
  },

  // one line per matchup: the story in plain words
  swCaption(m) {
    const H = this.swName(m.home, m.season);
    if (!m.result) return `In progress · ${H} ${fmt.pct(m.series[m.series.length - 1].p, 0)} to win`;
    if (m.result === 'T') return 'Tied';
    const W = this.swName(m.winner, m.season), by = fmt.n(m.margin);
    if (m.winner_sun != null && m.winner_sun < 0.5) return `${W} were ${fmt.pct(m.winner_sun, 0)} on Sunday morning → won by ${by}`;
    if (m.winner_min < 0.5) return `${W} were down to ${fmt.pct(m.winner_min, 0)} → won by ${by}`;
    return `${W} led all week → won by ${by}`;
  },
  // day strip: the leader's chance at the start of each day, colored by who led
  swDays(m) {
    return m.series.map((x, i) => {
      const lead = x.p >= 0.5 ? m.home : m.away;
      return { i, label: x.final ? 'Final' : SW_DAY(x.d), p: x.p, lp: Math.max(x.p, 1 - x.p), lead, why: x.why, final: !!x.final, sun: i === m.sun_i };
    });
  },
  swWhy(m, side) {
    if (!side) return '';
    const parts = [`${fmt.n(side.pts)} pts`];
    if (side.top) parts.push(`${side.top[0]} ${side.top[1]} (${fmt.signed(side.top[2], 0)})`);
    if (side.low) parts.push(`${side.low[0]} ${side.low[1]} (${fmt.signed(side.low[2], 0)})`);
    if (side.out) parts.push(`${side.out.join(', ')} sat`);
    return parts.join(' · ');
  },

  // win-chance line, colored by whoever is ahead, with the key moments labeled.
  // top: the team drawn at the top (default home). big: the featured chart (fuller labels).
  swChart(m, top, big = false) {
    void this.themeTick;
    if (!m) return null;
    const flip = top != null && top === m.away;
    const [ht, at] = flip ? [m.away, m.home] : [m.home, m.away];
    const f = (p) => (flip ? 1 - p : p);
    const H = this.swName(ht, m.season), A = this.swName(at, m.season);
    const hc = this.swColor(ht, m.season), ac = this.swColor(at, m.season);
    const live = (m.live || []).length >= 5;
    const ts = (d) => new Date(d + 'T00:00:00').getTime();
    const X = (i) => (live ? ts(m.series[i].d) : i);
    let data, x;
    if (live) {
      data = m.live.map(([t, p]) => [t * 1000, f(p)]);
      const fin = m.series[m.series.length - 1];
      if (fin.final) data.push([ts(fin.d), f(fin.p)]);
      // a week in progress ends at the current live forecast (same number as the Live cards)
      const lm = !m.result && this.live?.season === m.season && this.live?.mp === m.mp && this.live.matchups.find((x) => x.matchup_id === m.matchup_id);
      if (lm) {
        const t = Date.parse(this.live.updated_at);
        if (!data.length || t > data[data.length - 1][0]) data.push([t, f(lm.home.win_prob)]);
      }
      x = axis({ type: 'time', splitLine: { show: false }, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => new Date(v).toLocaleDateString('en-US', { weekday: 'short' }) } });
    } else {
      data = m.series.map((s, i) => [i, f(s.p)]);
      x = catAxis(this.swDays(m).map((d) => d.label), { boundaryGap: false });
    }
    const label = (v) => (live ? new Date(v).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : this.swDays(m)[v]?.label);

    // key moments: the biggest one-day move (who drove it) and Sunday morning
    const marks = [];
    const mark = (i, text, color) => {
      const p = f(m.series[i].p);
      marks.push({ coord: [X(i), p], value: text, symbol: 'circle', symbolSize: 9,
        itemStyle: { color, borderColor: TOK('--surface'), borderWidth: 2 },
        label: { show: true, formatter: text, position: p > 0.75 ? 'bottom' : 'top', distance: 6, color: TOK('--ink'), fontSize: big ? 12 : 10.5, fontWeight: 600,
                 backgroundColor: TOK('--surface'), borderColor: TOK('--axis'), borderWidth: 1, borderRadius: 6, padding: [2, 5] } });
    };
    const end = m.move_i + 1;
    if (m.result && Math.abs(m.move) >= 0.15 && m.series[end]) {
      const gainer = m.move > 0 ? m.home : m.away, side = m.series[m.move_i].why?.[gainer === m.home ? 0 : 1];
      const day = SW_DAY(m.series[m.move_i].d);
      const text = big && side?.top ? `${day}: ${side.top[0].split(' ').slice(-1)[0]} ${side.top[1]} (${fmt.signed(side.top[2], 0)})`
        : `${day} ${fmt.signed(Math.abs(m.move) * 100, 0)}`;
      if (end !== m.sun_i) mark(end, text, this.swColor(gainer, m.season));
    }
    if (m.result && m.sun_i != null) {
      const p = m.series[m.sun_i].p, lead = p >= 0.5 ? m.home : m.away;
      mark(m.sun_i, `Sun AM ${big ? SW_SHORT(this.swName(lead, m.season)) + ' ' : ''}${fmt.pct(Math.max(p, 1 - p), 0)}`, this.swColor(lead, m.season));
    }
    return {
      option: {
        ...base(), grid: { ...base().grid, top: big ? 28 : 22, right: 14, left: 4, bottom: 2 },
        tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: TOK('--axis') } },
          formatter: (ps) => { const [t, p] = ps[0].value;
            return `<b>${label(t)}</b>` + tipRow(esc(H), fmt.pct(p, 0)) + tipRow(esc(A), fmt.pct(1 - p, 0)); } },
        xAxis: x,
        yAxis: axis({ type: 'value', min: 0, max: 1, interval: 0.5, axisLabel: { color: TOK('--muted'), fontSize: 10, formatter: (v) => (v === 1 ? SW_SHORT(H) : v === 0 ? SW_SHORT(A) : '50%') } }),
        visualMap: { show: false, dimension: 1, seriesIndex: 0, pieces: [{ min: 0.5, max: 1, color: hc }, { min: 0, max: 0.5, color: ac }] },
        series: [{ type: 'line', data, smooth: 0.25, symbol: 'circle', symbolSize: live ? 0 : (big ? 7 : 5), showSymbol: !live, lineStyle: { width: big ? 3 : 2.5 },
                   areaStyle: { opacity: 0.12, origin: 0.5 },
                   markPoint: { silent: true, data: marks },
                   markLine: { silent: true, symbol: 'none', label: { show: false },
                     data: [{ yAxis: 0.5, lineStyle: { color: TOK('--axis'), type: 'solid', width: 1 } },
                            ...(m.result && m.sun_i != null ? [{ xAxis: X(m.sun_i), lineStyle: { color: TOK('--muted'), type: 'dashed', width: 1 } }] : [])] } }],
      },
      table: { cols: [{ label: 'When' }, { label: H, num: 1 }, { label: A, num: 1 }],
               rows: data.map(([t, p]) => [label(t), fmt.pct(p, 0), fmt.pct(1 - p, 0)]) },
    };
  },

  // ---------- season drama grid: weeks across, that week's matchups (most dramatic first) down
  get swGrid() {
    const D = this.swingData;
    if (!D) return { weeks: [], rows: 0 };
    const weeks = this.swWeeks.map((w) => ({ ...w, games: this.swingData.matchups.filter((m) => m.season === this.swSeason && m.mp === w.mp).sort((a, b) => (b.drama ?? 0) - (a.drama ?? 0)) }));
    return { weeks, rows: Math.max(0, ...weeks.map((w) => w.games.length)) };
  },
  swCellStyle(m) {
    void this.themeTick;
    if (!m) return 'background: transparent';
    if (!m.result) return 'background: var(--wash)';
    const pct = Math.round(6 + (m.drama || 0) * 0.84);  // more color = more drama, in both themes
    return `background: color-mix(in srgb, var(--s1) ${pct}%, var(--wash)); color: ${pct > 55 ? '#fff' : 'var(--ink)'}`;
  },
  // mini line from the winner's point of view (ends at the top), inline SVG
  swSpark(m) {
    if (!m) return '';
    const w = m.winner ?? m.home, ps = m.series.map((s) => (w === m.home ? s.p : 1 - s.p));
    const W = 46, Hh = 24, n = Math.max(1, ps.length - 1);
    const pts = ps.map((p, i) => `${(i / n * W).toFixed(1)},${((1 - p) * (Hh - 4) + 2).toFixed(1)}`).join(' ');
    return `<svg width="${W}" height="${Hh}" viewBox="0 0 ${W} ${Hh}" aria-hidden="true"><line x1="0" x2="${W}" y1="${Hh / 2}" y2="${Hh / 2}" stroke="currentColor" stroke-opacity=".3" stroke-width="1"/><polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
  },
  swCellTitle(m) {
    if (!m) return '';
    return `${this.swName(m.home, m.season)} ${fmt.n(m.home_pts)} – ${fmt.n(m.away_pts)} ${this.swName(m.away, m.season)}` + (m.result ? ` · drama ${m.drama}` : ' · in progress');
  },

  // ---------- history (season / team / playoffs filters)
  get swPool() {
    return (this.swingData?.matchups || []).filter((m) => (this.swHist === 'all' || m.season === +this.swHist) && (this.swPlayoffs || !m.playoff));
  },
  // franchises (owners): team ids get reused when a team changes hands, so filter by owner
  swOwner(tid, season) { return this.swingData?.owners?.[season]?.[tid]; },
  get swTeams() {
    const D = this.swingData;
    if (!D) return [];
    const latest = {};
    D.seasons.forEach((s) => Object.entries(D.owners[s] || {}).forEach(([tid, o]) => { latest[o] = { name: this.swName(+tid, s), tid: +tid, season: s }; }));
    return Object.entries(latest).map(([id, v]) => ({ id, ...v })).sort((a, b) => a.name.localeCompare(b.name));
  },
  swIs(tid, m, owner = this.swTeam) { return this.swOwner(tid, m.season) === owner; },
  swHas(m) { return this.swTeam === 'all' || this.swIs(m.home, m) || this.swIs(m.away, m); },
  get swLists() {
    const P = this.swPool, T = this.swTeam !== 'all';
    const won = P.filter((m) => m.winner);
    const row = (m, team, title, sub, val, cls) => ({ m, team, title, sub, val, cls, key: `${m.season}-${m.mp}-${m.matchup_id}-${team}` });
    const nm = (t, m) => this.swName(t, m.season);
    const mine = (tid, m) => !T || this.swIs(tid, m);
    const N = 8;
    return {
      miracles: won.filter((m) => m.winner_sun != null && m.winner_sun < 0.5 && mine(m.winner, m)).sort((a, b) => a.winner_sun - b.winner_sun).slice(0, N)
        .map((m) => row(m, m.winner, `${nm(m.winner, m)} beat ${nm(m.loser, m)}`, `${this.swWeekLabel(m)} · won by ${fmt.n(m.margin)}` + (m.sunday_hero ? ` · ${m.sunday_hero[0]} ${m.sunday_hero[1]} Sun` : ''), fmt.pct(m.winner_sun, 0), 'up')),
      collapses: won.filter((m) => m.winner_sun != null && m.winner_sun < 0.5 && mine(m.loser, m)).sort((a, b) => a.winner_sun - b.winner_sun).slice(0, N)
        .map((m) => row(m, m.loser, `${nm(m.loser, m)} lost to ${nm(m.winner, m)}`, `${this.swWeekLabel(m)} · lost by ${fmt.n(m.margin)}`, fmt.pct(1 - m.winner_sun, 0), 'down')),
      photo: won.filter((m) => this.swHas(m)).sort((a, b) => a.margin - b.margin).slice(0, N)
        .map((m) => row(m, m.winner, `${nm(m.winner, m)} beat ${nm(m.loser, m)}`, `${this.swWeekLabel(m)} · ${fmt.n(m.home_pts)}–${fmt.n(m.away_pts)}`, `by ${fmt.n(m.margin, m.margin % 1 ? 1 : 0)}`, '')),
      cruise: won.filter((m) => mine(m.winner, m)).sort((a, b) => b.winner_min - a.winner_min || b.margin - a.margin).slice(0, N)
        .map((m) => row(m, m.winner, `${nm(m.winner, m)} over ${nm(m.loser, m)}`, `${this.swWeekLabel(m)} · never below ${fmt.pct(m.winner_min, 0)} · won by ${fmt.n(m.margin)}`, fmt.pct(m.winner_min, 0), 'up')),
      coaster: P.filter((m) => m.result && this.swHas(m)).sort((a, b) => b.total_move - a.total_move).slice(0, N)
        .map((m) => row(m, m.winner || m.home, `${nm(m.home, m)} vs ${nm(m.away, m)}`, `${this.swWeekLabel(m)} · ${m.winner ? nm(m.winner, m) + ' won by ' + fmt.n(m.margin) : 'tied'}`, `${Math.round(m.total_move * 100)} pts`, '')),
      heart: P.filter((m) => m.lead_changes && this.swHas(m)).sort((a, b) => b.lead_changes - a.lead_changes || b.total_move - a.total_move).slice(0, N)
        .map((m) => row(m, m.winner || m.home, `${nm(m.home, m)} vs ${nm(m.away, m)}`, `${this.swWeekLabel(m)} · ${m.result ? (m.winner ? nm(m.winner, m) + ' won by ' + fmt.n(m.margin) : 'tied') : 'in progress'}`, `${m.lead_changes}×`, '')),
    };
  },
  get swListCols() {
    return [
      ['miracles', 'Sunday miracles', 'Lowest win chance on Sunday morning, then won'],
      ['collapses', 'Sunday collapses', 'Highest win chance on Sunday morning, then lost'],
      ['photo', 'Photo finishes', 'Smallest final margins'],
      ['cruise', 'Never in doubt', 'Highest win chance even at its worst point'],
      ['coaster', 'Rollercoasters', 'Most total up-and-down movement in win chance'],
      ['heart', 'Heart attacks', 'Most lead changes (the favorite flipped)'],
    ];
  },
  // per franchise: signature win, most painful loss, Mr. Clutch (best final-day game in a Sunday comeback)
  get swAwards() {
    const P = this.swPool.filter((m) => m.winner && m.winner_sun != null);
    return this.swTeams.filter((t) => this.swTeam === 'all' || t.id === this.swTeam).map((t) => {
      const wins = P.filter((m) => this.swIs(m.winner, m, t.id)), losses = P.filter((m) => this.swIs(m.loser, m, t.id));
      if (!wins.length && !losses.length) return null;
      const sig = wins.slice().sort((a, b) => a.winner_sun - b.winner_sun || a.winner_min - b.winner_min)[0];
      const pain = losses.slice().sort((a, b) => a.winner_sun - b.winner_sun)[0];
      const hero = wins.filter((m) => m.winner_sun <= 0.5 && m.sunday_hero).sort((a, b) => b.sunday_hero[1] - a.sunday_hero[1])[0];
      return { ...t, sig, pain, hero };
    }).filter(Boolean);
  },
  swAwardLine(m, kind) {
    if (!m) return '—';
    const opp = kind === 'pain' ? m.winner : m.loser, s = m.season;
    if (kind === 'sig') return `${fmt.pct(m.winner_sun, 0)} Sun AM vs ${this.swName(opp, s)} · ${this.swWeekLabel(m)}`;
    if (kind === 'pain') return `${fmt.pct(1 - m.winner_sun, 0)} Sun AM vs ${this.swName(opp, s)} · ${this.swWeekLabel(m)}`;
    return `${m.sunday_hero[0]} ${m.sunday_hero[1]} pts (${fmt.signed(m.sunday_hero[2], 0)}) vs ${this.swName(opp, s)} · ${this.swWeekLabel(m)}`;
  },

  // ---------- clutch grades
  get swClutch() {
    const D = this.swingData;
    if (!D) return [];
    const rows = this.swHist === 'all' ? D.clutch : (D.clutch_by_season[this.swHist] || []);
    return rows.filter((r) => r.n);
  },
  get swClutchSoFar() { return this.swHist !== 'all' && +this.swHist === this.swingData?.current; },
  swClutchChart() {
    void this.themeTick;
    const R = this.swClutch.slice().sort((a, b) => a.clutch - b.clutch);
    if (!R.length) return null;
    return { option: {
      ...base(), grid: { ...base().grid, top: 8, right: 36 },
      tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps) => { const r = R[ps[0].dataIndex];
          return `<b>${esc(this.swName(r.team_id, r.season))}</b>` + tipRow('Wins above odds', fmt.signed(r.clutch, 1)) + tipRow('Won', fmt.n(r.wins, 1))
            + tipRow('Expected on Sunday morning', fmt.n(r.exp, 1)) + tipRow('Comeback wins', r.comebacks) + tipRow('Blown leads', r.blown); } },
      yAxis: catAxis(R.map((r) => this.swName(r.team_id, r.season))),
      xAxis: axis({ type: 'value' }),
      series: [{ type: 'bar', barWidth: 14, data: R.map((r) => ({ value: r.clutch, itemStyle: { color: TOK(r.clutch >= 0 ? '--good' : '--bad') } })),
                 label: { show: true, position: 'right', color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.signed(p.value, 1) } }],
    } };
  },
};
