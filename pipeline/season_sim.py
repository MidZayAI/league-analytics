"""Season simulator: playoff, bye and championship odds.

Each remaining game is played with team scores drawn around each team's expected score for that
week (current roster, player projections, NBA schedule and that week's cap):
    score = expected * strength * (1 + week_cv * noise)
where strength is a per-team random walk over the weeks ahead (trades, waivers, injuries make
far-off weeks less certain). The current week can instead use the live simulation's draws, so
season odds agree with the matchup odds. Standings follow the league rules: wins, then
head-to-head record among tied teams, then points for. Then the bracket is played out.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from expected import expected_value  # noqa: E402
from normalize import RAW  # noqa: E402

# odds_shrink pulls each period's expected scores toward that period's league mean (roster
# projections overstate real gaps between teams); tuned by odds_backtest.py
DEFAULTS = {"week_cv": 0.10, "odds_drift": 0.02, "odds_shrink": 1.0}
# a "what if" needs enough simulations on both sides; nearly decided games are reported as decided
MIN_BRANCH = 0.02


def league_format(season, t):
    """Regular-season matchup periods, playoff round periods, playoff team count."""
    settings = json.loads((RAW / str(season) / "league.json").read_text(encoding="utf-8"))["settings"]
    n_reg = settings["scheduleSettings"]["matchupPeriodCount"]
    n_po = settings["scheduleSettings"].get("playoffTeamCount") or t["playoff_teams"]
    all_mps = sorted(int(m) for m in settings["scheduleSettings"]["matchupPeriods"])
    reg = [m for m in all_mps if m <= n_reg]
    rounds = [m for m in all_mps if m > n_reg][: (2 if n_po <= 4 else 3)]
    return reg, rounds, n_po


def expected_scores(players, weeks, caps, games, sp_now, rng, play_fn):
    """players: team_id -> list of (pro, mu, base_p, inj). Returns {(team_id, mp): expected score}.
    play_fn(inj, days_ahead, base) gives the chance to play on a given day."""
    out = {}
    for tid, plist in players.items():
        for mp in weeks:
            c = caps.loc[mp]
            rows = []
            for sp in range(int(c.sp_start), int(c.sp_end) + 1):
                day_games = games.get(sp, ())
                for pro, mu, base, inj in plist:
                    if pro in day_games:
                        rows.append((sp, max(mu, 1.0), play_fn(inj, sp - sp_now, base), 0))
            cands = pd.DataFrame(rows, columns=["sp", "mu", "p", "player_id"])
            out[(tid, mp)] = expected_value(cands, int(c.cap), 300, rng)[0]
    return out


def simulate(teams, done, future_games, exp, rounds, n_playoff, params, sims, rng, current=None):
    """teams: list of team ids. done: completed games [(a, b, pts_a, pts_b)].
    future_games: [(mp, a, b)] still to play (regular season), in week order.
    exp: {(team, mp): expected score} for future + playoff periods.
    current: optional {"mp": mp, "draws": {team: array[sims]}} for the week in progress.
    Returns per-sim arrays: seeds [sims, n] (team index at each seed), champion, finalist, wins, etc."""
    n = len(teams)
    idx = {t: i for i, t in enumerate(teams)}
    cv, drift = params["week_cv"], params["odds_drift"]
    lam = params.get("odds_shrink", 1.0)
    mean = {}
    for (_, mp), v in exp.items():
        mean.setdefault(mp, []).append(v)
    mean = {mp: float(np.mean(v)) for mp, v in mean.items()}
    exp = {(tm, mp): mean[mp] + lam * (v - mean[mp]) for (tm, mp), v in exp.items()}

    wins = np.zeros((sims, n))
    pf = np.zeros((sims, n))
    h2h = np.zeros((sims, n, n))  # h2h[s, i, j] = wins of i over j
    for a, b, pa, pb in done:
        i, j = idx[a], idx[b]
        pf[:, i] += pa
        pf[:, j] += pb
        if pa != pb:
            w, l = (i, j) if pa > pb else (j, i)
            wins[:, w] += 1
            h2h[:, w, l] += 1

    # team-strength random walk across the periods still to come
    periods = sorted({mp for mp, _, _ in future_games} | set(rounds))
    if current:
        periods = [p for p in periods if p != current["mp"]]
    step = {mp: k for k, mp in enumerate(periods)}
    walk = 1 + drift * np.cumsum(rng.standard_normal((sims, n, max(1, len(periods)))), axis=2)
    walk = np.maximum(walk, 0.5)

    def score(team, mp):
        i = idx[team]
        if current and mp == current["mp"]:
            return current["draws"][team]
        e = exp.get((team, mp), 0.0)
        return e * walk[:, i, step[mp]] * (1 + cv * rng.standard_normal(sims))

    cur_win = {}
    for mp, a, b in future_games:
        sa, sb = score(a, mp), score(b, mp)
        i, j = idx[a], idx[b]
        a_wins = sa > sb
        wins[:, i] += a_wins
        wins[:, j] += ~a_wins
        h2h[:, i, j] += a_wins
        h2h[:, j, i] += ~a_wins
        pf[:, i] += sa
        pf[:, j] += sb
        if current and mp == current["mp"]:
            cur_win[(a, b)] = a_wins

    # standings: wins, then head-to-head among teams tied on wins, then points for
    same = wins[:, :, None] == wins[:, None, :]
    tie_w = (h2h * same).sum(axis=2)
    tie_g = ((h2h + np.transpose(h2h, (0, 2, 1))) * same).sum(axis=2)
    tie_pct = np.where(tie_g > 0, tie_w / np.maximum(tie_g, 1), 0.5)
    key = wins * 1e3 + tie_pct * 10 + pf / (pf.max() + 1)
    seeds = np.argsort(-key, axis=1)  # seeds[s, k] = team index at seed k+1

    # bracket (team indices); ties in a playoff game go to the higher seed
    def play(hi, lo, mp):
        s_hi = np.stack([score(teams[i], mp) for i in range(n)], axis=1)
        a = s_hi[np.arange(sims), hi]
        b = s_hi[np.arange(sims), lo]
        return np.where(a >= b, hi, lo), np.where(a >= b, lo, hi)

    S = lambda k: seeds[:, k - 1]
    if n_playoff >= 6 and len(rounds) >= 3:
        w36, _ = play(S(3), S(6), rounds[0])
        w45, _ = play(S(4), S(5), rounds[0])
        f1, _ = play(S(1), w45, rounds[1])
        f2, _ = play(S(2), w36, rounds[1])
        champ, runner = play(f1, f2, rounds[2])
    else:
        f1, _ = play(S(1), S(4), rounds[0])
        f2, _ = play(S(2), S(3), rounds[0])
        champ, runner = play(f1, f2, rounds[-1])
    return {"seeds": seeds, "wins": wins, "champ": champ, "finalists": (f1, f2), "cur_win": cur_win}


def summarize(teams, out, n_playoff, done_wins, current_games=()):
    """Per-team odds, seed grid, projected wins, stakes for this week's game, rooting guide."""
    n = len(teams)
    seeds, wins = out["seeds"], out["wins"]
    sims = seeds.shape[0]
    seed_of = np.empty_like(seeds)
    seed_of[np.arange(sims)[:, None], seeds] = np.arange(n)[None, :]  # seed_of[s, i] = 0-based seed
    made = seed_of < n_playoff
    bye = seed_of < 2 if n_playoff >= 6 else np.zeros_like(made)
    champ = out["champ"][:, None] == np.arange(n)[None, :]
    final = (out["finalists"][0][:, None] == np.arange(n)) | (out["finalists"][1][:, None] == np.arange(n))

    res = []
    for i, t in enumerate(teams):
        r = {"team_id": int(t), "playoff": float(made[:, i].mean()), "bye": float(bye[:, i].mean()),
             "final": float(final[:, i].mean()), "title": float(champ[:, i].mean()),
             "seeds": [float((seed_of[:, i] == k).mean()) for k in range(n)],
             "proj_w": float(wins[:, i].mean()), "w_lo": float(np.percentile(wins[:, i], 10)),
             "w_hi": float(np.percentile(wins[:, i], 90))}
        res.append(r)

    # stakes + rooting from the week in progress
    idx = {t: i for i, t in enumerate(teams)}
    games = list(out["cur_win"].items())
    for (a, b), a_wins in games:
        for me, my_win in ((a, a_wins), (b, ~a_wins)):
            i = idx[me]
            p = my_win.mean()
            open_game = MIN_BRANCH <= p <= 1 - MIN_BRANCH
            cond = lambda arr, m: float(arr[m, i].mean()) if open_game else None
            res[i]["stakes"] = {"p_win": float(p),
                                "playoff_w": cond(made, my_win), "playoff_l": cond(made, ~my_win),
                                "bye_w": cond(bye, my_win), "bye_l": cond(bye, ~my_win),
                                "title_w": cond(champ, my_win), "title_l": cond(champ, ~my_win)}
    for i, t in enumerate(teams):
        guide = []
        for (a, b), a_wins in games:
            if t in (a, b) or not (MIN_BRANCH <= a_wins.mean() <= 1 - MIN_BRANCH):
                continue
            d_po = made[a_wins, i].mean() - made[~a_wins, i].mean()
            d_ti = champ[a_wins, i].mean() - champ[~a_wins, i].mean()
            guide.append({"a": int(a), "b": int(b), "root_for": int(a if d_po + d_ti >= 0 else b),
                          "playoff_swing": float(abs(d_po)), "title_swing": float(abs(d_ti))})
        res[i]["rooting"] = sorted(guide, key=lambda g: -(g["playoff_swing"] + g["title_swing"]))
    return res


def clinch_flags(teams, wins_now, games_left, n_playoff):
    """Exact (conservative) clinch/elimination: ties count against the team."""
    out = {}
    for t in teams:
        lo, hi = wins_now[t], wins_now[t] + games_left[t]
        others = [u for u in teams if u != t]
        can_catch = sum(1 for u in others if wins_now[u] + games_left[u] >= lo)
        ahead_for_sure = sum(1 for u in others if wins_now[u] > hi)
        out[t] = {"clinched_playoffs": can_catch <= n_playoff - 1,
                  "clinched_bye": n_playoff >= 6 and can_catch <= 1,
                  "eliminated": ahead_for_sure >= n_playoff}
    return out
