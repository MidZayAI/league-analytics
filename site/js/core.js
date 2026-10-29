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
  injuries: 'Injuries: a player tagged OUT uses ESPN’s expected return date when there is one (otherwise how often players in the same spot have returned in past seasons). When a key teammate is likely to sit, players who historically gain from his absence get a boost — half of their measured with/without-him gain, which tested best against later games. Teammates with no history together (trades, signings, rookies) get an estimate from position and role: a same-position backup gains most.',
  waivers: 'Waiver wire: every free agent’s games still to come this week and next. Each game = chance he plays × (half our model — ESPN’s projection pulled toward his actual season and last 15 days — and half Sleeper’s projection for that specific game, re-scored with this league’s settings) + any boost from an injured teammate. “Compare with my team” shows the gain over your weakest healthy player across the same games.',
  stakes: 'Odds if this week’s game is won vs. lost, from the same 10,000 season simulations (the live matchup forecast decides this week). The gap is how much this game matters.',
  rooting: 'For every other game this week: how much your playoff odds move depending on who wins. Root for the team listed; the number is the swing in percentage points.',
  seedgrid: 'Chance of finishing at each seed. Darker = more likely. Seeds 1-2 get a first-round bye; 1-6 make the playoffs.',
  playerproj: 'PTS = points that counted so far this week. EXP = what the model expected from those same games (his projected average per game; a game in progress counts in proportion to how much has been played). Green = beating it, red = below. "~N more" = expected points still to come (rest of a live game + upcoming starts under the cap).',
};

const SILHOUETTE = "data:image/svg+xml," + encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'><rect width='40' height='40' fill='#2c2c2a'/>" +
  "<circle cx='20' cy='15' r='7' fill='#6f6e69'/><path d='M6 40c2-9 8-13 14-13s12 4 14 13z' fill='#6f6e69'/></svg>");

const POLL_MS = 2 * 60 * 1000;  // how often an open page checks for a new refresh

const SECTIONS = [
  { id: 'matchups', label: 'Matchups', icon: 'M4 6h16M4 12h16M4 18h10' },
  { id: 'teams', label: 'Teams', icon: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 9c1-4 4-6 7-6s6 2 7 6' },
  { id: 'league', label: 'League', icon: 'M4 20V10M10 20V4M16 20v-7M22 20H2' },
  { id: 'history', label: 'History', icon: 'M12 8v4l3 2M21 12a9 9 0 1 1-3-6.7M21 4v4h-4' },
];
const LEAGUE_VIEWS = [
  { id: 'standings', label: 'Standings' }, { id: 'luck', label: 'Luck' }, { id: 'expected', label: 'Expected' },
  { id: 'coaching', label: 'Coaching' }, { id: 'cap', label: 'Games cap' }, { id: 'odds', label: 'Playoff odds' },
];
const OLD_PAGES = { week: 'matchups', standings: 'league/standings', luck: 'league/luck', expected: 'league/expected',
                    coaching: 'league/coaching', cap: 'league/cap', franchises: 'history' };

window.Core = {
  meta: null, history: null, seasons: {}, season: null, live: null, backtest: null,
  section: 'matchups', page: 'matchups', loading: true, error: null, themeTick: 0,
  teamSel: null, ownerSel: null, luckTeam: 'all', luckFilter: 'all', includeCurrent: false,
  weekSel: null, weekMid: null, gridView: false, rosterSide: 0, nowTick: Date.now(),
  sections: SECTIONS, leagueViews: LEAGUE_VIEWS, glossary: GLOSSARY, silhouette: SILHOUETTE,
  fmt, tableHtml,

  async init() {
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

  // #/matchups[/season/mp] · #/matchup/season/mp/id · #/teams · #/league[/view] · #/history
  async route() {
    let parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
    if (parts.length === 1 && OLD_PAGES[parts[0]]) parts = OLD_PAGES[parts[0]].split('/');
    const [sec = 'matchups', a, b, c] = parts;
    if (sec === 'matchup' && a && b && c) {
      if (+a !== this.season) await this.selectSeason(+a);
      Object.assign(this, { section: 'matchups', page: 'h2h', weekSel: +b, weekMid: +c });
    } else if (sec === 'matchups') {
      // a bare #/matchups always means "now": the live week if one is running
      const target = a ? +a : (this.live?.active ? this.live.season : this.season);
      if (target !== this.season) await this.selectSeason(target);
      Object.assign(this, { section: 'matchups', page: 'matchups', weekSel: b ? +b : this.defaultWeek });
    } else if (sec === 'waivers') {
      Object.assign(this, { section: 'matchups', page: 'waivers' });
    } else if (sec === 'injuries') {
      Object.assign(this, { section: 'matchups', page: 'injuries' });
    } else if (sec === 'league') {
      await this.seasonWithResults();
      this.section = 'league';
      this.page = LEAGUE_VIEWS.some((v) => v.id === a) ? a : 'standings';
      if (this.page === 'cap') this.loadAll();
    } else if (sec === 'teams') {
      await this.seasonWithResults();
      Object.assign(this, { section: 'teams', page: 'teams' });
    } else if (sec === 'team' && a) {
      await this.seasonWithResults();
      const owner = decodeURIComponent(a);
      const t = this.S?.teams.find((x) => x.owner_id === owner);
      Object.assign(this, { section: 'teams', page: 'team', teamOwner: owner, ownerSel: owner });
      if (t) this.teamSel = t.team_id;
    } else if (sec === 'history') {
      Object.assign(this, { section: 'history', page: 'franchises' });
    } else {
      Object.assign(this, { section: 'matchups', page: 'matchups', weekSel: this.defaultWeek });
    }
    scrollTo({ top: 0 });
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
    if (this.section === 'matchups') this.weekSel = this.defaultWeek;
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
  get updated() {
    if (!this.meta) return '';
    return new Date(this.meta.updated_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
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
