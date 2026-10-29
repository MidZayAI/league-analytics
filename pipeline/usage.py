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


ROLE_EDGES = (18.0, 28.0)  # T's minutes: bench < 18 <= rotation < 28 <= starter


def role(minutes):
    return int(np.searchsorted(ROLE_EDGES, minutes if minutes == minutes else 0.0, side="right"))


def fit_role_prior(pairs):
    """raw ~ k_fp * a[same position?][T's role]: a bench backup at K's position gains far more than
    a starter at another position. Used for teammates with no shared history (new situations, rookies)."""
    p = pairs[pairs.n_out > 0]
    same = (p.k_grp == p.t_grp).to_numpy()
    rl = p.t_min_in.map(role).to_numpy()
    cols = [p.k_fp.to_numpy(float) * ((same == s) & (rl == r)) for s in (True, False) for r in range(3)]
    w = np.sqrt((p.n_out * p.w_season).to_numpy(float))
    coef, *_ = np.linalg.lstsq(np.column_stack(cols) * w[:, None], p.raw.to_numpy(float) * w, rcond=None)
    return {(s, r): float(coef[i]) for i, (s, r) in enumerate((s, r) for s in (True, False) for r in range(3))}


def role_bump(prior, k_fp, same, t_min):
    return k_fp * prior[(bool(same), role(t_min))]


def shrunk(pairs, prior, kappa):
    same = pairs.k_grp == pairs.t_grp
    if isinstance(next(iter(prior)), tuple):  # role prior
        pri = [role_bump(prior, k, s, m) for k, s, m in zip(pairs.k_fp, same, pairs.t_min_in)]
    else:
        pri = pairs.k_fp * np.where(same, prior["same"], prior["other"])
    n = pairs.n_out * pairs.w_season
    w = n / (n + kappa)
    return pairs.assign(prior=pri, bump=w * pairs.raw + (1 - w) * np.asarray(pri))


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


def validate_new_situations(season=2026, windows=("2025-11-15", "2025-12-15", "2026-01-15", "2026-02-15", "2026-03-15", "2026-04-15"),
                            kappa=16, scale=0.5, together_min=10):
    """Out of sample, like validate(), but judged on the games the current model can't see: a key teammate
    sits and the pair has fewer than `together_min` games together on that team (trades, signings,
    rookies, returning players). Key = 24+ min or 30+ FP this season on any team (no 10-games-here rule)."""
    pg = player_games([season])
    played = {(r.aid, r.event) for r in pg.itertuples()}
    res = {k: {"all": [], "new": []} for k in ("none", "current", "role")}
    for start, end in zip(windows[:-1], windows[1:]):
        hist = pg[pg.date < start]
        pairs = fit_pairs(pg, before=start)
        if pairs.empty:
            continue
        flat, rp = fit_prior(pairs), fit_role_prior(pairs)
        cur = shrunk(pairs, flat, kappa).set_index(["team", "k", "t"])
        rol = shrunk(pairs, rp, kappa).set_index(["team", "k", "t"])
        agg = hist.groupby("aid").agg(fp=("fp", "mean"), mins=("min", "mean"), n=("fp", "size"), grp=("grp", "first"))
        last_team = hist.sort_values("date").groupby("aid").team.last()
        keys = agg[(agg.n >= 5) & ((agg.mins >= KEY_MIN) | (agg.fp >= KEY_FP))]
        key_team = {k: last_team[k] for k in keys.index}
        span = spans(pg)  # roster membership over the whole season
        test = pg[(pg.date >= start) & (pg.date < end)]
        for r in test.itertuples():
            if r.aid not in agg.index or agg.n[r.aid] < 3:
                continue
            outs = [k for k, tm in key_team.items() if tm == r.team and k != r.aid and (k, r.event) not in played
                    and (season, k, r.team) in span and span[(season, k, r.team)][0] <= r.date <= span[(season, k, r.team)][1]]
            if not outs:
                continue
            m, t_min, grp = agg.fp[r.aid], agg.mins[r.aid], agg.grp[r.aid]
            b_cur = b_role = 0.0
            new = False
            for k in outs:
                key = (r.team, k, r.aid)
                tog = (cur.n_in[key] + cur.n_out[key]) if key in cur.index else 0
                new |= tog < together_min
                if key in cur.index:
                    b_cur += max(0.0, cur.bump[key])
                    b_role += max(0.0, rol.bump[key])
                else:
                    b_role += max(0.0, role_bump(rp, keys.fp[k], keys.grp[k] == grp, t_min))
            for name, pred in (("none", m), ("current", m + scale * b_cur), ("role", m + scale * b_role)):
                res[name]["all"].append((r.fp - pred) ** 2)
                if new:
                    res[name]["new"].append((r.fp - pred) ** 2)
    out = {k: {s: (float(np.sqrt(np.mean(v))), len(v)) for s, v in d.items() if v} for k, d in res.items()}
    return out, fit_role_prior(fit_pairs(pg))


if __name__ == "__main__" and "--new" in sys.argv:
    out, rp = validate_new_situations()
    for k, d in out.items():
        print(f"   {k:8} " + "   ".join(f"{s}: RMSE {v[0]:6.2f} (n={v[1]:,})" for s, v in d.items()))
    print("role prior (FP gained per 1 FP/game of the missing teammate):",
          {f"{'same' if s else 'other'}-{['bench', 'rotation', 'starter'][r]}": round(v, 3) for (s, r), v in rp.items()})
elif __name__ == "__main__":
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
