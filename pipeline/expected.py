"""Expected points: how a team should have scored in a matchup, and why it didn't.

Every team-week is split into steps that add up exactly to the actual score:
  E0  pregame expected     week-start roster, chance-to-play, cap (the predictor before day 1)
  E1  + availability       same roster, but who actually played
  E2  + roster moves       actual daily rosters (pickups/drops), who actually played
  E3  + lineup & cap       projected points of the games that actually counted
  Act + hot/cold           actual points of those games

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
        self._raw = {}

    def raw(self, day):
        if day not in self._raw:
            self._raw[day] = self.model.raw_state(self.season, day)
        return self._raw[day]


def week_breakdown(ctx, tid, mp, week_days, cap, rng, done_days=None):
    """Breakdown for one team-week. done_days limits it to the days played so far (live view);
    then E1/E2 get the same number of games the team has actually used so far."""
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

    done = list(week_days if done_days is None else done_days)
    counted = td[td.counted & td.sp.isin(done)]
    cap_eff = cap if done_days is None else int(counted.gp.sum())

    def cands(rows):
        return pd.DataFrame(rows, columns=["sp", "mu", "p", "player_id"])

    # E0: pregame, whole week; per-day expectation lets the live view take just the days played
    c0 = cands([(sp, mu[r.player_id], p[r.player_id], r.player_id)
                for r in roster0.itertuples() for sp in week_days if r.pro in ctx.games.get(sp, ())])
    _, s0 = expected_value(c0, cap, E0_SIMS, rng)
    e0 = float((c0.mu * s0)[c0.sp.isin(done)].sum()) if len(c0) else 0.0

    # E1: week-start roster with real availability (unknown after a drop to free agency -> p)
    rows = []
    for r in roster0.itertuples():
        for sp in done:
            if r.pro in ctx.games.get(sp, ()):
                gp = ctx.played.get((sp, r.player_id))
                rows.append((sp, mu[r.player_id], p[r.player_id] if gp is None else float(gp > 0), r.player_id))
    e1, _ = expected_value(cands(rows), cap_eff, E1_SIMS, rng)

    # E2: actual daily rosters, real availability, best use of the cap by projection
    have = td[td.sp.isin(done) & (td.gp > 0)].drop_duplicates(["sp", "player_id"])
    e2, _ = expected_value(cands([(r.sp, mu[r.player_id], 1.0, r.player_id) for r in have.itertuples()]),
                           cap_eff, 1, rng)

    # E3 / actual: the games that counted
    cg = counted.assign(mu=counted.player_id.map(mu))
    e3, actual = float(cg.mu.sum()), float(cg.pts.sum())
    players = cg.groupby(["player_id", "name"]).agg(games=("gp", "size"), pts=("pts", "sum"), exp=("mu", "sum")).reset_index()
    players["diff"] = players.pts - players.exp

    out = {"team_id": tid, "mp": mp, "e0": e0, "e1": e1, "e2": e2, "e3": e3, "actual": actual,
           "availability": e1 - e0, "moves": e2 - e1, "lineup": e3 - e2, "hot_cold": actual - e3,
           "days_done": len(done), "games_used": int(counted.gp.sum())}
    parts = out["e0"] + out["availability"] + out["moves"] + out["lineup"] + out["hot_cold"]
    assert abs(parts - actual) < 1e-6, (tid, mp, parts, actual)
    return out, players


def top_players(players, n=3):
    fmt = lambda df: [{"name": r.name, "games": int(r.games), "pts": float(r.pts), "exp": float(r.exp), "diff": float(r.diff)}
                      for r in df.itertuples()]
    return fmt(players.nlargest(n, "diff")), fmt(players.nsmallest(n, "diff"))


def expected_season(season, t, model, seed=11):
    """Completed team-weeks of a season -> (week rows DataFrame, player-season DataFrame)."""
    rng = np.random.default_rng(seed)
    ctx = SeasonContext(season, t, model)
    caps = t["caps"].set_index("mp")
    m = t["matchups"]
    m = m[m.result.notna()]
    rows, plist = [], []
    for r in m.itertuples():
        c = caps.loc[r.mp]
        days = list(range(int(c.sp_start), int(c.sp_end) + 1))
        res = week_breakdown(ctx, r.team_id, r.mp, days, int(c.cap), rng)
        if res is None:
            continue
        out, players = res
        out["hot"], out["cold"] = top_players(players)
        rows.append(out)
        plist.append(players.assign(team_id=r.team_id, mp=r.mp))
    w = pd.DataFrame(rows)
    if w.empty:
        return w, pd.DataFrame()

    # expected record: decide each matchup on expected points (E3 vs the opponent's E3)
    opp = m.set_index(["mp", "team_id"]).opp_id
    e3 = w.set_index(["mp", "team_id"]).e3
    w["opp_e3"] = [e3.get((mp, opp.get((mp, tid)))) for mp, tid in zip(w.mp, w.team_id)]
    w["x_result"] = np.where(w.opp_e3.isna(), None, np.where(w.e3 > w.opp_e3, "W", np.where(w.e3 < w.opp_e3, "L", "T")))
    res = m.set_index(["mp", "team_id"]).result
    w["result"] = [res.get((mp, tid)) for mp, tid in zip(w.mp, w.team_id)]
    w["flipped"] = w.x_result.notna() & w.result.isin(["W", "L"]) & (w.x_result != w.result)

    P = pd.concat(plist, ignore_index=True)
    players = (P.groupby(["team_id", "player_id", "name"])
               .agg(games=("games", "sum"), pts=("pts", "sum"), exp=("exp", "sum")).reset_index())
    players["diff"] = players.pts - players.exp
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
          f"hot/cold {reg.hot_cold.mean():+.1f} (bias check, should be ~0), flipped {int(reg.flipped.sum())}")
    print(reg.groupby("team_id")[["availability", "moves", "lineup", "hot_cold"]].sum().round(0))
