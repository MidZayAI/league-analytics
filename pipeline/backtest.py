"""Backtest the matchup predictor on completed seasons, tune its parameters, report calibration.

Usage: python pipeline/backtest.py            # tune + write site/data/backtest.json, pipeline/model_params.json
       python pipeline/backtest.py --no-tune  # evaluate current params only

For every regular-season matchup and every day of it, we forecast from the start of that day using
only information known then (scores so far, games used, that day's rosters, player history), and
compare with the real result.
"""
import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import projections  # noqa: E402
from normalize import cached_seasons, load_season  # noqa: E402
from simulate import simulate_team, win_prob  # noqa: E402
from espn_client import ROOT  # noqa: E402

PARAMS_FILE = Path(__file__).resolve().parent / "model_params.json"
OUT = ROOT / "site" / "data" / "backtest.json"
# First pass (k 5-20, r 0-0.5) favored the most shrinkage and no recency blend; this grid extends it.
GRID = {"k": [20.0, 40.0, 80.0], "r": [0.0], "p_base": [0.80], "p_k": [4.0], "team_sd": [0.0, 0.08, 0.15]}


def team_candidates(roster, est, days, games):
    rows = []
    for r in roster.itertuples():
        e = est.loc[r.player_id]
        for sp in days:
            if r.pro in games.get(sp, ()):
                rows.append((sp, e.mu, e.sd, e.p))
    return pd.DataFrame(rows, columns=["sp", "mu", "sd", "p"])


def prepare(tables, model, seasons):
    """Everything param-independent: per (matchup, day) the two teams' state and rosters."""
    states = []
    for s in seasons:
        t = tables[s]
        caps = t["caps"].set_index("mp")
        games = t["pro_games"]
        daily = {k: g for k, g in t["daily"].groupby(["team_id", "mp"])}
        m = t["matchups"]
        reg = m[~m.playoff & m.result.notna() & m.opp_id.notna()]
        raw_cache = {}
        for (mp, mid), pair in reg.groupby(["mp", "matchup_id"]):
            if len(pair) != 2:
                continue
            c = caps.loc[mp]
            days = list(range(int(c.sp_start), int(c.sp_end) + 1))
            a, b = pair.iloc[0], pair.iloc[1]
            for di, day in enumerate(days):
                if day not in raw_cache:
                    raw_cache[day] = model.raw_state(s, day)
                side = []
                for row in (a, b):
                    td = daily.get((row.team_id, mp))
                    done = td[(td.sp < day) & td.counted]
                    roster = td[td.sp == day]
                    if roster.empty:
                        roster = td[td.sp == td[td.sp <= day].sp.max()]
                    side.append({"cur": float(done.pts.sum()), "rem_cap": int(c.cap - done.gp.sum()),
                                 "roster": roster[["player_id", "pro"]].drop_duplicates("player_id")})
                states.append({"season": s, "mp": mp, "day_idx": di, "n_days": len(days),
                               "team_a": int(a.team_id), "team_b": int(b.team_id),
                               "y": {"W": 1.0, "L": 0.0, "T": 0.5}[a.result],
                               "raw": raw_cache[day], "days": days[di:], "games": games, "side": side})
    return states


def evaluate(states, model, params, sims=1000, seed=7):
    rng = np.random.default_rng(seed)
    out = []
    for st in states:
        res = []
        for sd in st["side"]:
            est = model.finish(st["raw"], sd["roster"].player_id.tolist(), params)
            cands = team_candidates(sd["roster"], est, st["days"], st["games"])
            pts, _, _ = simulate_team(cands, sd["rem_cap"], sims, rng, params.get("team_sd", 0.0))
            res.append((sd["cur"], pts))
        p = win_prob(res[0][0], res[0][1], res[1][0], res[1][1])
        out.append({k: st[k] for k in ("season", "mp", "day_idx", "n_days", "team_a", "team_b", "y")} | {"p": p})
    return pd.DataFrame(out)


def score(df):
    p = df.p.clip(1e-3, 1 - 1e-3)
    return {"brier": float(((df.p - df.y) ** 2).mean()),
            "logloss": float(-(df.y * np.log(p) + (1 - df.y) * np.log(1 - p)).mean())}


def report(df, params, model):
    both = pd.concat([df[["p", "y"]], pd.DataFrame({"p": 1 - df.p, "y": 1 - df.y})])
    bins = np.linspace(0, 1, 11)
    both["bin"] = pd.cut(both.p, bins, include_lowest=True)
    cal = both.groupby("bin", observed=True).agg(p_mean=("p", "mean"), won=("y", "mean"), n=("y", "size")).reset_index()
    by_day = df.groupby("day_idx").apply(lambda g: pd.Series(score(g) | {"n": len(g)}), include_groups=False).reset_index()
    pre = df[df.day_idx == 0]
    fav = pre[(pre.p - 0.5).abs() > 0.001]
    pregame = []
    for r in pre.itertuples():
        pregame.append({"season": r.season, "mp": r.mp, "team_id": r.team_a, "opp_id": r.team_b, "p": r.p, "won": r.y})
        pregame.append({"season": r.season, "mp": r.mp, "team_id": r.team_b, "opp_id": r.team_a, "p": 1 - r.p, "won": 1 - r.y})
    return {
        "params": params, "cv": model.cv, "seasons": sorted(df.season.unique().tolist()),
        "n_forecasts": len(df), "n_matchups": len(pre),
        **score(df), "brier_coinflip": 0.25,
        "pregame": score(pre) | {"favorite_won": float(((fav.p > 0.5) == (fav.y > 0.5)).mean())},
        "by_day": by_day.to_dict("records"),
        "calibration": [{"p_mean": r.p_mean, "won": r.won, "n": int(r.n)} for r in cal.itertuples()],
        "pregame_probs": pregame,
    }


def main():
    t0 = time.time()
    seasons = cached_seasons()
    tables = {s: load_season(s) for s in seasons}
    model = projections.HistoryModel(tables)
    bt = [s for s in seasons if s - 1 in tables and tables[s]["complete"]]
    states = prepare(tables, model, bt)
    print(f"{len(states)} forecasts across {bt} prepared in {time.time() - t0:.0f}s")

    best = dict(projections.PARAMS)
    if "--no-tune" not in sys.argv:
        results = []
        for vals in itertools.product(*GRID.values()):
            params = dict(zip(GRID, vals))
            s = score(evaluate(states, model, params, sims=600))
            results.append((s["brier"], params))
            print(f"  {params}  brier {s['brier']:.4f}  logloss {s['logloss']:.4f}")
        best = min(results, key=lambda x: x[0])[1]
        PARAMS_FILE.write_text(json.dumps(best, indent=2), encoding="utf-8")

    df = evaluate(states, model, best, sims=3000)
    rep = report(df, best, model)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, default=float, separators=(",", ":")), encoding="utf-8")
    print(f"best {best}: brier {rep['brier']:.4f} (coin flip 0.25), pregame brier {rep['pregame']['brier']:.4f}, "
          f"favorite won {rep['pregame']['favorite_won']:.1%}  [{time.time() - t0:.0f}s]")
    for r in rep["calibration"]:
        print(f"   predicted {r['p_mean']:.2f} -> won {r['won']:.2f}  (n={r['n']})")


if __name__ == "__main__":
    main()
