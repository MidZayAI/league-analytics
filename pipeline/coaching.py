"""Coaching Rating: actual points vs the hindsight-optimal lineup under the weekly games cap.

For each team-week we solve a small integer program over every player-day where the player
actually played and was on the roster that day:
  maximize   sum(points * x)
  subject to each player-day fills at most one starting slot it is eligible for,
             each slot holds at most `count` players per day,
             total games started <= that week's cap (hard stop, per the league's agreement).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))


def optimal_lineup(rows, slots, cap=None, value=None):
    """rows: DataFrame of player-days (sp, player_id, pts, elig). Returns (score, games, chosen index set).

    value overrides the objective (e.g. all ones to find the max games a roster could fill).
    """
    if rows.empty:
        return 0.0, 0, set()
    obj = rows.pts.to_numpy() if value is None else np.asarray(value, dtype=float)
    var_row, var_slot = [], []
    for i, elig in enumerate(rows.elig):
        for s in slots:
            if s in elig:
                var_row.append(i)
                var_slot.append(s)
    n = len(var_row)
    if n == 0:
        return 0.0, 0, set()
    var_row = np.array(var_row)
    var_slot = np.array(var_slot)
    days = rows.sp.to_numpy()[var_row]

    cons_r, cons_c, ub = [], [], []
    r = 0
    # one slot per player-day
    for i in np.unique(var_row):
        idx = np.nonzero(var_row == i)[0]
        cons_r += [r] * len(idx); cons_c += list(idx); ub.append(1); r += 1
    # slot capacity per day
    keys = days.astype(np.int64) * 100 + var_slot
    for k in np.unique(keys):
        idx = np.nonzero(keys == k)[0]
        cons_r += [r] * len(idx); cons_c += list(idx); ub.append(slots[int(k % 100)]); r += 1
    # weekly games cap
    if cap is not None and not pd.isna(cap):
        cons_r += [r] * n; cons_c += list(range(n)); ub.append(int(cap)); r += 1

    A = coo_matrix((np.ones(len(cons_r)), (cons_r, cons_c)), shape=(r, n)).tocsr()
    res = milp(c=-obj[var_row], constraints=LinearConstraint(A, -np.inf, np.array(ub, dtype=float)),
               integrality=np.ones(n), bounds=Bounds(0, 1))
    if res.x is None:
        return 0.0, 0, set()
    chosen = var_row[res.x > 0.5]
    return float(rows.pts.to_numpy()[chosen].sum()), int(len(chosen)), set(rows.index[chosen])


def coach_season(t):
    """One row per team-week with optimal points, efficiency and the biggest missed starts."""
    daily, m, slots = t["daily"], t["matchups"], t["slots"]
    played = daily[daily.gp > 0]
    out = []
    for r in m.itertuples():
        rows = played[(played.mp == r.mp) & (played.team_id == r.team_id)]
        opt_pts, opt_gp, chosen = optimal_lineup(rows, slots, r.cap)
        _, max_games, _ = optimal_lineup(rows, slots, None, value=np.ones(len(rows)))
        actual = float(r.pts or 0.0)
        over_cap = bool(r.cap and r.gp and r.gp > r.cap)
        missed = rows.loc[[i for i in chosen if not rows.at[i, "counted"]]].nlargest(3, "pts")
        out.append({
            "season": r.season, "mp": r.mp, "team_id": r.team_id, "playoff": r.playoff,
            "final": r.result is not None,
            "actual_pts": actual, "actual_gp": r.gp, "cap": r.cap,
            "opt_pts": opt_pts, "opt_gp": opt_gp, "max_games": max_games,
            "efficiency": min(actual / opt_pts, 1.0) if opt_pts > 0 else None,
            "bench_lost": max(opt_pts - actual, 0.0),
            "over_cap": over_cap,
            # would the optimal lineup have flipped a loss?
            "coaching_loss": r.result == "L" and r.opp_pts is not None and opt_pts > r.opp_pts,
            "missed_starts": [{"name": x.name, "sp": int(x.sp), "pts": float(x.pts)} for x in missed.itertuples()],
        })
    return pd.DataFrame(out)


if __name__ == "__main__":
    import time
    from normalize import load_season

    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    t0 = time.time()
    c = coach_season(load_season(season, rebuild=True))
    print(f"{season}: {len(c)} team-weeks solved in {time.time() - t0:.1f}s")
    reg = c[~c.playoff & c.final]
    s = reg.groupby("team_id").agg(actual=("actual_pts", "sum"), opt=("opt_pts", "sum"),
                                   lost=("bench_lost", "sum"), coach_L=("coaching_loss", "sum"))
    s["eff"] = (s.actual / s.opt).round(3)
    print(s.sort_values("eff", ascending=False))
