"""Expected points: how a team should have scored in a matchup, and why it didn't.

Every team-week is split into steps that add up exactly to the actual score:
  E0  pregame expected     week-start roster, chance-to-play, cap (the predictor before day 1)
  E1  + availability       same roster, but who actually played
  E2  + roster moves       actual daily rosters (pickups/drops), who actually played
  E3  + lineup & cap       projected points of the games that actually counted
  E4  + breakouts          those games at each player's actual season average (better or worse
                           this season than expected going in: breakouts, rookies, declines)
  Act + hot/cold           actual points of those games (week-to-week swings around a player's
                           own season level; sums to about 0 over a season apart from timing)

Player projections (mu, chance to play) come from the backtested HistoryModel as of the start of
the week, so nothing uses hindsight. Lineups follow simulate.allocate (best 10 a day, then the
cap on the highest-projected games), the same rule the predictor uses.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simulate import allocate  # noqa: E402

E0_SIMS = 4000
E1_SIMS = 400


def expected_value(cands, cap, sims, rng):
    """Expected projected points started, and P(started) per candidate (availability ~ p)."""
    n = len(cands)
    if n == 0 or cap <= 0:
        return 0.0, np.zeros(n)
    mu, p, sp = cands.mu.to_numpy(float), cands.p.to_numpy(float), cands.sp.to_numpy()
    if np.all((p == 0) | (p == 1)):
        avail = (p == 1)[None, :]
    else:
        avail = rng.random((sims, n)) < p
    start = allocate(avail, mu, sp, cap).mean(axis=0)
    return float((mu * start).sum()), start


class SeasonContext:
    """Per-season lookups shared by every team-week."""

    def __init__(self, season, t, model):
        self.season, self.t, self.model = season, t, model
        d = t["daily"]
        self.by_team_mp = {k: g for k, g in d.groupby(["team_id", "mp"])}
        # did a player play on a day (known whenever any league team rostered him)
        self.played = {(sp, pid): gp for sp, pid, gp in zip(d.sp, d.player_id, d.gp)}
        self.games = t["pro_games"]
        # each player's average this season (to date for the current season): his "true level"
        self.season_avg = d[d.gp > 0].groupby("player_id").pts.mean().to_dict()
        self._raw = {}

    def raw(self, day):
        if day not in self._raw:
            self._raw[day] = self.model.raw_state(self.season, day)
        return self._raw[day]


def logged_game_mu(dates):
    """{(player_id, sp): pre-game projection} from the per-game log (data/history/gameproj_{season}.json):
    the blend of our model, Sleeper and the DraftKings line, with the weights fit to graded games.
    dates: {sp: date}. Logged numbers are frozen at tip-off, so nothing uses hindsight."""
    import json
    import gameproj
    if not gameproj.LOG.exists():
        return {}
    log = json.loads(gameproj.LOG.read_text(encoding="utf-8"))
    w = gameproj.load_weights()
    sp_of = {d.isoformat(): sp for sp, d in dates.items()}
    # calibrate: the blend runs a little hot vs what players actually scored in the games they played,
    # so scale it so logged projections match actual points league-wide (keeps "vs projection" centered)
    import nba_logs
    actual = {}
    for aid, a in nba_logs.load(gameproj.CURRENT).items():
        for _, day, _, mins, fp in a["games"]:
            if mins and mins > 0:
                actual[(aid, day)] = fp
    def blend(model, sleeper, mk):
        if mk is not None and sleeper is not None:
            return float(np.dot(w["market"], [model, sleeper, mk]))
        if sleeper is not None:
            return float(np.dot(w["nomarket"], [model, sleeper]))
        return float(model)
    num = den = 0.0
    for day, players in log.items():
        for aid, (model, sleeper, mk) in players.items():
            y = actual.get((aid, day))
            if y is not None:
                num += y
                den += blend(model, sleeper, mk)
    k = num / den if den > 1000 else 1.0
    out = {}
    for day, players in log.items():
        sp = sp_of.get(day)
        if sp is None:
            continue
        for aid, (model, sleeper, mk) in players.items():
            out[(int(aid), sp)] = max(blend(model, sleeper, mk) * k, 1.0)
    print(f"  logged projections: {len(out)} games, calibration x{k:.3f}")
    return out


def week_breakdown(ctx, tid, mp, week_days, cap, rng, done_days=None, partial_day=None, game_mu=None, cv=None,
                   repl=None, final=None):
    """Breakdown for one team-week. done_days limits it to the days played so far (live view);
    then E1/E2 get the same number of games the team has actually used so far.
    partial_day=(sp, pro_team_ids): on that day (today) only games of those NBA teams count, i.e.
    games that are final; games still in progress stay out so half a game isn't judged as a whole one.
    game_mu: {(player_id, sp): pre-game projection for that game} (betting lines + projections); games
    not in it use the week-start model. cv: game-to-game spread, for the luck percentile."""
    td = ctx.by_team_mp.get((tid, mp))
    if td is None or td.empty:
        return None
    start = week_days[0]
    roster0 = td[td.sp == start]
    if roster0.empty:
        roster0 = td[td.sp == td.sp.min()]
    roster0 = roster0.drop_duplicates("player_id")
    pids = list(dict.fromkeys(list(roster0.player_id) + list(td.player_id)))
    est = ctx.model.finish(ctx.raw(start), pids)
    est["mu"] = est.mu.clip(lower=1.0)  # same floor as simulate_team
    mu, p = est.mu.to_dict(), est.p.to_dict()
    gm = game_mu or {}

    def m(pid, sp):
        return gm.get((int(pid), int(sp)), mu[pid])

    done = list(week_days if done_days is None else done_days)

    def keep(sp, pro):
        return partial_day is None or sp != partial_day[0] or pro in partial_day[1]

    td = td[[keep(sp, pro) for sp, pro in zip(td.sp, td.pro)]]
    counted = td[td.counted & td.sp.isin(done)]
    cap_eff = cap if done_days is None else int(counted.gp.sum())

    def cands(rows):
        return pd.DataFrame(rows, columns=["sp", "mu", "p", "player_id"])

    # E0: pregame, whole week; per-day expectation lets the live view take just the days played
    c0 = pd.DataFrame([(sp, m(r.player_id, sp), p[r.player_id], r.player_id, r.pro)
                       for r in roster0.itertuples() for sp in week_days if r.pro in ctx.games.get(sp, ())],
                      columns=["sp", "mu", "p", "player_id", "pro"])
    _, s0 = expected_value(c0, cap, E0_SIMS, rng)
    in_scope = [sp in done and keep(sp, pro) for sp, pro in zip(c0.sp, c0.pro)]
    e0 = float((c0.mu * s0)[in_scope].sum()) if len(c0) else 0.0

    # E1: week-start roster with real availability (unknown after a drop to free agency -> p)
    rows = []
    for r in roster0.itertuples():
        for sp in done:
            gp = ctx.played.get((sp, r.player_id))
            # a game he actually played counts even when his listed NBA team (current team, not the
            # team at the time, after a trade) had no game that day
            if (r.pro in ctx.games.get(sp, ()) or (gp or 0) > 0) and keep(sp, r.pro):
                rows.append((sp, m(r.player_id, sp), p[r.player_id] if gp is None else float(gp > 0), r.player_id))
    e1, _ = expected_value(cands(rows), cap_eff, E1_SIMS, rng)

    # E2: actual daily rosters, real availability, best use of the cap by projection
    have = td[td.sp.isin(done) & (td.gp > 0)].drop_duplicates(["sp", "player_id"])
    e2, _ = expected_value(cands([(r.sp, m(r.player_id, r.sp), 1.0, r.player_id) for r in have.itertuples()]),
                           cap_eff, 1, rng)

    # E3 / actual: the games that counted
    cg = counted.assign(mu=[m(pid, sp) for pid, sp in zip(counted.player_id, counted.sp)],
                        lvl=counted.player_id.map(ctx.season_avg))
    e3, e4, actual = float(cg.mu.sum()), float(cg.lvl.sum()), float(cg.pts.sum())
    players = cg.groupby(["player_id", "name"]).agg(games=("gp", "size"), pts=("pts", "sum"), exp=("mu", "sum"),
                                                    lvl=("lvl", "sum")).reset_index()
    players["breakout"] = players.lvl - players.exp
    players["diff"] = players.pts - players.lvl  # hot (+) / cold (-) vs his own season level

    out = {"team_id": tid, "mp": mp, "e0": e0, "e1": e1, "e2": e2, "e3": e3, "e4": e4, "actual": actual,
           "availability": e1 - e0, "moves": e2 - e1, "lineup": e3 - e2, "breakouts": e4 - e3, "hot_cold": actual - e4,
           "days_done": len(done), "games_used": int(counted.gp.sum())}
    parts = out["e0"] + out["availability"] + out["moves"] + out["lineup"] + out["breakouts"] + out["hot_cold"]
    assert abs(parts - actual) < 1e-6, (tid, mp, parts, actual)

    # v2 steps: unused cap games cost a replacement-level player's points; pickups are judged against
    # that same replacement level; vs projection covers everyone else's counted games
    R = float(repl) if repl else float(ctx.model.repl)
    if final is None:
        final = done_days is None
    in0 = set(roster0.player_id)
    added_ids = set(have.player_id) - in0
    used = int(counted.gp.sum())
    u = max(int(cap) - used, 0) if final else 0
    pick = cg.player_id.isin(added_ids).to_numpy()
    out["repl"] = round(R, 2)
    out["unused_games"] = u
    out["unused"] = -u * R
    out["e0x"] = e0 + u * R
    out["pickups"] = float((cg.pts[pick] - R).sum())
    out["perf2"] = float((cg.pts[~pick] - cg.mu[~pick]).sum())
    out["lineup2"] = actual - out["e0x"] - out["availability"] - out["pickups"] - out["unused"] - out["perf2"]
    parts2 = out["e0x"] + out["availability"] + out["pickups"] + out["lineup2"] + out["unused"] + out["perf2"]
    assert abs(parts2 - actual) < 1e-6, (tid, mp, parts2, actual)

    if game_mu is not None:  # live view: the players behind each step
        name = dict(zip(td.player_id, td.name))
        # missed games: week-start players sitting out games their NBA team played
        c0 = c0.assign(start=s0)
        missed = {}
        for r in c0[in_scope].itertuples():
            if ctx.played.get((r.sp, r.player_id)) == 0:
                x = missed.setdefault(int(r.player_id), {"id": int(r.player_id), "name": name.get(r.player_id), "games": 0, "lost": 0.0})
                x["games"] += 1
                x["lost"] += float(r.mu * r.start)
        out["missed"] = sorted(missed.values(), key=lambda x: -x["lost"])[:4]
        # roster moves: who came in (games played, points that counted) and who went out
        by_p = counted.groupby("player_id").pts.sum().to_dict()
        added = {}
        cnt = counted.groupby("player_id").gp.sum().to_dict()
        for r in have.itertuples():
            if r.player_id not in in0:
                x = added.setdefault(int(r.player_id), {"id": int(r.player_id), "name": name.get(r.player_id), "games": 0,
                                                        "pts": float(by_p.get(r.player_id, 0.0)),
                                                        "counted": int(cnt.get(r.player_id, 0))})
                x["games"] += 1
        for x in added.values():
            x["vs_repl"] = round(x["pts"] - R * x["counted"], 1)
        on_team = set(zip(td.sp, td.player_id))
        dropped = {}
        for r in c0[in_scope].itertuples():
            if (r.sp, r.player_id) not in on_team:
                x = dropped.setdefault(int(r.player_id), {"id": int(r.player_id), "name": name.get(r.player_id), "games": 0})
                x["games"] += 1
        out["added"] = sorted(added.values(), key=lambda x: -x["pts"])[:3]
        out["dropped"] = sorted(dropped.values(), key=lambda x: -x["games"])[:3]
        # lineup & cap: games played that didn't count
        bench = td[td.sp.isin(done) & (td.gp > 0) & ~td.counted]
        bl = {}
        for r in bench.itertuples():
            x = bl.setdefault(int(r.player_id), {"id": int(r.player_id), "name": r.name, "games": 0, "pts": 0.0, "proj": 0.0})
            x["games"] += 1
            x["pts"] += float(r.pts)
            x["proj"] += float(m(r.player_id, r.sp))
        out["benched"] = sorted(bl.values(), key=lambda x: -x["pts"])[:4]
        # every player: counted games vs the pre-game projection of those games
        tab = []
        for (pid, nm), g in cg.groupby(["player_id", "name"]):
            b = bl.get(int(pid), {})
            tab.append({"id": int(pid), "name": nm, "games": int(len(g)), "pts": float(g.pts.sum()), "proj": round(float(g.mu.sum()), 1),
                        "bench_games": b.get("games", 0), "bench_pts": b.get("pts", 0.0),
                        "lined": int(sum((int(pid), int(sp)) in gm for sp in g.sp))})
        for pid, b in bl.items():
            if pid not in {t["id"] for t in tab}:
                tab.append({"id": pid, "name": b["name"], "games": 0, "pts": 0.0, "proj": 0.0, "bench_games": b["games"], "bench_pts": b["pts"], "lined": 0})
        out["players"] = sorted(tab, key=lambda x: (-x["pts"], -x["bench_pts"]))
        out["lined_games"] = int(sum(t["lined"] for t in tab))
        # luck percentile for points vs projection: the chance of doing this well or worse
        if cv and len(cg):
            sd = np.sqrt(np.sum(np.maximum(6.0, cv * cg.mu.to_numpy(float)) ** 2))
            from math import erf, sqrt
            out["pct_perf"] = round(0.5 * (1 + erf((actual - e3) / sd / sqrt(2))), 3)
    return out, players


STEPS = ("who", "pickups", "lineup", "unused", "perf", "luck", "decisions")
STEP_FIELDS = ("gp", "availability", "pickups", "lineup2", "unused", "perf2")


def step_values(w):
    """Per-game value of each step for one team-week (or season total): higher = better for the team."""
    gp = max(float(w["gp"]), 1.0)
    v = {"who": w["availability"] / gp, "pickups": w["pickups"] / gp, "lineup": w["lineup2"] / gp,
         "unused": w["unused"] / gp, "perf": w["perf2"] / gp}
    v["luck"] = v["who"] + v["perf"]
    v["decisions"] = v["pickups"] + v["lineup"] + v["unused"]
    return v


def step_history(seasons_weeks):
    """Per-game step values of every finished team-week (or team-season) on record: {step: sorted list}."""
    import math
    ok = lambda v: v is not None and not (isinstance(v, float) and math.isnan(v))
    h = {k: [] for k in STEPS}
    for w in seasons_weeks:
        if not all(ok(w.get(k)) for k in STEP_FIELDS) or w["gp"] <= 0:
            continue
        for k, v in step_values(w).items():
            h[k].append(v)
    return {k: sorted(v) for k, v in h.items()}


def step_percentiles(w, hist):
    return {k: percentile(hist[k], v) for k, v in step_values(w).items()}


def percentile(sorted_vals, v):
    if not sorted_vals or v is None or v != v:
        return None
    import bisect
    lo, hi = bisect.bisect_left(sorted_vals, v), bisect.bisect_right(sorted_vals, v)
    return round((lo + hi) / 2 / len(sorted_vals), 3)


def top_players(players, n=3):
    fmt = lambda df: [{"id": int(r.player_id), "name": r.name, "games": int(r.games), "pts": float(r.pts), "exp": float(r.exp),
                       "lvl": float(r.lvl), "diff": float(r.diff)} for r in df.itertuples()]
    return fmt(players.nlargest(n, "diff")), fmt(players.nsmallest(n, "diff"))


def expected_season(season, t, model, seed=11, game_mu=None, cv=None, repl_by_mp=None):
    """Completed team-weeks of a season -> (week rows DataFrame, player-season DataFrame).
    game_mu: per-game pre-game projections (betting lines) where logged; {} = model only, with the
    detail lists (who missed games, moves, bench, every player) still produced."""
    rng = np.random.default_rng(seed)
    ctx = SeasonContext(season, t, model)
    caps = t["caps"].set_index("mp")
    m = t["matchups"]
    m = m[m.result.notna()]
    rows, plist = [], []
    for r in m.itertuples():
        c = caps.loc[r.mp]
        days = list(range(int(c.sp_start), int(c.sp_end) + 1))
        res = week_breakdown(ctx, r.team_id, r.mp, days, int(c.cap), rng, game_mu=game_mu, cv=cv,
                             repl=(repl_by_mp or {}).get(int(r.mp)))
        if res is None:
            continue
        out, players = res
        if "players" in out:  # keep the season files small
            out["players"] = [{k: p[k] for k in ("id", "name", "games", "pts", "proj", "bench_games", "bench_pts")} for p in out["players"]]
        out["hot"], out["cold"] = top_players(players)
        rows.append(out)
        plist.append(players.assign(team_id=r.team_id, mp=r.mp))
    w = pd.DataFrame(rows)
    if w.empty:
        return w, pd.DataFrame()

    # expected record: decide each matchup on E4 (the games each team used, at every player's true
    # season level), so only week-to-week hot/cold swings are removed
    opp = m.set_index(["mp", "team_id"]).opp_id
    e4 = w.set_index(["mp", "team_id"]).e4
    w["opp_e4"] = [e4.get((mp, opp.get((mp, tid)))) for mp, tid in zip(w.mp, w.team_id)]
    w["x_result"] = np.where(w.opp_e4.isna(), None, np.where(w.e4 > w.opp_e4, "W", np.where(w.e4 < w.opp_e4, "L", "T")))
    res = m.set_index(["mp", "team_id"]).result
    w["result"] = [res.get((mp, tid)) for mp, tid in zip(w.mp, w.team_id)]
    w["flipped"] = w.x_result.notna() & w.result.isin(["W", "L"]) & (w.x_result != w.result)

    P = pd.concat(plist, ignore_index=True)
    players = (P.groupby(["team_id", "player_id", "name"])
               .agg(games=("games", "sum"), pts=("pts", "sum"), exp=("exp", "sum"), lvl=("lvl", "sum")).reset_index())
    players["breakout"] = players.lvl - players.exp
    players["diff"] = players.pts - players.lvl
    return w, players


if __name__ == "__main__":
    import time
    from normalize import cached_seasons, load_season
    from projections import HistoryModel

    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    t0 = time.time()
    tables = {s: load_season(s) for s in cached_seasons()}
    w, players = expected_season(season, tables[season], HistoryModel(tables))
    reg = w[w.mp.isin(tables[season]["matchups"].query("~playoff").mp)]
    print(f"{season}: {len(w)} team-weeks in {time.time() - t0:.0f}s")
    print(f"mean per team-week: E0 {reg.e0.mean():.0f}, actual {reg.actual.mean():.0f}, "
          f"breakouts {reg.breakouts.mean():+.1f}, hot/cold {reg.hot_cold.mean():+.1f}, flipped {int(reg.flipped.sum())}")
    print(reg.groupby("team_id")[["availability", "moves", "lineup", "breakouts", "hot_cold"]].sum().round(0))
