"""Teammate usage bumps: how much a player's fantasy output changes when a key teammate sits.

For every team-season, a "key" player K (24+ min or 30+ FP per game) and each teammate T:
    raw bump = T's FP per game with K out  -  T's FP per game with K playing
using only games while both were on that team (traded players are handled by date ranges).
Small samples are shrunk toward a position prior: a same-position backup typically gains more
than a player at another position, scaled by how productive K is.

    bump(T|K) = w * raw + (1 - w) * prior(T, K),   w = n_out / (n_out + kappa)

Usage: python pipeline/usage.py   (fits + out-of-sample validation report)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nba_logs  # noqa: E402

KEY_MIN, KEY_FP, MIN_GAMES = 24.0, 30.0, 10


def pos_group(pos):
    p = (pos or "").upper()
    return "C" if "C" in p else ("F" if "F" in p else "G")


def player_games(seasons):
    """One row per player-game: season, athlete, team, event, date, min, fp, pos group."""
    rows = []
    for s in seasons:
        for aid, a in nba_logs.load(s).items():
            for ev, day, team, mins, fp in a["games"]:
                rows.append((s, int(aid), a.get("name"), pos_group(a.get("pos")), int(team), str(ev), day, mins, fp))
    return pd.DataFrame(rows, columns=["season", "aid", "name", "grp", "team", "event", "date", "min", "fp"])


def team_games(pg):
    """(season, team) -> sorted [(date, event)] the team played."""
    tg = pg.drop_duplicates(["season", "team", "event"])[["season", "team", "event", "date"]]
    return {k: sorted(zip(g.date, g.event)) for k, g in tg.groupby(["season", "team"])}


def spans(pg):
    """(season, aid, team) -> (first date, last date) the player appeared for that team."""
    return {k: (g.date.min(), g.date.max()) for k, g in pg.groupby(["season", "aid", "team"])}


def key_players(pg, before=None):
    q = pg if before is None else pg[pg.date < before]
    agg = q.groupby(["season", "team", "aid"]).agg(n=("fp", "size"), mins=("min", "mean"), fp=("fp", "mean"), grp=("grp", "first"))
    return agg[(agg.n >= MIN_GAMES) & ((agg.mins >= KEY_MIN) | (agg.fp >= KEY_FP))]


def fit_pairs(pg, before=None, season_weights=None):
    """Raw with/without splits for every (team, K, T) pair, using games before `before`."""
    season_weights = season_weights or {}
    q = pg if before is None else pg[pg.date < before]
    keys = key_players(pg, before)
    tg, sp = team_games(q), spans(q)
    played = {(r.season, r.aid, r.event) for r in q.itertuples()}
    out = []
    for (s, team), K in keys.groupby(level=["season", "team"]):
        games = tg.get((s, team), [])
        mates = q[(q.season == s) & (q.team == team)]
        for (_, _, k_aid), krow in K.iterrows():
            k0, k1 = sp[(s, k_aid, team)]
            # games while K was on the team; did he play?
            k_games = {ev: ((s, k_aid, ev) in played) for day, ev in games if k0 <= day <= k1}
            for t_aid, tg_rows in mates[mates.aid != k_aid].groupby("aid"):
                tg_rows = tg_rows[tg_rows.event.isin(k_games)]
                if len(tg_rows) < 5:
                    continue
                k_in = tg_rows.event.map(k_games)
                fin, fout = tg_rows.fp[k_in], tg_rows.fp[~k_in]
                if len(fin) < 3:
                    continue
                out.append({"season": s, "team": team, "k": k_aid, "t": t_aid, "k_fp": krow.fp, "k_grp": krow.grp,
                            "t_grp": tg_rows.grp.iloc[0], "t_name": tg_rows.name.iloc[0], "t_min_in": tg_rows["min"][k_in].mean(),
                            "n_in": len(fin), "n_out": len(fout), "fp_in": fin.mean(),
                            "raw": (fout.mean() - fin.mean()) if len(fout) else 0.0,
                            "min_out": tg_rows["min"][~k_in].mean() if len(fout) else np.nan,
                            "w_season": season_weights.get(s, 1.0)})
    return pd.DataFrame(out)


def fit_prior(pairs):
    """raw ~ k_fp * (a_same * same_group + a_other * other), weighted by games without K."""
    p = pairs[pairs.n_out > 0]
    same = (p.k_grp == p.t_grp).to_numpy(float)
    k_fp = p.k_fp.to_numpy(float)
    X = np.column_stack([k_fp * same, k_fp * (1 - same)])
    w = np.sqrt((p.n_out * p.w_season).to_numpy(float))
    coef, *_ = np.linalg.lstsq(X * w[:, None], p.raw.to_numpy(float) * w, rcond=None)
    return {"same": float(coef[0]), "other": float(coef[1])}


def shrunk(pairs, prior, kappa):
    same = pairs.k_grp == pairs.t_grp
    pri = pairs.k_fp * np.where(same, prior["same"], prior["other"])
    n = pairs.n_out * pairs.w_season
    w = n / (n + kappa)
    return pairs.assign(prior=pri, bump=w * pairs.raw + (1 - w) * pri)


def validate(season=2026, windows=("2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01"), kappas=(2, 4, 8, 16, 32)):
    """Refit at the start of each month, predict that month's games where a key teammate sat."""
    pg = player_games([season])
    tg = team_games(pg)
    res = {k: [] for k in ("none", "flat", *[f"pair_k{k}" for k in kappas], "prior_only")}
    examples = []
    for start, end in zip(windows[:-1], windows[1:]):
        pairs = fit_pairs(pg, before=start)
        if pairs.empty:
            continue
        prior = fit_prior(pairs)
        keys = key_players(pg, before=start)
        sp = spans(pg[pg.date < start])
        played = {(r.aid, r.event) for r in pg.itertuples()}
        test = pg[(pg.date >= start) & (pg.date < end)]
        hist = pg[pg.date < start]
        mean_all = hist.groupby("aid").fp.mean()
        by_pair = {k: shrunk(pairs, prior, k).set_index(["team", "k", "t"]) for k in kappas}
        base_in = pairs.set_index(["team", "k", "t"])
        for r in test.itertuples():
            if r.aid not in mean_all.index:
                continue
            team_keys = [k for (_, team, k) in keys.index if team == r.team and k != r.aid]
            out_keys = [k for k in team_keys if (k, r.event) not in played and (season, k, r.team) in sp]
            if not out_keys:
                continue
            m = mean_all[r.aid]
            res["none"].append((r.fp - m) ** 2)
            k_fps = [keys.loc[(season, r.team, k), "fp"] for k in out_keys]
            res["flat"].append((r.fp - m * (1 + 0.05 * (max(k_fps) >= 45) + 0.03 * (30 <= max(k_fps) < 45))) ** 2)
            pr = sum(keys.loc[(season, r.team, k), "fp"] * (prior["same"] if keys.loc[(season, r.team, k), "grp"] == r.grp else prior["other"]) for k in out_keys)
            res["prior_only"].append((r.fp - (m + pr)) ** 2)
            for kap, tbl in by_pair.items():
                bumps = [tbl.loc[(r.team, k, r.aid), "bump"] for k in out_keys if (r.team, k, r.aid) in tbl.index]
                base = base_in.loc[(r.team, out_keys[0], r.aid), "fp_in"] if (r.team, out_keys[0], r.aid) in base_in.index else m
                pred = (base + sum(bumps)) if bumps else (m + pr)
                res[f"pair_k{kap}"].append((r.fp - pred) ** 2)
        if start == windows[-2]:
            tbl = by_pair[8].reset_index()
            examples = tbl[(tbl.n_out >= 5)].nlargest(8, "bump")
    rmse = {k: float(np.sqrt(np.mean(v))) for k, v in res.items() if v}
    return rmse, len(res["none"]), prior, examples


if __name__ == "__main__":
    rmse, n, prior, ex = validate()
    print(f"out-of-sample (2025-26, refit monthly), {n:,} player-games where a key teammate sat:")
    for k, v in sorted(rmse.items(), key=lambda x: x[1]):
        print(f"   {k:12} RMSE {v:6.2f}")
    print("prior (FP gained per 1 FP/game of the missing teammate):", {k: round(v, 3) for k, v in prior.items()})
    if len(ex):
        names = {int(a): x["name"] for a, x in nba_logs.load(2026).items()}
        print("largest shrunk bumps (fit through March):")
        for r in ex.itertuples():
            print(f"   {r.t_name:24} when {names.get(r.k, r.k):24} sits: {r.fp_in:5.1f} -> +{r.bump:4.1f} FP (raw +{r.raw:4.1f}, {r.n_out} games, min {r.t_min_in:4.1f}->{r.min_out:4.1f})")
