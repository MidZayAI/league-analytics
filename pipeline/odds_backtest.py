"""Backtest playoff / bye / title odds on completed seasons and tune the season-drift parameter.

Usage: python pipeline/odds_backtest.py
Writes site/data/odds_backtest.json (accuracy + weekly odds per past season) and adds
week_cv / odds_drift to pipeline/model_params.json.

From the start of every regular-season week we forecast with only what was known then
(results so far, that day's rosters, player history) and compare with what happened.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import season_sim as ss  # noqa: E402
from espn_client import ROOT  # noqa: E402
from normalize import TIDY, cached_seasons, load_season  # noqa: E402
from projections import HistoryModel  # noqa: E402

PARAMS_FILE = Path(__file__).resolve().parent / "model_params.json"
OUT = ROOT / "site" / "data" / "odds_backtest.json"
DRIFTS = [0.02, 0.05, 0.08]
SHRINKS = [0.4, 0.6, 0.8, 1.0]
SIMS = 3000


def fit_week_cv(seasons):
    """Spread of actual weekly scores around the pregame expectation (7-day regular weeks)."""
    ratios = []
    for s in seasons:
        path = TIDY / f"expected_{s}.pkl"
        if not path.exists():
            continue
        w, _ = pd.read_pickle(path)
        t = load_season(s)
        caps = t["caps"].set_index("mp")
        reg = set(t["matchups"][~t["matchups"].playoff].mp)
        w = w[w.mp.isin(reg) & (w.e0 > 0) & w.mp.map(lambda m: caps.loc[m].days <= 7)]
        ratios += list(w.actual / w.e0 - 1)
    return float(np.std(ratios)) if ratios else ss.DEFAULTS["week_cv"]


def season_games(t, reg_mps):
    m = t["matchups"]
    g = m[m.mp.isin(reg_mps) & m.opp_id.notna() & (m.team_id < m.opp_id)]
    return [(int(r.mp), int(r.team_id), int(r.opp_id), float(r.pts or 0), float(r.opp_pts or 0)) for r in g.itertuples()]


def states_for(season, t, model, rng):
    """Yield (after_week, teams, done, future, exp) for every week start of a completed season."""
    reg, rounds, n_po = ss.league_format(season, t)
    caps = t["caps"].set_index("mp")
    games = season_games(t, reg)
    teams = sorted(t["teams"].team_id.tolist())
    daily, pro_games = t["daily"], t["pro_games"]
    play_mps = sorted({g[0] for g in games})
    for w in play_mps:
        start = int(caps.loc[w].sp_start)
        raw = model.raw_state(season, start)
        day = daily[daily.sp == start]
        players = {}
        for tid in teams:
            ro = day[day.team_id == tid].drop_duplicates("player_id")
            est = model.finish(raw, ro.player_id.tolist())
            players[tid] = [(int(r.pro), float(est.at[r.player_id, "mu"]), float(est.at[r.player_id, "p"]), "ACTIVE")
                            for r in ro.itertuples()]
        future_mps = [m for m in play_mps if m >= w] + [m for m in rounds if m in caps.index]
        exp = ss.expected_scores(players, future_mps, caps, pro_games, start, rng, lambda inj, ahead, base: base)
        done = [(a, b, pa, pb) for mp, a, b, pa, pb in games if mp < w]
        future = [(mp, a, b) for mp, a, b, _, _ in games if mp >= w]
        yield w - 1, teams, done, future, exp, [m for m in rounds if m in caps.index], n_po


def outcomes(t, n_po):
    tm = t["teams"].set_index("team_id")
    return {int(i): {"playoff": float(r.seed <= n_po), "bye": float(r.seed <= 2) if n_po >= 6 else None,
                     "title": float(r.rank_final == 1)} for i, r in tm.iterrows()}


def brier(rows, key):
    rows = [r for r in rows if r["y"][key] is not None]
    return float(np.mean([(r[key] - r["y"][key]) ** 2 for r in rows])) if rows else None


def main():
    t0 = time.time()
    rng = np.random.default_rng(5)
    seasons = cached_seasons()
    tables = {s: load_season(s) for s in seasons}
    model = HistoryModel(tables)
    bt = [s for s in seasons if s - 1 in tables and tables[s]["complete"]]
    week_cv = fit_week_cv(bt)
    print(f"week-to-week cv (fitted): {week_cv:.3f}")

    states = []
    for s in bt:
        y = None
        for st in states_for(s, tables[s], model, rng):
            after, teams, done, future, exp, rounds, n_po = st
            y = y or outcomes(tables[s], n_po)
            states.append((s, after, teams, done, future, exp, rounds, n_po, y))
    print(f"{len(states)} week-start states prepared in {time.time() - t0:.0f}s")

    def run(params, equal=False):
        rows, hist = [], {}
        for s, after, teams, done, future, exp, rounds, n_po, y in states:
            e = exp
            if equal:  # baseline: every team equally strong from here on
                mean = {mp: np.mean([v for (tid, m2), v in exp.items() if m2 == mp]) for (_, mp) in exp}
                e = {(tid, mp): mean[mp] for (tid, mp) in exp}
            out = ss.simulate(teams, done, future, e, rounds, n_po, params, SIMS, rng)
            for r in ss.summarize(teams, out, n_po, None):
                rows.append({**r, "season": s, "after": after, "y": y[r["team_id"]]})
                h = hist.setdefault(str(s), {}).setdefault(str(r["team_id"]), {"after": [], "playoff": [], "bye": [], "title": []})
                h["after"].append(after); h["playoff"].append(round(r["playoff"], 4))
                h["bye"].append(round(r["bye"], 4)); h["title"].append(round(r["title"], 4))
        return rows, hist

    results = []
    for d in DRIFTS:
        for lam in SHRINKS:
            params = {"week_cv": week_cv, "odds_drift": d, "odds_shrink": lam}
            rows, _ = run(params)
            b = {k: brier(rows, k) for k in ("playoff", "bye", "title")}
            results.append((b["playoff"] + b["bye"] + b["title"], d, lam, b))
            print(f"  drift {d:<5} shrink {lam:<4} brier playoff {b['playoff']:.4f}  bye {b['bye']:.4f}  title {b['title']:.4f}")
    _, best, best_lam, _ = min(results, key=lambda r: r[0])
    params = {"week_cv": week_cv, "odds_drift": best, "odds_shrink": best_lam}
    rows, hist = run(params)
    base_rows, _ = run({"week_cv": week_cv, "odds_drift": 0.0}, equal=True)

    cal = []
    edges = np.linspace(0, 1, 11)
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = [r for r in rows if lo <= r["playoff"] < hi or (hi == 1 and r["playoff"] == 1)]
        if sel:
            cal.append({"p_mean": float(np.mean([r["playoff"] for r in sel])),
                        "made": float(np.mean([r["y"]["playoff"] for r in sel])), "n": len(sel)})
    by_week = {}
    for r in rows:
        by_week.setdefault(r["after"], []).append(r)
    report = {
        "params": params, "seasons": bt, "n_states": len(states), "sims": SIMS,
        "brier": {k: brier(rows, k) for k in ("playoff", "bye", "title")},
        "brier_baseline": {k: brier(base_rows, k) for k in ("playoff", "bye", "title")},
        "calibration": cal,
        "by_week": [{"after": w, "playoff": brier(v, "playoff"), "title": brier(v, "title"), "n": len(v)}
                    for w, v in sorted(by_week.items())],
        "history": hist,
    }
    OUT.write_text(json.dumps(report, separators=(",", ":")), encoding="utf-8")
    saved = json.loads(PARAMS_FILE.read_text(encoding="utf-8")) if PARAMS_FILE.exists() else {}
    saved.update(params)
    PARAMS_FILE.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(f"best drift {best}, shrink {best_lam}: brier {report['brier']} vs equal-strength baseline {report['brier_baseline']}  [{time.time() - t0:.0f}s]")
    for c in cal:
        print(f"   predicted {c['p_mean']:.2f} -> made playoffs {c['made']:.2f}  (n={c['n']})")


if __name__ == "__main__":
    main()
