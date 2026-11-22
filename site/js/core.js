/* App state, data loading, routing and identity helpers (logos, franchise colors, headshots). */

// Short explanations shown behind the ⓘ buttons
const GLOSSARY = {
  winprob: 'Chance to win from 10,000 simulations of the rest of the week: each player\'s projected average, chance to play, and the games cap.',
  proj: 'Projected final score (average of the simulations) and the range most finishes land in (10th–90th percentile).',
  cap: 'Weekly games cap = 38 games per 7 NBA game days, rounded. Games past the cap don\'t count (league rule: never cross it).',
  start: 'How often this game makes the lineup in the simulations, given the remaining cap and better options that day.',
  upset: 'Upset: the winner had under a 35% chance before the week started.',
  badbeat: 'Bad beat: lost while scoring in the top half of the league that week.',
  flipped: 'Hot/cold flip: with every player at his season average, the other team would have won.',
  coachloss: 'Coaching loss: the team\'s own best possible lineup (same players, same cap) would have won.',
  breakdown: 'Pregame expected → availability (who played) → roster moves → lineup & cap → breakouts (season level vs. expectation) → hot/cold (week-to-week swings). Always adds up to the real score.',
  sofar: 'The same breakdown for the days played so far this week.',
  odds: '10,000 simulations of the rest of the season: every remaining game played from each roster’s projected strength, this week’s game from the live forecast, then the playoff bracket. Ties in the standings use head-to-head record, then points for. Clinched/Eliminated are exact math, not simulations.',
  power: 'Power rankings, in points per week at the average games cap. Roster talent = the current roster with perfect lineups: each player\u2019s rest-of-season points per game \u00d7 games \u00d7 chance he plays, best games filling the cap. Now = talent blended with what the team has actually scored (results count more as weeks are played). Roster only = the same, but with what the best possible lineups would have scored, so the manager\u2019s lineup calls don\u2019t count. Playoff push = expected points per playoff week (those weeks\u2019 games and caps, injured players returning). Grades come from the chance the score beats each other team in a typical week (A = wins about 70%+ of matchups, C = about 50%, F = under 30%). Management grade ranks four things within the league: lineup efficiency (actual vs best possible), bad starts (counting a player while a benched teammate who played, eligible for the slot, was projected 8+ points better that morning), waiver points above replacement per week, and droppable players (3+ points per game below the best free agent and worth 5+ points a week under the cap).',
  swings: 'Win chance: at the start of each day, the chance each team wins the week, from the score so far, games left under the cap, that day\u2019s rosters and each player\u2019s form up to then (nothing that happened later). Teams given 30% have won about 30% of the time. Sunday morning = the start of the matchup\u2019s last day. Drama blends how much the chances moved, how close it was on Sunday morning and how often the lead flipped, then ranks it against every matchup on record (90 = more dramatic than 90% of them). Clutch grade = wins above what the Sunday-morning chances expected, per matchup, graded against every team-season on record (A = top 15%, F = bottom 15%); a short record is pulled toward average so a lucky week or two can\u2019t make an A. A lead change = the favorite flipped (crossing 50% by more than 2 points). Each day\u2019s recap shows a team\u2019s points that counted, its best and worst counted game vs what was expected that morning, and regulars who sat.',
  injuries: 'Injuries: a player tagged OUT uses ESPN’s expected return date when there is one (otherwise how often players in the same spot have returned in past seasons). When a key teammate is likely to sit, players who historically gain from his absence get a boost — half of their measured with/without-him gain, which tested best against later games. Teammates with no history together (trades, signings, rookies) get an estimate from position and role: a same-position backup gains most.',
  waivers: 'Waiver wire: free agents ranked by projected fantasy points per game he plays (the games cap makes per-game quality what counts), in this league’s ESPN points scoring, over his games still to come this week and next. Each game = half our model — ESPN’s projection pulled toward his actual season and last 15 days — and half Sleeper’s projection for that specific game, re-scored with this league’s settings + any boost from an injured teammate. Injured players likely to miss most games are listed separately as stashes. “Compare with my team” shows the per-game gain over your weakest healthy player.',
  today: 'Today: free agents ranked only by DraftKings player props for today’s game (points, rebounds, assists, steals, blocks, threes), turned into fantasy points with this league’s scoring. Books re-price within hours of injury news or role changes, so this reacts faster than any season-average model. Turnovers and shooting aren’t priced, so they come from Sleeper’s projection for the game, scaled to the points line. “Since open” = how much the line-implied points moved since the line first came out. Pick your team for a start/sit list of your own players today.',
  longterm: 'Long-term: FP per game = FP per minute × minutes, projected separately and learned from five seasons of NBA game logs. Temporary bumps (a star teammate out short-term, hot streaks) are stripped out; ramp-ups, rookie growth and teammates out long-term are built in. Upside is calibrated so about 1 in 4 similar players reached it. Backtested on 2024-25 and 2025-26: rest-of-season error ~5.0 FP/g vs ~6-7 for season average.',
  draft: 'Draft grades: every player is ranked by total fantasy points over the season (his whole season, whichever team he ended up on). Spots gained = where he was drafted minus where he ranks: picked 37th, playing like the 9th-best player = +28. A hit is a pick that outperformed its draft spot. A player who barely mattered counts as replacement level instead of a huge negative. Letter grades compare a team’s spots gained per pick with every draft in league history (A = top 15%, F = bottom 15%). WAR details: points above a replacement-level player while on the drafting team, turned into wins. Waiver wire: pickups include players added before the season who weren’t drafted. Points that counted in the lineup from pickups, above what a replacement-level player (roughly the best free agent available) would have given in those same games, with replacement measured over the same weeks he was used (each matchup week ± one week). Verdicts: Big win = +100 or more, Helped = +25, Wash = within 25, Hurt = −25 or worse. A swung week = his edge over replacement that week was bigger than the final margin. Ones that got away = players a team dropped, with every game they’ve played since (any team) vs replacement. “Paid off” = pickups that beat replacement level; “played like #N” = his points per game while on your roster vs every regular that season. Waiver grades compare above-replacement points per week with every team-season on record, so a season in progress grades fairly.',
  trades: 'Trade analyzer: real trades are found on ESPN (accepted, in review or processed) and measured exactly: the rest of the season is simulated with rosters before vs after, using the same random numbers, so the change in playoff and title odds is the trade alone. Value is cap-aware: with a weekly games cap, two good players can be worth less than one great one because they compete for the same cap games. Players are valued at their rest-of-season FP/g (temporary bumps removed). The builder estimates any what-if instantly from the same numbers.',
  lineup: 'Best lineup: every remaining game this week is worth (chance he plays × projected points), using the same per-game projection as the waiver wire and win odds. An exact solver picks which games to start: each starter fills one slot he’s eligible for (PG, SG, SF, PF, C, G, F, 3 UTIL), and total starts never exceed the games left under this week’s cap. It saves cap games for better days instead of starting everyone early. The pickup is the free agent whose add (dropping one of your weakest healthy players) raises this plan the most.',
  projblend: 'Per-game projection: our model (Sleeper’s daily projection as the starting point, pulled toward his actual season and last 15 days), Sleeper’s projection for that game, and DraftKings player props when posted (points, rebounds, assists, steals, blocks, threes, turned into fantasy points with this league’s scoring). The weights are re-fit as games are graded.',
  stakes: 'Odds if this week’s game is won vs. lost, from the same 10,000 season simulations (the live matchup forecast decides this week). The gap is how much this game matters.',
  rooting: 'For every other game this week: how much your playoff odds move depending on who wins. Root for the team listed; the number is the swing in percentage points.',
  seedgrid: 'Chance of finishing at each seed. Darker = more likely. Seeds 1-2 get a first-round bye; 1-6 make the playoffs.',
  playerproj: 'PTS = points that counted so far this week. EXP = what the model expected from those same games (his projected average per game; a game in progress counts in proportion to how much has been played). Green = beating it, red = below. "~N more" = expected points still to come (rest of a live game + upcoming starts under the cap).',
};

const SILHOUETTE = "data:image/svg+xml," + encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'><rect width='40' height='40' fill='#2c2c2a'/>" +
  "<circle cx='20' cy='15' r='7' fill='#6f6e69'/><path d='M6 40c2-9 8-13 14-13s12 4 14 13z' fill='#6f6e69'/></svg>");

const POLL_MS = 2 * 60 * 1000;  // how often an open page checks for a new refresh

// Five tabs. Each tab's views show as one row of pills (SUBNAV); pages keep their old ids.
const SECTIONS = [
  { id: 'live', label: 'Live', icon: 'M3 12h4l3-8 4 16 3-8h4' },
  { id: 'league', label: 'League', icon: 'M4 20V10M10 20V4M16 20v-7M22 20H2' },
  { id: 'teams', label: 'Teams', icon: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 9c1-4 4-6 7-6s6 2 7 6' },
  { id: 'moves', label: 'Moves', icon: 'M7 4v16M7 4 3 8M7 4l4 4M17 20V4M17 20l-4-4M17 20l4-4' },
  { id: 'history', label: 'History', icon: 'M12 8v4l3 2M21 12a9 9 0 1 1-3-6.7M21 4v4h-4' },
];
const SUBNAV = {
  league: [
    { id: 'standings', label: 'Standings', href: '#/league/standings', pages: ['standings'] },
    { id: 'power', label: 'Power rankings', href: '#/league/power', pages: ['power'] },
    { id: 'deep', label: 'Deep stats', href: '#/league/deep', pages: ['deep'] },
  ],
  moves: [
    { id: 'waivers', label: 'Waiver wire', href: '#/moves/waivers', pages: ['waivers'] },
    { id: 'trades', label: 'Trade analyzer', href: '#/moves/trades', pages: ['trades'] },
    { id: 'draft', label: 'Draft grades', href: '#/moves/draft', pages: ['draft'] },
  ],
  history: [
    { id: 'franchises', label: 'All-time', href: '#/history', pages: ['franchises'] },
    { id: 'swings', label: 'Swings & comebacks', href: '#/history/swings', pages: ['swings'] },
  ],
};
// second-level pills inside a sub-view until those pages are merged (phase 2)
const SUBSUB = {
  deep: [{ id: 'luck', label: 'Luck & expected', href: '#/league/deep' }, { id: 'coach', label: 'Coaching & games cap', href: '#/league/deep/coach' }],
};
const LEAGUE_VIEWS = [
  { id: 'standings', label: 'Standings' }, { id: 'luck', label: 'Luck' }, { id: 'expected', label: 'Expected' },
  { id: 'coaching', label: 'Coaching' }, { id: 'cap', label: 'Games cap' }, { id: 'odds', label: 'Playoff odds' },
  { id: 'draft', label: 'Draft & waivers' },
];
// old links keep working
const OLD_PAGES = { week: 'live', matchups: 'live', standings: 'league/standings', luck: 'league/luck', expected: 'league/expected',
                    coaching: 'league/coaching', cap: 'league/cap', franchises: 'history', waivers: 'moves/waivers',
                    injuries: 'moves/injuries', trades: 'moves/trades', power: 'league/power', swings: 'history/swings' };
const SUB_HREF = { standings: '#/league/standings', odds: '#/league/odds', luck: '#/league/luck', expected: '#/league/expected',
                   coaching: '#/league/coaching', cap: '#/league/cap', waivers: '#/moves/waivers', injuries: '#/moves/injuries' };

window.Core = {
  meta: null, history: null, seasons: {}, season: null, live: null, backtest: null,
  section: 'live', page: 'matchups', loading: true, error: null, themeTick: 0, myOwner: null,
  teamSel: null, ownerSel: null, luckTeam: 'all', luckFilter: 'all', includeCurrent: false,
  weekSel: null, weekMid: null, gridView: false, rosterSide: 0, nowTick: Date.now(),
  sections: SECTIONS, subnavs: SUBNAV, subsubs: SUBSUB, subHref: SUB_HREF, leagueViews: LEAGUE_VIEWS, glossary: GLOSSARY, silhouette: SILHOUETTE,
  fmt, tableHtml,

  async init() {
    try { this.myOwner = localStorage.getItem('myOwner') || null; } catch (e) { /* storage blocked */ }
    addEventListener('hashchange', () => this.route());
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => this.themeTick++);
    try {
      const get = (f) => fetch(`data/${f}`, { cache: 'no-cache' }).then((r) => { if (!r.ok) throw new Error(f); return r.json(); });
      const optional = (f) => get(f).catch(() => null);
      [this.meta, this.history, this.live, this.backtest] = await Promise.all(
        [get('meta.json'), get('history.json'), optional('live.json'), optional('backtest.json')]);
      const cur = this.meta.current_season;
      await this.loadSeason(cur);
      const hasGames = this.seasons[cur].weeks.some((w) => w.result && !w.playoff);
      await this.selectSeason(hasGames || this.live?.active ? cur : Math.max(...this.meta.seasons.filter((s) => s !== cur)));
      const ranked = [...this.history.all_time_complete].sort((a, b) => b.win_pct - a.win_pct);
      this.ownerSel = ranked[0]?.owner_id;
    } catch (e) {
      this.error = `Couldn't load league data (${e.message}). Run python pipeline/build.py first.`;
    }
    this.loading = false;
    await this.route();
    this.loadOdds();
    this.loadInjuries();
    this.loadWaivers();
    this.loadLineups();
    this.loadRosters();
    // keep "updated X min ago" current, and pick up new refreshes without a reload
    setInterval(() => { this.nowTick = Date.now(); }, 30 * 1000);
    setInterval(() => { if (!document.hidden) this.poll(); }, POLL_MS);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) this.poll(); });
  },

  // A refresh rewrites meta.json; when its timestamp changes, swap in the new live/season/history data.
  async poll() {
    try {
      const get = (f) => fetch(`data/${f}`, { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null));
      const m = await get('meta.json');
      if (!m || m.updated_at === this.meta?.updated_at) return;
      const [live, history, cur] = await Promise.all([get('live.json'), get('history.json'), get(`season_${m.current_season}.json`)]);
      const followLive = this.isLiveWeek;
      if (history) this.history = history;
      if (cur) this.seasons[m.current_season] = cur;
      if (live) this.live = live;
      this._pregame = null;
      this.meta = m;
      if (followLive && this.live?.active) this.weekSel = this.live.mp;
      this.loadOdds();
      this.loadInjuries();
      this.loadWaivers();
      this.loadLineups();
      this.loadRosters();
    } catch (e) { /* offline or mid-deploy: try again next tick */ }
  },
  ago(iso) {
    void this.nowTick;
    if (!iso) return '';
    const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (min < 1) return 'just now';
    if (min < 60) return `${min} min ago`;
    const h = Math.floor(min / 60);
    return h < 24 ? `${h} hr ${min % 60} min ago` : new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  },

  // #/live[/season/mp] · #/matchup/season/mp/id · #/league/{standings,odds,power,deep,luck,...} · #/teams · #/team/owner
  // #/moves/{waivers,injuries,trades,draft} · #/history[/swings]
  async route() {
    let parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
    if (parts.length && OLD_PAGES[parts[0]] && !(parts[0] === 'matchups' && parts.length > 1)) {
      parts = [...OLD_PAGES[parts[0]].split('/'), ...parts.slice(1)];
    }
    if (parts[0] === 'matchups') parts[0] = 'live';
    if (parts[0] === 'league' && parts[1] === 'draft') parts = ['moves', 'draft'];
    const [sec = 'live', a, b, c] = parts;
    if (sec === 'matchup' && a && b && c) {
      if (+a !== this.season) await this.selectSeason(+a);
      Object.assign(this, { section: 'live', page: 'h2h', weekSel: +b, weekMid: +c });
      this.loadSwings();
    } else if (sec === 'live') {
      // a bare #/live always means "now": the live week if one is running
      const target = a ? +a : (this.live?.active ? this.live.season : this.season);
      if (target !== this.season) await this.selectSeason(target);
      Object.assign(this, { section: 'live', page: 'matchups', weekSel: b ? +b : this.defaultWeek });
    } else if (sec === 'league') {
      if (a === 'power') {
        Object.assign(this, { section: 'league', page: 'power' });
        this.loadPower();
      } else {
        await this.seasonWithResults();
        this.section = 'league';
        const coach = ['coaching', 'cap'].includes(a) || (a === 'deep' && b === 'coach');
        const deep = coach || ['deep', 'luck', 'expected'].includes(a);
        this.page = deep ? 'deep' : 'standings';
        if (deep) this.deepView = coach ? 'coach' : 'luck';
        if (coach) this.loadAll();
      }
    } else if (sec === 'moves') {
      const view = ['trades', 'draft'].includes(a) ? a : 'waivers';
      Object.assign(this, { section: 'moves', page: view });
      if (a === 'injuries') this.injOpen = true;
      if (view === 'draft') { await this.seasonWithResults(); this.loadDraft(); }
    } else if (sec === 'teams' || sec === 'team') {
      await this.seasonWithResults();
      const owner = sec === 'team' && a ? decodeURIComponent(a) : (this.myOwner || this.S?.teams.find((t) => t.team_id === this.teamSel)?.owner_id);
      const t = this.S?.teams.find((x) => x.owner_id === owner);
      Object.assign(this, { section: 'teams', page: 'team', teamOwner: owner, ownerSel: owner });
      if (t) this.teamSel = t.team_id;
    } else if (sec === 'history') {
      Object.assign(this, { section: 'history', page: a === 'swings' ? 'swings' : 'franchises' });
      if (a === 'swings') this.loadSwings();
    } else {
      Object.assign(this, { section: 'live', page: 'matchups', weekSel: this.defaultWeek });
    }
    scrollTo({ top: 0 });
  },
  // the sub-view pills for the current tab, and which one is active
  get subnav() { return SUBNAV[this.section] || []; },
  get subActive() { return this.subnav.find((v) => v.pages.includes(this.page))?.id; },
  get subsub() { return SUBSUB[this.subActive] || []; },
  get subsubActive() { return this.page === 'deep' ? this.deepView : this.page; },

  // ---------- My team (saved in this browser): highlighted everywhere, pinned to the top of Live
  setMyOwner(o) {
    this.myOwner = o || null;
    try { o ? localStorage.setItem('myOwner', o) : localStorage.removeItem('myOwner'); } catch (e) { /* storage blocked */ }
  },
  myTeamId(season = this.season) { return this.seasons[season]?.teams.find((t) => t.owner_id === this.myOwner)?.team_id ?? null; },
  isMine(teamId, season = this.season) { return !!this.myOwner && this.team(teamId, season)?.owner_id === this.myOwner; },
  get myTeamChoices() {
    const cur = this.seasons[this.meta?.current_season]?.teams || this.S?.teams || [];
    return [...cur].sort((a, b) => a.name.localeCompare(b.name));
  },
  go(hash) { location.hash = hash; },
  // stats pages need finished games: before week 1 of a new season ends, show the last season with results
  async seasonWithResults() {
    if (this.hasData) return;
    const prev = [...(this.meta?.seasons || [])].reverse().find((s) => s < this.season);
    if (prev) await this.selectSeason(prev);
  },

  async loadSeason(s) {
    if (!this.seasons[s]) {
      const r = await fetch(`data/season_${s}.json`, { cache: 'no-cache' });
      this.seasons[s] = await r.json();
    }
    return this.seasons[s];
  },
  loadAll() { return Promise.all((this.meta?.seasons || []).map((s) => this.loadSeason(s))); },
  async selectSeason(s) {
    s = +s;
    await this.loadSeason(s);
    this.season = s;
    const ts = this.seasons[s].teams;
    if (!ts.some((t) => t.team_id === this.teamSel)) {
      this.teamSel = [...ts].sort((a, b) => (a.seed ?? 99) - (b.seed ?? 99))[0]?.team_id;
    }
    this.luckTeam = 'all';
    if (this.section === 'live') this.weekSel = this.defaultWeek;
    // on a team page, follow the same franchise (owner) into the new season
    const mine = this.teamOwner && ts.find((t) => t.owner_id === this.teamOwner);
    if (mine) this.teamSel = mine.team_id;
  },
  toggleTheme() {
    document.documentElement.dataset.theme = isDark() ? 'light' : 'dark';
    try { localStorage.setItem('theme', document.documentElement.dataset.theme); } catch (e) { /* storage blocked */ }
    this.themeTick++;
  },
  get dark() { void this.themeTick; return isDark(); },

  // ---------- season accessors
  get S() { return this.seasons[this.season]; },
  get teams() { return this.S ? [...this.S.teams].sort((a, b) => (a.seed ?? 99) - (b.seed ?? 99) || b.w - a.w || b.pf - a.pf) : []; },
  team(id, season = this.season) { return this.seasons[season]?.teams.find((t) => t.team_id === id); },
  tname(id, season) { return this.team(id, season)?.name ?? '—'; },
  get regWeeks() { return this.S ? this.S.weeks.filter((w) => w.result && !w.playoff) : []; },
  get doneMps() { return [...new Set(this.regWeeks.map((w) => w.mp))].sort((a, b) => a - b); },
  cap(mp) { return this.S?.caps.find((c) => c.mp === mp); },
  wk(mp) { return `Wk ${mp}`; },
  wkDates(mp) { const c = this.cap(mp); return c ? `${fmt.date(c.start)} – ${fmt.date(c.end)}` : ''; },
  owner(id) { return this.history?.owners[id] ?? 'Unknown'; },
  get hasData() { return this.regWeeks.length > 0; },
  // the one "last updated" shown site-wide: the newest refresh (live forecast when the season is on)
  get updated() {
    const t = [this.live?.updated_at, this.meta?.updated_at].filter(Boolean).sort().pop();
    return t ? this.ago(t) : '';
  },
  get updatedExact() {
    const t = [this.live?.updated_at, this.meta?.updated_at].filter(Boolean).sort().pop();
    return t ? new Date(t).toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
  },

  // ---------- identity
  profile(ownerId) { return this.history?.profiles?.[ownerId] || {}; },
  // franchise color: fixed categorical slot for current franchises, neutral for former owners
  colorOf(ownerId) { const s = this.profile(ownerId).color_slot; return s ? `var(--s${s})` : 'var(--muted)'; },
  teamColor(teamId, season = this.season) { return this.colorOf(this.team(teamId, season)?.owner_id); },
  logoOf(teamId, season = this.season) { return this.team(teamId, season)?.logo || this.profile(this.team(teamId, season)?.owner_id).logo || this.avatarOf(teamId, season); },
  // fallback badge: team initials on the franchise color
  avatarOf(teamId, season = this.season) {
    const t = this.team(teamId, season);
    const slot = this.profile(t?.owner_id).color_slot;
    const bg = slot ? TOK(`--s${slot}`) : TOK('--muted');
    const ini = String(t?.name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
    return 'data:image/svg+xml,' + encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'><rect width='40' height='40' fill='${bg}'/>`
      + `<text x='20' y='25.5' text-anchor='middle' font-family='system-ui,sans-serif' font-size='15' font-weight='700' fill='white'>${ini}</text></svg>`);
  },
  headshot(id) { return id ? `https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/${id}.png&w=96&h=70&cb=1` : SILHOUETTE; },
  lastName(name) { const p = String(name || '').split(' '); return p.length > 1 ? `${p[0][0]}. ${p.slice(1).join(' ')}` : name; },
};
