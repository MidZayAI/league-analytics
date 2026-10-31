"""Long-term value: a player's rest-of-season fantasy points per game, and a realistic upside.

FP/g = FP per minute x minutes, projected separately (roles move minutes far more than talent moves
per-minute production). Everything is learned from past seasons' NBA game logs (nba_logs) and
backtested: at checkpoints in a season, using only games before that date, predict each player's
rest-of-season FP/g.

  per-minute rate   this season's FP/min, shrunk toward last season's (or the rookie average)
  normal minutes    minutes in games where every key teammate played, except teammates who are
                    out long-term (their absence is the new normal). Games where a star sat only
                    briefly (the waiver-wire spike) don't define his role.
  minutes forecast  a fit on: normal minutes, last-5-game minutes, last season's minutes (with the
                    gap split into below/above, so ramp-ups and fading roles are learned separately)
                    and a rookie term
  upside            rest-of-season x the 75th-percentile actual/predicted ratio for similar players
                    (rookie / ramping up / low minutes / established), calibrated on past seasons

Usage: python pipeline/longterm.py --backtest
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage as u  # noqa: E402
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
PARAMS = ROOT / "pipeline" / "longterm_params.json"
KEY_FP, KEY_MIN = 30.0, 24.0
NORMAL_PRIOR = 3           # games of last season's role used when he has few "normal" games
LONG_STREAK = 8           # a key teammate out this many straight games = long-term (new normal)
RATE_K = 400.0            # minutes of this season before his own FP/min outweighs the prior
UPSIDE_Q = 0.75
CHECKPOINTS = ("11-01", "11-15", "12-15", "01-15")
FEATURES = ["min_norm", "min_last5", "gap_below", "gap_above", "has_prev", "rookie"]


def _season_start(season):
    return f"{season - 1}"


def features(pg, season, as_of, long_out=None):
    """One row per player with 3+ games this season before as_of. long_out: optional set of athlete ids
    known to be out long-term (live: ESPN return dates); otherwise inferred from miss streaks."""
    cur = pg[(pg.season == season) & (pg.date < as_of)]
    if cur.empty:
        return pd.DataFrame()
    prev = pg[pg.season == season - 1]
    prev2 = pg[pg.season == season - 2]
    seen_before = set(pg[pg.season < season].aid)

    agg = cur.groupby("aid").agg(n=("fp", "size"), fp=("fp", "sum"), mins=("min", "sum"), team=("team", "last"),
                                 name=("name", "first"), grp=("grp", "first"))
    p_agg = prev.groupby("aid").agg(pn=("fp", "size"), pfp=("fp", "sum"), pmin=("min", "sum"))
    p2 = prev2.groupby("aid").agg(qn=("fp", "size"), qfp=("fp", "sum"), qmin=("min", "sum"))

    # key players and who is out short-term vs long-term, per team, as of the checkpoint
    tg = u.team_games(cur)
    keys = {k: r.team for k, r in agg[(agg.n >= 3) & ((agg.fp / agg.n >= KEY_FP) | (agg.mins / agg.n >= KEY_MIN))].iterrows()}
    # last season's key players who haven't played yet this season (injured stars): every game so far is a miss
    p_team = prev.sort_values("date").groupby("aid").team.last()
    p_key = p_agg[(p_agg.pn >= 20) & ((p_agg.pfp / p_agg.pn >= KEY_FP) | (p_agg.pmin / p_agg.pn >= KEY_MIN))]
    for k in p_key.index:
        if k not in agg.index and k in p_team.index:
            keys[k] = p_team[k]
    played = set(zip(cur.aid, cur.event))
    short = {}  # team -> {key aid: [events he missed that count as temporary]}
    for k, team in keys.items():
        games = tg.get((season, team), [])
        mine = cur[(cur.aid == k) & (cur.team == team)]
        if k in agg.index and mine.empty:
            continue  # traded away
        first = mine.date.min() if len(mine) else "0000"
        evs = [(d, e) for d, e in games if d >= first]
        streak = 0
        for _, e in reversed(evs):
            if (k, e) in played:
                break
            streak += 1
        is_long = streak >= LONG_STREAK or (long_out is not None and k in long_out)
        if is_long:
            continue  # his absence is the new normal: those games define the role
        short.setdefault(team, set()).update(e for _, e in evs if (k, e) not in played)

    cur = cur.assign(boosted=[e in short.get(t, ()) for t, e in zip(cur.team, cur.event)])
    normal = cur[~cur.boosted].groupby("aid")["min"].agg(["mean", "size"])
    boosted = cur[cur.boosted].groupby("aid").agg(b_n=("fp", "size"), b_fp=("fp", "mean"), b_min=("min", "mean"))
    last5 = cur.sort_values("date").groupby("aid").tail(5).groupby("aid")["min"].mean()

    df = agg.join(p_agg).join(p2).join(boosted)
    df["avg"] = df.fp / df.n
    df["min_avg"] = df.mins / df.n
    df["min_last5"] = last5.reindex(df.index).fillna(df.min_avg)
    # last healthy season: last season if 20+ games, else the one before (players who missed a season)
    use_q = (df.pn.fillna(0) < 20) & (df.qn.fillna(0) >= 20)
    df["prev_min"] = np.where(use_q, df.qmin / df.qn, df.pmin / df.pn)
    df["prev_fpm"] = np.where(use_q, df.qfp / df.qmin, df.pfp / df.pmin)
    df["has_prev"] = (df.pn.fillna(0) >= 20) | use_q
    # normal minutes: games with every key teammate available; with few of those (a star has missed
    # most games so far), lean on last season's role as if it were NORMAL_PRIOR such games
    nn = normal["size"].reindex(df.index).fillna(0)
    nm = normal["mean"].reindex(df.index).fillna(0)
    prior_min = np.where(df.has_prev, df.prev_min, df.min_avg)
    df["min_norm"] = np.where(nn >= 3, nm, (nn * nm + NORMAL_PRIOR * prior_min) / (nn + NORMAL_PRIOR))
    df["rookie"] = ~df.index.isin(seen_before)
    gap = (df.prev_min - df.min_norm).where(df.has_prev, 0.0).fillna(0.0)
    df["gap_below"] = gap.clip(lower=0)   # playing fewer minutes than his healthy level (ramp-up)
    df["gap_above"] = (-gap).clip(lower=0)  # more than last season (new role, or a temporary bump)
    df["has_prev"] = df.has_prev.astype(float)
    df["rookie"] = df.rookie.astype(float)
    df["fpm_cur"] = df.fp / df.mins.clip(lower=1)
    df["boost_share"] = df.b_n.fillna(0) / df.n
    return df


def predict(df, coef):
    """Rest-of-season FP/g (and its pieces) from features and fitted coefficients."""
    X = df[FEATURES].to_numpy(float)
    mins = np.clip(X @ np.array(coef["min_w"]) + coef["min_b"], 0, 40)
    prior = np.where(df.has_prev > 0, df.prev_fpm, np.where(df.rookie > 0, coef["rookie_fpm"], coef["fpm_mean"]))
    prior = np.nan_to_num(prior.astype(float), nan=coef["fpm_mean"])
    rate = (df.mins * df.fpm_cur + RATE_K * prior) / (df.mins + RATE_K)
    rate = rate * np.where(df.rookie > 0, 1 + coef["rookie_growth"], 1.0)
    return pd.DataFrame({"ros": rate * mins, "rate": rate, "min_proj": mins}, index=df.index)


def group(df, pred):
    g = np.where(df.rookie > 0, "rookie",
                 np.where(df.gap_below >= 5, "ramping",
                          np.where(pred.min_proj < 20, "low minutes", "established")))
    return pd.Series(g, index=df.index)


def training_rows(pg, seasons):
    rows = []
    for s in seasons:
        for cp in CHECKPOINTS:
            as_of = f"{s - 1 if cp >= '07' else s}-{cp}"
            df = features(pg, s, as_of)
            if df.empty:
                continue
            after = pg[(pg.season == s) & (pg.date >= as_of)].groupby("aid").agg(rn=("fp", "size"), rfp=("fp", "mean"),
                                                                                 rmin=("min", "mean"))
            df = df.join(after, how="inner")
            df = df[df.rn >= 5]
            rows.append(df.assign(season=s, as_of=as_of))
    return pd.concat(rows) if rows else pd.DataFrame()


def fit(train):
    w = train.rn.to_numpy(float)
    X = np.column_stack([train[FEATURES].to_numpy(float), np.ones(len(train))])
    sw = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * sw[:, None], train.rmin.to_numpy(float) * sw, rcond=None)
    vets = train[train.has_prev > 0]
    rookies = train[train.rookie > 0]
    fpm_mean = float((train.fp.sum()) / train.mins.sum())
    rookie_fpm = float(rookies.fp.sum() / rookies.mins.sum()) if len(rookies) else fpm_mean
    # rookies' per-minute production later in the season vs so far (minutes-weighted)
    growth = float((rookies.rfp * rookies.rn).sum() / (rookies.rmin * rookies.rn).sum() / rookie_fpm - 1) if len(rookies) else 0.0
    coef = {"min_w": beta[:-1].tolist(), "min_b": float(beta[-1]), "fpm_mean": fpm_mean,
            "rookie_fpm": rookie_fpm, "rookie_growth": max(0.0, growth), "n_vets": int(len(vets))}
    # upside: 75th percentile of actual / predicted by group
    pred = predict(train, coef)
    ratio = (train.rfp / pred.ros.clip(lower=3)).clip(0, 4)
    grp = group(train, pred)
    coef["upside"] = {g: float(ratio[grp == g].quantile(UPSIDE_Q)) for g in grp.unique()}
    return coef


def baselines(df):
    """What we'd predict without this model."""
    shrink = (df.n * df.avg + 20 * (df.prev_fpm * df.prev_min).fillna(df.avg)) / (df.n + 20)
    return {"season avg": df.avg, "last 5 games": df.min_last5 * df.fpm_cur, "current model": shrink}


def backtest(pg):
    out = {}
    for test in (2025, 2026):
        train = training_rows(pg, [s for s in (2024, 2025, 2026) if s != test])
        tst = training_rows(pg, [test])
        coef = fit(train)
        pred = predict(tst, coef)
        w = tst.rn.to_numpy(float)
        grp = group(tst, pred)
        boosted = (tst.boost_share >= 0.3) & (tst.b_min > tst.min_norm + 3)
        subsets = {"all": np.ones(len(tst), bool), "rookies": tst.rookie > 0, "ramping up": grp == "ramping",
                   "injury-boosted": boosted.to_numpy()}
        meths = {**baselines(tst), "long-term model": pred.ros}
        res = {}
        for sname, m in subsets.items():
            m = np.asarray(m)
            if m.sum() < 10:
                continue
            res[sname] = {k: float(np.sqrt(np.average((v.to_numpy()[m] - tst.rfp.to_numpy()[m]) ** 2, weights=w[m])))
                          for k, v in meths.items()} | {"n": int(m.sum())}
        up = pred.ros * grp.map(coef["upside"]).fillna(1.25)
        res["upside hit rate"] = float(np.average(tst.rfp >= up, weights=w))
        out[test] = res
    return out


def fit_all(pg):
    coef = fit(training_rows(pg, [2024, 2025, 2026]))
    PARAMS.write_text(json.dumps(coef, indent=1), encoding="utf-8")
    return coef


def load_params():
    return json.loads(PARAMS.read_text(encoding="utf-8")) if PARAMS.exists() else None


def live(as_of, feed=None, evidence=None, coef=None):
    """Rest-of-season FP/g, upside, role ceiling and plain-English tags for every player who has played
    this season. feed: ESPN injury report {aid: {status, return}} (teammates out 3+ weeks = long-term);
    evidence: injuries.InjuryState.evidence {(K, T): {min_out, ...}} for the role ceiling."""
    from datetime import date, timedelta
    coef = coef or load_params()
    if not coef:
        return {}
    pg = u.player_games([CURRENT - 2, CURRENT - 1, CURRENT])
    soon = (date.fromisoformat(as_of) + timedelta(days=21)).isoformat()
    long_out = {a for a, f in (feed or {}).items()
                if f.get("status") == "Out" and (f.get("return") is None or f["return"] >= soon)}
    df = features(pg, CURRENT, as_of, long_out)
    if df.empty:
        return {}
    pred = predict(df, coef)
    grp = group(df, pred)
    ceil_min = {}
    for (k, t), ev in (evidence or {}).items():
        if ev.get("min_out") and not ev.get("role"):
            ceil_min[t] = max(ceil_min.get(t, 0), ev["min_out"])
    out = {}
    for aid, r in df.iterrows():
        p = pred.loc[aid]
        ros = float(p.ros)
        up = ros * coef["upside"].get(grp[aid], 1.15)
        tags = []
        if r.gap_below >= 3 and r.n >= 4 and p.min_proj > r.min_avg + 1:
            tags.append(f"Ramping up: {r.min_norm:.0f} min now, {r.prev_min:.0f} when healthy, ~{p.min_proj:.0f} projected")
        if r.rookie:
            tags.append(f"Rookie: {p.min_proj:.0f} min projected; rookies' per-minute production rises "
                        f"~{100 * coef['rookie_growth']:.0f}% later in the season")
        if (r.b_n or 0) >= 2 and r.boost_share >= 0.3 and (r.b_min or 0) > r.min_norm + 3:
            tags.append(f"Short-term bump: {r.b_min:.0f} min / {r.b_fp:.0f} FP in games a key teammate missed, "
                        f"{r.min_norm:.0f} min otherwise")
        if r.gap_above >= 6 and not r.rookie and r.boost_share < 0.5:
            tags.append(f"New role: {r.min_norm:.0f} min vs {r.prev_min:.0f} last season (part of that usually fades)")
        ceiling = None
        if ceil_min.get(aid, 0) >= p.min_proj + 6:
            ceiling = float(p.rate * ceil_min[aid])
            tags.append(f"Role ceiling ~{ceiling:.0f} FP/g at the {ceil_min[aid]:.0f} min he gets when a key teammate sits")
        out[int(aid)] = {"ros": round(ros, 1), "upside": round(up, 1), "ceiling": round(ceiling, 1) if ceiling else None,
                         "min_now": round(float(r.min_avg), 1), "min_norm": round(float(r.min_norm), 1),
                         "min_proj": round(float(p.min_proj), 1), "rate": round(float(p.rate), 3),
                         "n": int(r.n), "group": grp[aid], "tags": tags}
    return out


if __name__ == "__main__":
    pg = u.player_games([2023, 2024, 2025, 2026, 2027])
    if "--backtest" in sys.argv:
        for season, res in backtest(pg).items():
            print(f"\ntest season {season - 1}-{str(season)[2:]} (trained on the others), RMSE of rest-of-season FP/g:")
            for sname, r in res.items():
                if isinstance(r, dict):
                    print(f"  {sname:15} n={r.pop('n'):5}  " + "  ".join(f"{k} {v:5.2f}" for k, v in r.items()))
                else:
                    print(f"  {sname}: {r:.0%} finished at or above their upside (target 25%)")
    coef = fit_all(pg)
    print("\nfitted:", {k: (np.round(v, 3).tolist() if isinstance(v, list) else v) for k, v in coef.items()})
