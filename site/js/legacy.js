/* Section builders from the original single-page layout (stats pages). Kept intact while the
   redesign replaces sections one at a time; mixed into the app in js/app.js. */
window.Legacy = {
    // ---------- stat tiles
    get tiles() {
      const T = this.S?.teams.filter((t) => t.w + t.l + t.t > 0) || [];
      if (!T.length) return [];
      const by = (k, dir = -1) => [...T].sort((a, b) => dir * ((a[k] ?? -1e9) - (b[k] ?? -1e9)))[0];
      const best = [...T].sort((a, b) => b.w - a.w || b.pf - a.pf)[0];
      const pf = by('pf'), lucky = by('luck'), unlucky = by('luck', 1), coach = by('efficiency');
      return [
        { label: 'Best record', value: fmt.rec(best), sub: best.name },
        { label: 'Most points', value: fmt.n(pf.pf), sub: pf.name },
        { label: 'Luckiest', value: fmt.signed(lucky.luck) + ' W', sub: lucky.name },
        { label: 'Unluckiest', value: fmt.signed(unlucky.luck) + ' W', sub: unlucky.name },
        { label: 'Top coach', value: fmt.pct(coach.efficiency), sub: coach.name },
      ];
    },

    // ---------- Standings
    winsChart() {
      void this.themeTick;
      const T = [...(this.S?.teams || [])].sort((a, b) => a.w - b.w || a.exp_w - b.exp_w);
      return {
        option: {
          ...base(),
          legend: { ...base().legend, data: ['Actual wins', 'Expected wins'] },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const t = T[ps[0].dataIndex]; return `<b>${esc(t.name)}</b>` + tipRow('Actual', fmt.rec(t)) + tipRow('Expected', fmt.n(t.exp_w, 1)) + tipRow('Luck', fmt.signed(t.luck) + ' wins'); } },
          xAxis: axis({ type: 'value', minInterval: 1 }),
          yAxis: catAxis(T.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
          series: [
            { type: 'custom', silent: true, tooltip: { show: false }, data: T.map((t, i) => [t.w, t.exp_w, i]), encode: { x: [0, 1], y: 2 },
              renderItem: (p, api) => { const a = api.coord([api.value(0), api.value(2)]), b = api.coord([api.value(1), api.value(2)]);
                return { type: 'line', shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style: { stroke: TOK('--axis'), lineWidth: 2 } }; } },
            { type: 'scatter', name: 'Expected wins', data: T.map((t, i) => [t.exp_w, i]), symbolSize: 12,
              itemStyle: { color: TOK('--surface'), borderColor: TOK('--muted'), borderWidth: 2 } },
            { type: 'scatter', name: 'Actual wins', data: T.map((t, i) => [t.w, i]), symbolSize: 12,
              itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 } },
          ],
        },
        table: { cols: [{ label: 'Team' }, { label: 'Actual W', num: 1 }, { label: 'Expected W', num: 1 }, { label: 'Luck', num: 1 }],
                 rows: [...T].reverse().map((t) => [t.name, t.w, fmt.n(t.exp_w, 1), fmt.signed(t.luck)]) },
      };
    },
    scoresChart() {
      void this.themeTick;
      const W = (this.S?.weeks || []).filter((w) => w.team_id === this.teamSel && w.result).sort((a, b) => a.mp - b.mp);
      const name = this.tname(this.teamSel);
      return {
        option: {
          ...base(),
          legend: { ...base().legend, data: [name, 'League average'] },
          grid: { ...base().grid, top: 52, right: 16 },  // room for a two-row legend
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: TOK('--axis') } },
            formatter: (ps) => { const w = W[ps[0].dataIndex];
              return `<b>${this.wk(w.mp)}</b> <span style="color:${TOK('--muted')}">${this.wkDates(w.mp)}${w.playoff ? ' · Playoffs' : ''}</span>`
                + tipRow(esc(name), fmt.n(w.pts)) + tipRow('vs ' + esc(this.tname(w.opp_id)), fmt.n(w.opp_pts))
                + tipRow('Result', `${w.result} (${fmt.signed(w.margin, 0)})`) + tipRow('Weekly rank', `${w.rank} of ${w.n_others + 1}`)
                + tipRow('League avg', fmt.n(w.week_avg)); } },
          xAxis: catAxis(W.map((w) => this.wk(w.mp)), { boundaryGap: false }),
          yAxis: axis({ type: 'value', scale: true }),
          series: [
            { type: 'line', name: 'League average', data: W.map((w) => w.week_avg), symbol: 'none',
              lineStyle: { width: 2, color: TOK('--muted') }, itemStyle: { color: TOK('--muted') } },
            { type: 'line', name, data: W.map((w) => w.pts), symbol: 'circle', symbolSize: 8,
              lineStyle: { width: 2, color: TOK('--s1') }, itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 } },
          ],
        },
        table: { cols: [{ label: 'Week' }, { label: 'Score', num: 1 }, { label: 'Opponent' }, { label: 'Opp score', num: 1 }, { label: 'Result' }, { label: 'League avg', num: 1 }],
                 rows: W.map((w) => [this.wk(w.mp), fmt.n(w.pts), this.tname(w.opp_id), fmt.n(w.opp_pts), w.result, fmt.n(w.week_avg)]) },
      };
    },

    // ---------- Luck Lab
    divBars(T, key, { label, invert = false, digits = 1, suffix = '' }) {
      const [neg, , pos] = RAMPS.div[this.dark ? 'dark' : 'light'];
      const D = [...T].sort((a, b) => a[key] - b[key]);
      return {
        ...base(),
        grid: { ...base().grid, top: 8, left: 8, right: 48 },
        tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
          formatter: (ps) => { const t = D[ps[0].dataIndex]; return `<b>${esc(t.name)}</b>` + tipRow(label, fmt.signed(t[key], digits) + suffix); } },
        // pad both ends so the outside value labels never collide with the team names
        xAxis: { ...labeledValueAxis(), min: (v) => Math.min(0, v.min * 1.35), max: (v) => Math.max(0, v.max * 1.2) },
        yAxis: catAxis(D.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
        series: [{
          type: 'bar', barWidth: 14,
          data: D.map((t) => { const v = t[key]; const good = invert ? v < 0 : v > 0;
            return { value: v, itemStyle: { color: good ? pos : neg, borderRadius: v >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] },
                     label: { position: v < 0 ? 'left' : 'right' } }; }),
          label: { show: true, color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.signed(p.value, digits) },
        }],
      };
    },
    luckChart() {
      void this.themeTick;
      const T = this.S?.teams || [];
      return {
        option: this.divBars(T, 'luck', { label: 'Luck', suffix: ' wins' }),
        table: { cols: [{ label: 'Team' }, { label: 'Record' }, { label: 'Expected W', num: 1 }, { label: 'Luck', num: 1 }, { label: 'Bad beats', num: 1 }, { label: 'Lucky wins', num: 1 }],
                 rows: [...T].sort((a, b) => b.luck - a.luck).map((t) => [t.name, fmt.rec(t), fmt.n(t.exp_w, 1), fmt.signed(t.luck), t.bad_beats, t.lucky_wins]) },
      };
    },
    paChart() {
      void this.themeTick;
      const T = (this.S?.teams || []).map((t) => ({ ...t, pa_pg: t.pa_vs_avg / Math.max(1, t.w + t.l + t.t) }));
      return {
        option: this.divBars(T, 'pa_pg', { label: 'Opponents vs avg', invert: true, digits: 0, suffix: ' pts/wk' }),
        table: { cols: [{ label: 'Team' }, { label: 'Points against', num: 1 }, { label: 'Vs avg opponent, per week', num: 1 }],
                 rows: [...T].sort((a, b) => b.pa_pg - a.pa_pg).map((t) => [t.name, fmt.n(t.pa), fmt.signed(t.pa_pg, 0)]) },
      };
    },
    heatmap({ xs, ys, cell, ramp, domain, label, tooltip, highlight }) {
      const stops = RAMPS[ramp][this.dark ? 'dark' : 'light'];
      const [lo, hi] = domain;
      const data = [];
      ys.forEach((y, yi) => xs.forEach((x, xi) => {
        const c = cell(x, y);
        if (c == null) return;
        const fill = rampColor(stops, hi === lo ? 0.5 : (c.v - lo) / (hi - lo));
        data.push({ value: [xi, yi, c.v], raw: c,
          itemStyle: { color: fill, borderColor: highlight?.(x, y) ? TOK('--ink') : TOK('--surface'), borderWidth: highlight?.(x, y) ? 2 : 2, borderRadius: 4 },
          label: { color: inkOn(fill) } });
      }));
      return {
        ...base(),
        grid: { ...base().grid, top: 8, right: 8 },
        tooltip: { ...base().tooltip, formatter: (p) => tooltip(xs[p.value[0]], ys[p.value[1]], p.data.raw) },
        xAxis: catAxis(xs.map((x) => x.label), { position: 'top', axisLine: { show: false }, axisLabel: { color: TOK('--muted'), fontSize: 11, interval: 0 } }),
        yAxis: catAxis(ys.map((y) => y.label), { inverse: true, axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
        series: [{ type: 'heatmap', data, label: { show: true, fontSize: 11, formatter: (p) => label(p.data.raw) },
                   emphasis: { itemStyle: { borderColor: TOK('--ink'), borderWidth: 2 } } }],
      };
    },
    swapChart() {
      void this.themeTick;
      if (!this.S) return null;
      const T = this.teams, M = this.S.schedule_swap;
      const xs = T.map((t) => ({ id: t.team_id, label: t.abbrev || t.name.slice(0, 4) }));
      const ys = T.map((t) => ({ id: t.team_id, label: t.name }));
      const vals = T.flatMap((a) => T.map((b) => M[a.team_id]?.[b.team_id]?.[0] ?? 0));
      return {
        option: this.heatmap({
          xs, ys, ramp: 'seq', domain: [Math.min(...vals), Math.max(...vals)],
          cell: (x, y) => { const r = M[y.id]?.[x.id]; return r ? { v: r[0], w: r[0], l: r[1] } : null; },
          label: (c) => `${c.w}-${c.l}`,
          highlight: (x, y) => x.id === y.id,
          tooltip: (x, y, c) => `<b>${esc(y.label)}</b> with ${esc(this.tname(x.id))}'s schedule` + tipRow('Record', `${c.w}-${c.l}`)
            + tipRow('Actual record', fmt.rec(this.team(y.id))),
        }),
        table: { cols: [{ label: 'Team ↓ / schedule of →' }, ...T.map((t) => ({ label: t.abbrev || t.name, num: 1 }))],
                 rows: T.map((a) => [a.name, ...T.map((b) => (M[a.team_id]?.[b.team_id] || []).join('-'))]) },
      };
    },
    absenceChart() {
      void this.themeTick;
      const T = [...(this.S?.teams || [])].sort((a, b) => a.missed_pts - b.missed_pts);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 8, right: 56 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const t = T[ps[0].dataIndex]; return `<b>${esc(t.name)}</b>` + tipRow('Points lost to absences', fmt.n(t.missed_pts)); } },
          xAxis: labeledValueAxis(),
          yAxis: catAxis(T.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
          series: [{ type: 'bar', barWidth: 14, data: T.map((t) => t.missed_pts),
                     itemStyle: { color: TOK('--s1'), borderRadius: [0, 4, 4, 0] },
                     label: { show: true, position: 'right', color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.n(p.value) } }],
        },
        table: { cols: [{ label: 'Team' }, { label: 'Points lost to absences', num: 1 }],
                 rows: [...T].reverse().map((t) => [t.name, fmt.n(t.missed_pts)]) },
      };
    },
    get luckRows() {
      if (!this.S) return [];
      return this.S.weeks
        .filter((w) => w.result && w.opp_id != null && !w.playoff)
        .filter((w) => this.luckTeam === 'all' || w.team_id === +this.luckTeam)
        .filter((w) => this.luckFilter === 'all' || (this.luckFilter === 'bad' ? w.bad_beat : w.lucky_win))
        .sort((a, b) => b.mp - a.mp || b.pts - a.pts);
    },
    get badBeats() {
      return this.regWeeks.filter((w) => w.bad_beat).sort((a, b) => a.rank - b.rank || b.pts - a.pts).slice(0, 5);
    },

    // ---------- Coaching
    effChart() {
      void this.themeTick;
      const T = [...(this.S?.teams || [])].filter((t) => t.efficiency != null).sort((a, b) => a.efficiency - b.efficiency);
      const min = Math.floor((Math.min(...T.map((t) => t.efficiency)) - 0.02) * 50) / 50;
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 8, right: 56 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const t = T[ps[0].dataIndex];
              return `<b>${esc(t.name)}</b>` + tipRow('Lineup efficiency', fmt.pct(t.efficiency)) + tipRow('Points left on bench', fmt.n(t.bench_lost))
                + tipRow('Coaching losses', t.coaching_losses); } },
          xAxis: axis({ type: 'value', min: Math.max(0, min), max: 1, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => Math.round(v * 100) + '%' } }),
          yAxis: catAxis(T.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
          series: [{ type: 'scatter', symbolSize: 13, data: T.map((t, i) => [t.efficiency, i]),
                     itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 },
                     label: { show: true, position: 'right', color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.pct(p.value[0]) } }],
        },
        table: { cols: [{ label: 'Team' }, { label: 'Efficiency', num: 1 }, { label: 'Bench points lost', num: 1 }, { label: 'Coaching losses', num: 1 }],
                 rows: [...T].reverse().map((t) => [t.name, fmt.pct(t.efficiency), fmt.n(t.bench_lost), t.coaching_losses]) },
      };
    },
    effHeatChart() {
      void this.themeTick;
      if (!this.S) return null;
      const mps = this.doneMps, T = this.teams;
      const by = {}; this.regWeeks.forEach((w) => { by[`${w.team_id}-${w.mp}`] = w; });
      const vals = this.regWeeks.map((w) => w.efficiency).filter((v) => v != null);
      return {
        option: this.heatmap({
          xs: mps.map((m) => ({ mp: m, label: String(m) })), ys: T.map((t) => ({ id: t.team_id, label: t.name })),
          ramp: 'seq', domain: [Math.min(0.8, ...vals), 1],
          cell: (x, y) => { const w = by[`${y.id}-${x.mp}`]; return w && w.efficiency != null ? { v: w.efficiency, w } : null; },
          label: (c) => Math.round(c.v * 100),
          tooltip: (x, y, c) => `<b>${esc(y.label)}</b> · ${this.wk(x.mp)}` + tipRow('Efficiency', fmt.pct(c.v))
            + tipRow('Scored', fmt.n(c.w.pts)) + tipRow('Best possible', fmt.n(c.w.opt_pts))
            + (c.w.missed_starts?.length ? `<div style="margin-top:4px;color:${TOK('--muted')}">Missed: ${c.w.missed_starts.map((m) => `${esc(m.name)} (${fmt.n(m.pts)})`).join(', ')}</div>` : ''),
        }),
        table: { cols: [{ label: 'Team' }, ...mps.map((m) => ({ label: 'Wk ' + m, num: 1 }))],
                 rows: T.map((t) => [t.name, ...mps.map((m) => { const w = by[`${t.team_id}-${m}`]; return w ? fmt.pct(w.efficiency, 0) : '–'; })]) },
      };
    },
    benchChart() {
      void this.themeTick;
      const T = [...(this.S?.teams || [])].sort((a, b) => a.bench_lost - b.bench_lost);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 8, right: 56 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const t = T[ps[0].dataIndex]; return `<b>${esc(t.name)}</b>` + tipRow('Points left on bench', fmt.n(t.bench_lost)); } },
          xAxis: labeledValueAxis(),
          yAxis: catAxis(T.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
          series: [{ type: 'bar', barWidth: 14, data: T.map((t) => t.bench_lost), itemStyle: { color: TOK('--s1'), borderRadius: [0, 4, 4, 0] },
                     label: { show: true, position: 'right', color: TOK('--ink2'), fontSize: 11, formatter: (p) => fmt.n(p.value) } }],
        },
        table: { cols: [{ label: 'Team' }, { label: 'Points left on bench', num: 1 }], rows: [...T].reverse().map((t) => [t.name, fmt.n(t.bench_lost)]) },
      };
    },
    get coachingLosses() {
      return (this.S?.weeks || []).filter((w) => w.coaching_loss).sort((a, b) => (b.opt_pts - b.opp_pts) - (a.opt_pts - a.opp_pts));
    },

    // ---------- Games cap
    capChart() {
      void this.themeTick;
      const C = this.S?.caps || [];
      const counts = {}; C.forEach((c) => { counts[c.cap] = (counts[c.cap] || 0) + 1; });
      const usual = +Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0];
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 24, right: 8 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const c = C[ps[0].dataIndex];
              return `<b>${this.wk(c.mp)}</b>${c.playoff ? ' · Playoffs' : ''} <span style="color:${TOK('--muted')}">${fmt.date(c.start)} – ${fmt.date(c.end)}</span>`
                + tipRow('Games cap', c.cap) + tipRow('NBA game days', `${c.game_days} of ${c.days}`); } },
          xAxis: catAxis(C.map((c) => String(c.mp)), { axisLabel: { color: TOK('--muted'), fontSize: 11, interval: 0 } }),
          yAxis: axis({ type: 'value' }),
          series: [{ type: 'bar', barMaxWidth: 28,
            data: C.map((c) => ({ value: c.cap, itemStyle: { color: TOK('--s1'), opacity: c.playoff ? 0.5 : 1, borderRadius: [4, 4, 0, 0] } })),
            label: { show: true, position: 'top', color: TOK('--ink2'), fontSize: 11, formatter: (p) => (p.value !== usual ? p.value : '') } }],
        },
        table: { cols: [{ label: 'Week' }, { label: 'Dates' }, { label: 'Days', num: 1 }, { label: 'NBA game days', num: 1 }, { label: 'Cap', num: 1 }],
                 rows: C.map((c) => [this.wk(c.mp) + (c.playoff ? ' (playoffs)' : ''), `${fmt.date(c.start)} – ${fmt.date(c.end)}`, c.days, c.game_days, c.cap]) },
      };
    },
    unusedChart() {
      void this.themeTick;
      if (!this.S) return null;
      const mps = this.doneMps, T = this.teams;
      const by = {}; this.regWeeks.forEach((w) => { by[`${w.team_id}-${w.mp}`] = w; });
      const unused = (w) => Math.max(0, (w.cap ?? 0) - (w.gp ?? 0));
      const vals = this.regWeeks.map(unused);
      return {
        option: this.heatmap({
          xs: mps.map((m) => ({ mp: m, label: String(m) })), ys: T.map((t) => ({ id: t.team_id, label: t.name })),
          ramp: 'seq', domain: [0, Math.max(4, ...vals)],
          cell: (x, y) => { const w = by[`${y.id}-${x.mp}`]; return w ? { v: unused(w), w } : null; },
          label: (c) => (c.v ? c.v : '·'),
          tooltip: (x, y, c) => `<b>${esc(y.label)}</b> · ${this.wk(x.mp)}` + tipRow('Games used', `${fmt.n(c.w.gp)} of ${c.w.cap}`)
            + tipRow('Unused games', c.v) + tipRow('Roster could fill', fmt.n(c.w.max_games))
            + (c.w.over_cap ? `<div style="margin-top:4px">⚠ Over the cap</div>` : ''),
        }),
        table: { cols: [{ label: 'Team' }, ...mps.map((m) => ({ label: 'Wk ' + m, num: 1 }))],
                 rows: T.map((t) => [t.name, ...mps.map((m) => { const w = by[`${t.team_id}-${m}`]; return w ? `${fmt.n(w.gp)}/${w.cap}` : '–'; })]) },
      };
    },
    get capLedger() {
      void Object.keys(this.seasons).length;
      const rows = [];
      for (const s of Object.keys(this.seasons).map(Number).sort((a, b) => b - a)) {
        const S = this.seasons[s];
        const nm = Object.fromEntries(S.teams.map((t) => [t.team_id, t.name]));
        S.weeks.filter((w) => w.over_cap).forEach((w) => rows.push({ season: s, mp: w.mp, team: nm[w.team_id], owner: this.owner(w.owner_id),
          gp: w.gp, cap: w.cap, over: w.gp - w.cap, result: w.result, margin: w.margin }));
      }
      return rows;
    },

    // ---------- Franchises
    get allTime() {
      const A = this.includeCurrent ? this.history?.all_time : this.history?.all_time_complete;
      return [...(A || [])].sort((a, b) => b.titles - a.titles || b.win_pct - a.win_pct);
    },
    get champions() { return (this.history?.seasons || []).filter((s) => s.champion).sort((a, b) => b.season - a.season); },
    get ownerSeasons() { return (this.history?.seasons || []).filter((s) => s.owner_id === this.ownerSel).sort((a, b) => a.season - b.season); },
    get ownerList() {
      return [...(this.history?.all_time || [])].sort((a, b) => this.owner(a.owner_id).localeCompare(this.owner(b.owner_id)));
    },
    finishChart() {
      void this.themeTick;
      const R = this.ownerSeasons.filter((s) => s.complete);
      const maxTeams = Math.max(8, ...(this.history?.seasons || []).map((s) => s.rank_final || 0));
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 16, right: 24, left: 8 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: TOK('--axis') } },
            formatter: (ps) => { const s = R[ps[0].dataIndex];
              return `<b>${fmt.seasonLabel(s.season)}</b> · ${esc(s.name)}` + tipRow('Finish', fmt.ord(s.rank_final) + (s.champion ? ' 🏆' : ''))
                + tipRow('Record', fmt.rec(s)) + tipRow('Luck', fmt.signed(s.luck) + ' W') + tipRow('Coaching', fmt.pct(s.efficiency)); } },
          xAxis: catAxis(R.map((s) => fmt.seasonLabel(s.season)), { boundaryGap: true }),
          yAxis: axis({ type: 'value', inverse: true, min: 1, max: maxTeams, interval: 1, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => fmt.ord(v) } }),
          series: [{ type: 'line', data: R.map((s) => s.rank_final), symbol: 'circle', symbolSize: 10,
                     lineStyle: { width: 2, color: TOK('--s1') }, itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 },
                     label: { show: true, position: 'top', color: TOK('--ink2'), fontSize: 11, formatter: (p) => (R[p.dataIndex].champion ? '🏆' : fmt.ord(p.value)) } }],
        },
        table: { cols: [{ label: 'Season' }, { label: 'Team' }, { label: 'Finish', num: 1 }, { label: 'Record' }],
                 rows: R.map((s) => [fmt.seasonLabel(s.season), s.name, fmt.ord(s.rank_final), fmt.rec(s)]) },
      };
    },
    h2hChart() {
      void this.themeTick;
      if (!this.history) return null;
      const H = this.history.h2h;
      const O = [...this.history.all_time].filter((a) => a.w + a.l + a.t > 0).sort((a, b) => b.win_pct - a.win_pct)
        .map((a) => ({ id: a.owner_id, label: this.owner(a.owner_id) }));
      return {
        option: this.heatmap({
          xs: O.map((o) => ({ ...o, label: o.label.length > 10 ? o.label.slice(0, 9) + '…' : o.label })), ys: O,
          ramp: 'div', domain: [0, 1],
          cell: (x, y) => { if (x.id === y.id) return null; const r = H[y.id]?.[x.id]; if (!r) return null;
            const g = r.w + r.l + r.t; return g ? { v: (r.w + 0.5 * r.t) / g, ...r } : null; },
          label: (c) => `${c.w}-${c.l}`,
          tooltip: (x, y, c) => `<b>${esc(y.label)}</b> vs ${esc(this.owner(x.id))}` + tipRow('All-time', `${c.w}-${c.l}${c.t ? '-' + c.t : ''}`)
            + tipRow('Win %', fmt.pct(c.v, 0)) + tipRow('Playoffs', `${c.playoff_w}-${c.playoff_l}`)
            + tipRow('Points', `${fmt.n(c.pf)} – ${fmt.n(c.pa)}`),
        }),
        table: { cols: [{ label: 'Franchise ↓ vs →' }, ...O.map((o) => ({ label: o.label, num: 1 }))],
                 rows: O.map((y) => [y.label, ...O.map((x) => { const r = H[y.id]?.[x.id]; return r ? `${r.w}-${r.l}` : '–'; })]) },
      };
    },
    recordLabel(r) { return `${fmt.seasonLabel(r.season)} · Wk ${r.mp}`; },

    // ---------- This Week (live predictor)
    get selMatchup() { return this.live?.matchups?.find((m) => m.matchup_id === this.weekMid) || this.live?.matchups?.[0]; },
    dayLabel(d) {
      const dt = new Date(d.date + 'T12:00:00');
      return { dow: dt.toLocaleDateString('en-US', { weekday: 'short' }), md: `${dt.getMonth() + 1}/${dt.getDate()}` };
    },
    get weekRange() {
      const D = this.live?.days || [];
      return D.length ? `${fmt.date(D[0].date)} – ${fmt.date(D[D.length - 1].date)}` : '';
    },
    get liveUpdated() {
      return this.live ? new Date(this.live.updated_at).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : '';
    },
    cellStyle(d) {
      if (d.state !== 'game' || !d.start) return '';
      return `background: color-mix(in srgb, var(--s1) ${Math.round(8 + d.start * 52)}%, transparent)`;
    },
    timelineChart() {
      void this.themeTick;
      const m = this.selMatchup;
      if (!m) return null;
      const id = String(m.matchup_id), hid = String(m.home.team_id);
      const S = (this.live.snapshots || []).filter((s) => s.probs[id]).map((s) => [s.ts * 1000, s.probs[id][hid]]);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 16, right: 16 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: TOK('--axis') } },
            formatter: (ps) => { const [ts, p] = ps[0].value;
              return `<b>${new Date(ts).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' })}</b>`
                + tipRow(esc(m.home.name), fmt.pct(p, 0)) + tipRow(esc(m.away.name), fmt.pct(1 - p, 0)); } },
          xAxis: axis({ type: 'time', splitLine: { show: false } }),
          yAxis: axis({ type: 'value', min: 0, max: 1, interval: 0.25, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => Math.round(v * 100) + '%' } }),
          series: [{ type: 'line', data: S, symbol: 'circle', symbolSize: S.length > 30 ? 0 : 8, showSymbol: S.length <= 30,
                     lineStyle: { width: 2, color: TOK('--s1') }, itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 },
                     markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: TOK('--axis'), type: 'solid', width: 1 }, data: [{ yAxis: 0.5 }] } }],
        },
        table: { cols: [{ label: 'Time' }, { label: m.home.name, num: 1 }, { label: m.away.name, num: 1 }],
                 rows: S.map(([ts, p]) => [new Date(ts).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' }), fmt.pct(p, 0), fmt.pct(1 - p, 0)]) },
      };
    },
    marginChart() {
      void this.themeTick;
      const m = this.selMatchup;
      if (!m) return null;
      const { edges, counts } = m.margin_hist;
      const total = counts.reduce((a, b) => a + b, 0) || 1;
      const mids = counts.map((_, i) => (edges[i] + edges[i + 1]) / 2);
      const s2 = TOK('--s2'), s1 = TOK('--s1');
      return {
        option: {
          ...base(),
          legend: { ...base().legend, data: [`${m.home.name} wins`, `${m.away.name} wins`] },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const i = ps[0].dataIndex; const lo = edges[i], hi = edges[i + 1];
              const who = mids[i] >= 0 ? m.home.name : m.away.name;
              return `<b>${esc(who)} by ${fmt.n(Math.abs(mids[i] >= 0 ? lo : hi))}–${fmt.n(Math.abs(mids[i] >= 0 ? hi : lo))}</b>` + tipRow('Share of simulations', fmt.pct(counts[i] / total)); } },
          xAxis: catAxis(mids.map((x) => fmt.n(x)), { axisLabel: { color: TOK('--muted'), fontSize: 11, interval: Math.ceil(mids.length / 8) } }),
          yAxis: axis({ type: 'value', axisLabel: { show: false } }),
          series: [
            { type: 'bar', name: `${m.home.name} wins`, stack: 'm', barCategoryGap: '8%', data: counts.map((c, i) => (mids[i] >= 0 ? c : '-')), itemStyle: { color: s1, borderRadius: [3, 3, 0, 0] } },
            { type: 'bar', name: `${m.away.name} wins`, stack: 'm', data: counts.map((c, i) => (mids[i] < 0 ? c : '-')), itemStyle: { color: s2, borderRadius: [3, 3, 0, 0] } },
          ],
        },
        table: { cols: [{ label: `Final margin (${m.home.name} − ${m.away.name})` }, { label: 'Share of simulations', num: 1 }],
                 rows: counts.map((c, i) => [`${fmt.n(edges[i])} to ${fmt.n(edges[i + 1])}`, fmt.pct(c / total)]) },
      };
    },
    calibChart() {
      void this.themeTick;
      const C = (this.backtest?.calibration || []).filter((c) => c.n > 0);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 28, right: 16 },
          legend: { ...base().legend, data: ['Actual win rate', 'Perfect calibration'] },
          tooltip: { ...base().tooltip, trigger: 'item',
            formatter: (p) => { const c = C[p.dataIndex]; return c ? tipRow('Predicted', fmt.pct(c.p_mean, 0)) + tipRow('Actually won', fmt.pct(c.won, 0)) + tipRow('Forecasts', fmt.n(c.n)) : ''; } },
          xAxis: axis({ type: 'value', min: 0, max: 1, name: 'Predicted', nameLocation: 'middle', nameGap: 24, nameTextStyle: { color: TOK('--muted') }, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => Math.round(v * 100) + '%' } }),
          yAxis: axis({ type: 'value', min: 0, max: 1, axisLabel: { color: TOK('--muted'), fontSize: 11, formatter: (v) => Math.round(v * 100) + '%' } }),
          series: [
            { type: 'line', name: 'Perfect calibration', data: [[0, 0], [1, 1]], symbol: 'none', silent: true, tooltip: { show: false },
              lineStyle: { width: 1, color: TOK('--axis') }, itemStyle: { color: TOK('--axis') } },
            { type: 'line', name: 'Actual win rate', data: C.map((c) => [c.p_mean, c.won]), symbol: 'circle', symbolSize: 9,
              lineStyle: { width: 2, color: TOK('--s1') }, itemStyle: { color: TOK('--s1'), borderColor: TOK('--surface'), borderWidth: 2 } },
          ],
        },
        table: { cols: [{ label: 'Predicted (avg)', num: 1 }, { label: 'Actually won', num: 1 }, { label: 'Forecasts', num: 1 }],
                 rows: C.map((c) => [fmt.pct(c.p_mean, 0), fmt.pct(c.won, 0), fmt.n(c.n)]) },
      };
    },
    byDayChart() {
      void this.themeTick;
      const D = (this.backtest?.by_day || []).filter((d) => d.n >= 20);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 16, right: 16 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const d = D[ps[0].dataIndex]; return `<b>Start of day ${d.day_idx + 1}</b>` + tipRow('Brier score', d.brier.toFixed(3)) + tipRow('Forecasts', d.n); } },
          xAxis: catAxis(D.map((d) => 'Day ' + (d.day_idx + 1))),
          yAxis: axis({ type: 'value', min: 0, max: 0.25 }),
          series: [{ type: 'bar', barMaxWidth: 28, data: D.map((d) => d.brier), itemStyle: { color: TOK('--s1'), borderRadius: [4, 4, 0, 0] },
                     markLine: { silent: true, symbol: 'none', lineStyle: { color: TOK('--muted'), type: 'solid', width: 1 },
                                 label: { formatter: 'Coin flip', color: TOK('--muted'), fontSize: 11, position: 'insideEndTop' }, data: [{ yAxis: 0.25 }] } }],
        },
        table: { cols: [{ label: 'Forecast made' }, { label: 'Brier score', num: 1 }, { label: 'Forecasts', num: 1 }],
                 rows: D.map((d) => ['Start of day ' + (d.day_idx + 1), d.brier.toFixed(3), d.n]) },
      };
    },
    get upsets() {
      return (this.backtest?.pregame_probs || []).filter((r) => r.season === this.season && r.won === 1)
        .sort((a, b) => a.p - b.p).slice(0, 5);
    },

    // ---------- Expected points
    expSteps: [
      { key: 'availability', label: 'Availability', short: 'Avail.', slot: '--s1' },
      { key: 'moves', label: 'Roster moves', short: 'Moves', slot: '--s2' },
      { key: 'lineup', label: 'Lineup & cap', short: 'Lineup', slot: '--s3' },
      { key: 'breakouts', label: 'Breakouts', short: 'Brkout', slot: '--s4' },
      { key: 'hot_cold', label: 'Hot/cold', short: 'Hot/cold', slot: '--s5' },
    ],
    get hasExpected() { return (this.S?.teams || []).some((t) => t.e0 != null); },
    get expTeams() { return [...(this.S?.teams || [])].filter((t) => t.e0 != null).sort((a, b) => b.x_actual - a.x_actual); },
    get expAvg() {
      const T = this.expTeams;
      if (!T.length) return null;
      const avg = (k) => T.reduce((s, t) => s + (t[k] || 0), 0) / T.length;
      return Object.fromEntries(['e0', 'availability', 'moves', 'lineup', 'breakouts', 'hot_cold', 'x_actual'].map((k) => [k, avg(k)]));
    },
    get expTiles() {
      const T = this.expTeams;
      if (!T.length) return [];
      const by = (k, dir = -1) => [...T].sort((a, b) => dir * (a[k] - b[k]))[0];
      const hot = by('hot_cold'), cold = by('hot_cold', 1), mv = by('moves'), av = by('availability', 1), lu = by('lineup'), bo = by('breakouts');
      return [
        { label: 'Best breakouts', value: fmt.signed(bo.breakouts, 0), sub: bo.name },
        { label: 'Ran hottest', value: fmt.signed(hot.hot_cold, 0), sub: hot.name },
        { label: 'Ran coldest', value: fmt.signed(cold.hot_cold, 0), sub: cold.name },
        { label: 'Best roster moves', value: fmt.signed(mv.moves, 0), sub: mv.name },
        { label: 'Most hurt by availability', value: fmt.signed(av.availability, 0), sub: av.name },
        { label: 'Best lineup decisions', value: fmt.signed(lu.lineup, 0), sub: lu.name },
      ];
    },
    get expBias() {
      const W = this.regWeeks.filter((w) => w.e3);
      if (!W.length) return null;
      const avg = (k) => W.reduce((s, w) => s + (w[k] || 0), 0) / W.length;
      return { per_week: avg('breakouts'), pct: avg('breakouts') / avg('e3'), hot: avg('hot_cold') };
    },
    signClass(v, band = 0.5) { return v > band ? 'up' : v < -band ? 'down' : ''; },
    signIcon(v, band = 0.5) { return v > band ? '▲' : v < -band ? '▼' : ''; },
    componentsChart() {
      void this.themeTick;
      const T = [...this.expTeams].reverse();
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 56, right: 16 },  // legend can wrap to two rows
          legend: { ...base().legend, data: this.expSteps.map((s) => s.label) },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', axis: 'y', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const t = T[ps[0].dataIndex];
              return `<b>${esc(t.name)}</b>` + this.expSteps.map((s) => tipRow(s.label, fmt.signed(t[s.key], 0))).join('')
                + tipRow('Pregame expected → actual', `${fmt.n(t.e0)} → ${fmt.n(t.x_actual)}`); } },
          xAxis: axis({ type: 'value' }),
          yAxis: catAxis(T.map((t) => t.name), { axisLine: { show: false }, axisLabel: { color: TOK('--ink2'), fontSize: 12 } }),
          series: this.expSteps.map((s) => ({
            type: 'bar', name: s.label, stack: 'c', stackStrategy: 'samesign', barWidth: 16,
            data: T.map((t) => t[s.key]), itemStyle: { color: TOK(s.slot), borderColor: TOK('--surface'), borderWidth: 1 },
          })),
        },
        table: { cols: [{ label: 'Team' }, ...this.expSteps.map((s) => ({ label: s.label, num: 1 }))],
                 rows: this.expTeams.map((t) => [t.name, ...this.expSteps.map((s) => fmt.signed(t[s.key], 0))]) },
      };
    },
    expWeek: null,
    get expWeekSel() {
      const mps = [...new Set((this.S?.weeks || []).filter((w) => w.result && w.e0 != null).map((w) => w.mp))].sort((a, b) => a - b);
      return mps.includes(this.expWeek) ? this.expWeek : mps[mps.length - 1];
    },
    get expWeekList() {
      return [...new Set((this.S?.weeks || []).filter((w) => w.result && w.e0 != null).map((w) => w.mp))].sort((a, b) => a - b);
    },
    get expRow() { return (this.S?.weeks || []).find((w) => w.mp === this.expWeekSel && w.team_id === this.teamSel && w.e0 != null); },
    waterfallChart() { return this.waterfallChartFor(this.expRow); },
    waterfallChartFor(w) {
      void this.themeTick;
      if (!w) return null;
      const [neg, , pos] = RAMPS.div[this.dark ? 'dark' : 'light'];
      const steps = [{ label: 'Pregame expected', short: 'Exp.', v: w.e0, total: true },
        ...this.expSteps.map((s) => ({ label: s.label, short: s.short, v: w[s.key] })), { label: 'Actual', short: 'Final', v: w.actual, total: true }];
      let run = 0;
      const baseVals = [], vals = [];
      steps.forEach((s) => {
        if (s.total) { baseVals.push(0); vals.push({ value: s.v, itemStyle: { color: TOK('--muted'), borderRadius: [4, 4, 0, 0] } }); run = s.v; return; }
        const lo = s.v >= 0 ? run : run + s.v;
        baseVals.push(lo);
        vals.push({ value: Math.abs(s.v), itemStyle: { color: s.v >= 0 ? pos : neg, borderRadius: 4 } });
        run += s.v;
      });
      const minY = Math.min(...baseVals.filter((v, i) => !steps[i].total), w.e0, w.actual, w.opp_pts ?? Infinity);
      return {
        option: {
          ...base(), grid: { ...base().grid, top: 24, right: 16 },
          tooltip: { ...base().tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(128,128,128,.08)' } },
            formatter: (ps) => { const s = steps[ps[0].dataIndex];
              return `<b>${s.label}</b>` + tipRow(s.total ? 'Points' : 'Change', s.total ? fmt.n(s.v) : fmt.signed(s.v, 0)); } },
          xAxis: catAxis(steps.map((s) => s.short), { axisLabel: { color: TOK('--muted'), fontSize: 11, interval: 0 } }),
          yAxis: axis({ type: 'value', min: Math.floor(minY * 0.9 / 50) * 50 }),
          series: [
            { type: 'bar', stack: 'w', data: baseVals, itemStyle: { color: 'transparent' }, emphasis: { disabled: true }, tooltip: { show: false }, silent: true },
            { type: 'bar', stack: 'w', barWidth: '50%', data: vals,
              label: { show: true, position: 'top', color: TOK('--ink2'), fontSize: 11,
                       formatter: (p) => { const s = steps[p.dataIndex]; return s.total ? fmt.n(s.v) : fmt.signed(s.v, 0); } },
              markLine: w.opp_pts == null ? undefined : { silent: true, symbol: 'none', lineStyle: { color: TOK('--ink2'), type: 'solid', width: 1 },
                label: { formatter: `Opponent ${fmt.n(w.opp_pts)}`, color: TOK('--ink2'), fontSize: 11, position: 'insideStartTop' }, data: [{ yAxis: w.opp_pts }] } },
          ],
        },
        table: { cols: [{ label: 'Step' }, { label: 'Points', num: 1 }],
                 rows: steps.map((s) => [s.label, s.total ? fmt.n(s.v) : fmt.signed(s.v, 0)]) },
      };
    },
    get flippedGames() {
      return this.regWeeks.filter((w) => w.flipped && w.result === 'W').sort((a, b) => (b.opp_e4 - b.e4) - (a.opp_e4 - a.e4));
    },
    playerSort: 'breakout',
    get expPlayers() {
      const P = [...(this.S?.players_expected || [])];
      const key = { breakout: (p) => -p.breakout, hot: (p) => -p.diff, cold: (p) => p.diff }[this.playerSort];
      P.sort((a, b) => key(a) - key(b));
      return P.slice(0, 15);
    },
};
