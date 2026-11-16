/* Matchups > Swings: day-by-day win chances for every matchup on record, comebacks, chokes, clutch. */

const SW_DAY = (d) => new Date(d + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short' });

window.Swings = {
  swingData: null, swSeason: null, swWeek: null, swHist: 'all', swTeam: 'all', swPlayoffs: true, swList: 'clutch',

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

  get swWeeks() {
    const ms = (this.swingData?.matchups || []).filter((m) => m.season === this.swSeason);
    const seen = new Map();
    ms.forEach((m) => { if (!seen.has(m.mp)) seen.set(m.mp, m.playoff); });
    return [...seen].map(([mp, playoff]) => ({ mp, playoff }));
  },
  get swWeekGames() {
    return (this.swingData?.matchups || []).filter((m) => m.season === this.swSeason && m.mp === this.swWeek)
      .sort((a, b) => (a.tier === 'WINNERS_BRACKET' ? 0 : 1) - (b.tier === 'WINNERS_BRACKET' ? 0 : 1) || b.swing - a.swing);
  },
  swPickSeason(s) {
    this.swSeason = +s;
    const w = this.swWeeks;
    this.swWeek = w.length ? w[w.length - 1].mp : null;
  },
  swOpen(m) {
    this.swSeason = m.season;
    this.swWeek = m.mp;
    this.$nextTick(() => document.getElementById(`sw-${m.season}-${m.mp}-${m.matchup_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
  },

  // one line per matchup: the story in plain words
  swCaption(m) {
    const H = this.swName(m.home, m.season), A = this.swName(m.away, m.season);
    if (!m.result) return `In progress · ${H} ${fmt.pct(m.series[m.series.length - 1].p, 0)} to win`;
    if (m.result === 'T') return 'Tied';
    const W = this.swName(m.winner, m.season), by = fmt.n(m.margin);
    if (m.winner_sat < 0.5) return `${W} were ${fmt.pct(m.winner_sat, 0)} on Saturday → won by ${by}`;
    if (m.winner_low < 0.5) return `${W} were down to ${fmt.pct(m.winner_low, 0)} → won by ${by}`;
    return `${W} led all week → won by ${by}`;
  },
  // day buttons: chance at the start of each day + what happened that day
  swDays(m) {
    return m.series.map((x, i) => ({ i, label: x.final ? 'Final' : SW_DAY(x.d), p: x.p, why: x.why, final: !!x.final, sat: i === m.sat_i }));
  },
  swWhy(m, side) {
    if (!side) return '';
    const parts = [`${fmt.n(side.pts)} pts`];
    if (side.top) parts.push(`${side.top[0]} ${side.top[1]} (${fmt.signed(side.top[2], 0)})`);
    if (side.low) parts.push(`${side.low[0]} ${side.low[1]} (${fmt.signed(side.low[2], 0)})`);
    if (side.out) parts.push(`${side.out.join(', ')} sat`);
    return parts.join(' · ');
  },

  // win-chance line, colored by whoever is ahead; this season's weeks use every live refresh
  // top: the team drawn at the top of the chart (default home)
  swChart(m, top) {
    void this.themeTick;
    if (!m) return null;
    const flip = top != null && top === m.away;
    const [ht, at] = flip ? [m.away, m.home] : [m.home, m.away];
    const f = (p) => (flip ? 1 - p : p);
    const H = this.swName(ht, m.season), A = this.swName(at, m.season);
    const hc = this.swColor(ht, m.season), ac = this.swColor(at, m.season);
    const live = (m.live || []).length >= 5;
    const ts = (d) => new Date(d + 'T00:00:00').getTime();
    let data, x;
    if (live) {
      data = m.live.map(([t, p]) => [t * 1000, f(p)]);
      const fin = m.series[m.series.length - 1];
      if (fin.final) data.push([ts(fin.d), f(fin.p)]);
      x = axis({ type: 'time', splitLine: { show: false }, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => new Date(v).toLocaleDateString('en-US', { weekday: 'short' }) } });
    } else {
      data = m.series.map((s, i) => [i, f(s.p)]);
      x = catAxis(this.swDays(m).map((d) => d.label), { boundaryGap: false });
    }
    const satX = live ? ts(m.series[m.sat_i]?.d) : m.sat_i;
    const label = (v) => (live ? new Date(v).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : this.swDays(m)[v]?.label);
    return {
      option: {
        ...base(), grid: { ...base().grid, top: 10, right: 12, left: 4, bottom: 2 },
        tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: TOK('--axis') } },
          formatter: (ps) => { const [t, p] = ps[0].value;
            return `<b>${label(t)}</b>` + tipRow(esc(H), fmt.pct(p, 0)) + tipRow(esc(A), fmt.pct(1 - p, 0)); } },
        xAxis: x,
        yAxis: axis({ type: 'value', min: 0, max: 1, interval: 0.5, axisLabel: { color: TOK('--muted'), fontSize: 10, formatter: (v) => (v === 1 ? H.split(' ')[0] : v === 0 ? A.split(' ')[0] : '50%') } }),
        visualMap: { show: false, dimension: 1, pieces: [{ min: 0.5, max: 1, color: hc }, { min: 0, max: 0.5, color: ac }] },
        series: [{ type: 'line', data, smooth: 0.25, symbol: 'circle', symbolSize: live ? 0 : 6, showSymbol: !live, lineStyle: { width: 2.5 },
                   areaStyle: { opacity: 0.12, origin: 0.5 },
                   markLine: { silent: true, symbol: 'none', label: { show: false },
                     data: [{ yAxis: 0.5, lineStyle: { color: TOK('--axis'), type: 'solid', width: 1 } },
                            ...(m.result && satX != null ? [{ xAxis: satX, lineStyle: { color: TOK('--muted'), type: 'dashed', width: 1 } }] : [])] } }],
      },
      table: { cols: [{ label: 'When' }, { label: H, num: 1 }, { label: A, num: 1 }],
               rows: data.map(([t, p]) => [label(t), fmt.pct(p, 0), fmt.pct(1 - p, 0)]) },
    };
  },

  // ---------- history lists (season / team / playoffs filters)
  get swPool() {
    return (this.swingData?.matchups || []).filter((m) => (this.swHist === 'all' || m.season === +this.swHist) && (this.swPlayoffs || !m.playoff));
  },
  // franchises (owners): team ids get reused when a team changes hands, so filter by owner
  swOwner(tid, season) { return this.swingData?.owners?.[season]?.[tid]; },
  get swTeams() {
    const D = this.swingData;
    if (!D) return [];
    const latest = {};
    D.seasons.forEach((s) => Object.entries(D.owners[s] || {}).forEach(([tid, o]) => { latest[o] = this.swName(+tid, s); }));
    return Object.entries(latest).map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  },
  swIs(tid, m) { return this.swOwner(tid, m.season) === this.swTeam; },
  swHas(m) { return this.swTeam === 'all' || this.swIs(m.home, m) || this.swIs(m.away, m); },
  get swLists() {
    const P = this.swPool, T = this.swTeam !== 'all';
    const won = P.filter((m) => m.winner);
    const row = (m, team, title, sub, val, cls) => ({ m, team, title, sub, val, cls, key: `${m.season}-${m.mp}-${m.matchup_id}-${team}` });
    const nm = (t, m) => this.swName(t, m.season);
    const day = (m, i) => (m.series[i]?.final ? 'Final' : SW_DAY(m.series[i].d));
    return {
      clutch: won.filter((m) => m.winner_sat < 0.5 && (!T || this.swIs(m.winner, m))).sort((a, b) => a.winner_sat - b.winner_sat).slice(0, 10)
        .map((m) => row(m, m.winner, `${nm(m.winner, m)} beat ${nm(m.loser, m)}`, `${this.swWeekLabel(m)} · won by ${fmt.n(m.margin)}`, fmt.pct(m.winner_sat, 0), 'up')),
      chokes: won.filter((m) => m.winner_sat < 0.5 && (!T || this.swIs(m.loser, m))).sort((a, b) => a.winner_sat - b.winner_sat).slice(0, 10)
        .map((m) => row(m, m.loser, `${nm(m.loser, m)} lost to ${nm(m.winner, m)}`, `${this.swWeekLabel(m)} · lost by ${fmt.n(m.margin)}`, fmt.pct(1 - m.winner_sat, 0), 'down')),
      comebacks: won.filter((m) => m.winner_low < 0.5 && (!T || this.swIs(m.winner, m))).sort((a, b) => a.winner_low - b.winner_low).slice(0, 10)
        .map((m) => { const ps = m.series.map((s) => (m.winner === m.home ? s.p : 1 - s.p)); const i = ps.indexOf(Math.min(...ps.slice(0, -1)));
          return row(m, m.winner, `${nm(m.winner, m)} beat ${nm(m.loser, m)}`, `${this.swWeekLabel(m)} · lowest ${day(m, i)} · won by ${fmt.n(m.margin)}`, fmt.pct(m.winner_low, 0), 'up'); }),
      wild: P.filter((m) => m.lead_changes && this.swHas(m)).sort((a, b) => b.lead_changes - a.lead_changes || b.swing - a.swing).slice(0, 10)
        .map((m) => row(m, m.winner || m.home, `${nm(m.home, m)} vs ${nm(m.away, m)}`, `${this.swWeekLabel(m)} · ${m.result ? nm(m.winner, m) + ' won by ' + fmt.n(m.margin) : 'in progress'}`, `${m.lead_changes}×`, '')),
      moves: P.filter((m) => !m.series[m.move_i + 1]?.final && this.swHas(m)).sort((a, b) => Math.abs(b.move) - Math.abs(a.move)).slice(0, 10)
        .map((m) => { const up = m.move > 0 ? m.home : m.away, p0 = m.series[m.move_i].p, p1 = m.series[m.move_i + 1].p;
          const f = (p) => fmt.pct(up === m.home ? p : 1 - p, 0);
          return row(m, up, `${nm(up, m)} ${f(p0)} → ${f(p1)}`, `${this.swWeekLabel(m)} · ${day(m, m.move_i)} · vs ${nm(up === m.home ? m.away : m.home, m)}`, fmt.signed(Math.abs(m.move) * 100, 0), 'up'); }),
    };
  },
  get swClutch() {
    const D = this.swingData;
    if (!D) return [];
    const rows = this.swHist === 'all' ? D.clutch : (D.clutch_by_season[this.swHist] || []);
    return rows.filter((r) => r.n);
  },
  swClutchChart() {
    void this.themeTick;
    const R = this.swClutch.slice().sort((a, b) => a.clutch - b.clutch);
    if (!R.length) return null;
    return { option: {
      ...base(), grid: { ...base().grid, top: 8, right: 36 },
      tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps) => { const r = R[ps[0].dataIndex];
          return `<b>${esc(this.swName(r.team_id, r.season))}</b>` + tipRow('Clutch wins', fmt.signed(r.clutch, 1)) + tipRow('Won', fmt.n(r.wins, 1))
            + tipRow('Expected on Saturday', fmt.n(r.exp, 1)) + tipRow('Comeback wins', r.comebacks) + tipRow('Blown leads', r.blown); } },
      yAxis: catAxis(R.map((r) => this.swName(r.team_id, r.season))),
      xAxis: axis({ type: 'value' }),
      series: [{ type: 'bar', barWidth: 14, data: R.map((r) => ({ value: r.clutch, itemStyle: { color: TOK(r.clutch >= 0 ? '--good' : '--bad') } })),
                 label: { show: true, position: 'right', color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.signed(p.value, 1) } }],
    } };
  },
};
