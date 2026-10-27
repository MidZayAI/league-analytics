"""Injury effects from NBA-wide game logs (nba_logs.py):

1. Return curve: P(a player plays the game h team-games from now | he has missed k straight).
   Replaces a flat "OUT = ~12% for every future game" with what actually happens.
2. Teammate usage bumps: when key teammate K sits, teammate T's expected fantasy points rise by
       BUMP_SCALE x max(0, shrunk with/without bump)
   Out-of-sample on 2025-26 this beat both no bump and a flat +5% (RMSE 13.36 vs 13.67 / 13.62);
   the raw per-pair bumps overstate the real gain about 2x, hence the half scale.

InjuryState(season, as_of) gives, for any player, his current miss streak, normal sit rate,
team, and the bumps he gets from each key teammate, using only games before `as_of`.
"""
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage as u  # noqa: E402

BUMP_SCALE = 0.5
KAPPA = 16
PREV_SEASON_WEIGHT = 0.5
CURVE_H = 20
BANDS = [(1, 1), (2, 2), (3, 5), (6, 10), (11, 10 ** 6)]
DEFAULT_SIT = 0.15


@lru_cache(maxsize=8)
def _pg(seasons):
    return u.player_games(list(seasons))


def fit_return_curve(seasons):
    """bands x h table of P(plays | missed k straight); h = 1..CURVE_H team-games ahead."""
    pg = _pg(tuple(seasons))
    fpg = pg.groupby(["season", "aid"]).fp.mean()
    teams_of = pg.groupby(["season", "aid"]).team.nunique()
    tg = u.team_games(pg)
    played = {(r.season, r.aid, r.event) for r in pg.itertuples()}
    hits = np.zeros((len(BANDS), CURVE_H))
    tot = np.zeros((len(BANDS), CURVE_H))
    for (s, aid, team), (d0, d1) in u.spans(pg).items():
        if fpg.get((s, aid), 0) < 15:
            continue
        games = tg[(s, team)]
        end = games[-1][0] if teams_of[(s, aid)] == 1 else d1  # stayed to season end unless traded
        seq = np.array([((s, aid, ev) in played) for day, ev in games if d0 <= day <= end])
        streak = 0
        for i in range(len(seq)):
            if streak:
                b = next(j for j, (lo, hi) in enumerate(BANDS) if lo <= streak <= hi)
                ahead = seq[i:i + CURVE_H]
                hits[b, :len(ahead)] += ahead
                tot[b, :len(ahead)] += 1
            streak = 0 if seq[i] else streak + 1
    curve = np.where(tot > 0, hits / np.maximum(tot, 1), np.nan)
    curve = np.maximum.accumulate(np.nan_to_num(curve, nan=0.0), axis=1)  # never less likely further out
    return [[round(float(v), 4) for v in row] for row in curve]


def curve_p(curve, k, h):
    """Chance he plays h team-games ahead after missing k straight (k >= 1)."""
    b = next(j for j, (lo, hi) in enumerate(BANDS) if lo <= max(k, 1) <= hi)
    return curve[b][min(max(h, 1), CURVE_H) - 1]


def play_p(curve, base, status, streak, h, game_date=None, return_date=None):
    """Chance a player plays his team's game h games from now.
    status: injury tag (OUT / INJURY_RESERVE / DAY_TO_DAY / ...); return_date from ESPN's NBA injury
    feed when known (live only). Without a tag, a player in a miss streak follows the return curve."""
    s = (status or "").upper().replace("-", "_")
    if s in ("OUT", "INJURY_RESERVE", "SUSPENSION"):
        if return_date and game_date:
            return 0.03 if game_date < return_date else 0.85 * base
        return curve_p(curve, max(streak, 1), h)
    if s == "DAY_TO_DAY":
        return 0.5 if h <= 1 else base
    if streak >= 1:
        return min(base, curve_p(curve, streak, h))
    return base


class InjuryState:
    """Everything injury-related for one season, using only games before `as_of` (YYYY-MM-DD).
    bumps_from reuses another state's (expensive) teammate bumps, e.g. a monthly refit."""

    def __init__(self, season, as_of, with_bumps=True, bumps_from=None):
        pg = _pg((season - 1, season))
        self.season, self.as_of = season, as_of
        hist = pg[pg.date < as_of]
        cur = hist[hist.season == season]
        tg = u.team_games(hist)
        last = cur.sort_values("date").groupby("aid").tail(1).set_index("aid")
        self.team = last.team.to_dict()
        self.name = cur.groupby("aid").name.first().to_dict()
        played = set(zip(cur.aid, cur.event))
        grp = cur.groupby(["aid", "team"]).date.agg(["size", "min"])
        self.streak, self.sit = {}, {}
        for aid, team in self.team.items():
            games = tg.get((season, team), [])
            n_mine, first = grp.loc[(aid, team)]
            n_span = sum(1 for day, _ in games if day >= first)
            k = 0
            for _, ev in reversed(games):
                if (aid, ev) in played:
                    break
                k += 1
            self.streak[aid] = k
            self.sit[aid] = (1 - n_mine / n_span) if n_span >= 5 else DEFAULT_SIT
        self.bumps = {}  # T -> [(K, scaled bump)]
        self.evidence = {}  # (K, T) -> with/without split behind the bump
        # season FP per game (this season if he has 5+ games, else last season) for the injury page
        both = pg[pg.date < as_of]
        cur_fp = both[both.season == season].groupby("aid").fp.agg(["mean", "size"])
        prev_fp = both[both.season == season - 1].groupby("aid").fp.mean()
        self.fp = {int(a): float(r["mean"]) for a, r in cur_fp.iterrows() if r["size"] >= 5}
        for a, v in prev_fp.items():
            self.fp.setdefault(int(a), float(v))
        self.pos = both.groupby("aid").grp.first().to_dict()
        for a, n in both.groupby("aid").name.first().items():
            self.name.setdefault(a, n)
        if bumps_from is not None:
            self.bumps, self.evidence = bumps_from.bumps, bumps_from.evidence
        elif with_bumps:
            pairs = u.fit_pairs(hist, before=as_of, season_weights={season: 1.0, season - 1: PREV_SEASON_WEIGHT})
            if len(pairs):
                prior = u.fit_prior(pairs)
                sh = u.shrunk(pairs, prior, KAPPA)
                sh = sh.assign(wt=(sh.n_out * sh.w_season).clip(lower=0.5))
                comb = sh.groupby(["k", "t"]).apply(lambda g: np.average(g.bump, weights=g.wt), include_groups=False)
                # evidence shown on the injury page: the most informative season's split for each pair
                best_row = sh.loc[sh.groupby(["k", "t"]).wt.idxmax()].set_index(["k", "t"])
                self.evidence = {}
                for (k_aid, t_aid), b in comb.items():
                    if b > 0 and self.team.get(k_aid) is not None and self.team.get(k_aid) == self.team.get(t_aid):
                        self.bumps.setdefault(int(t_aid), []).append((int(k_aid), BUMP_SCALE * float(b)))
                        r = best_row.loc[(k_aid, t_aid)]
                        self.evidence[(int(k_aid), int(t_aid))] = {
                            "n": int(r.n_out), "season": int(r.season), "raw": float(r.raw),
                            "min_in": float(r.t_min_in), "min_out": None if pd.isna(r.min_out) else float(r.min_out)}

    def sit_base(self, aid):
        return self.sit.get(aid, DEFAULT_SIT)

    def boost(self, t_aid, p_sit_of):
        """Extra expected FP for T given each key teammate's chance of sitting (p_sit_of(K) -> 0..1).
        Only sitting *beyond* K's normal rate counts: T's average already includes the usual DNPs."""
        best, total = None, 0.0
        for k_aid, b in self.bumps.get(t_aid, []):
            inc = b * max(0.0, p_sit_of(k_aid) - self.sit_base(k_aid))
            total += inc
            if inc > 0 and (best is None or inc > best[1]):
                best = (k_aid, inc)
        return total, best


if __name__ == "__main__":
    import json
    curve = fit_return_curve([2023, 2024, 2025, 2026])
    print("return curve (rows: missed 1, 2, 3-5, 6-10, 11+; cols h=1,3,5,10,20):")
    for (lo, hi), row in zip(BANDS, curve):
        print(f"   {lo}-{hi if hi < 999 else '+':>3}: " + "  ".join(f"{row[h - 1]:.2f}" for h in (1, 3, 5, 10, 20)))
    st = InjuryState(2026, "2026-03-01")
    print(f"InjuryState 2025-26 @ Mar 1: {len(st.team)} players, {sum(len(v) for v in st.bumps.values())} teammate bumps")
    top = sorted(((b, t, k) for t, lst in st.bumps.items() for k, b in lst), reverse=True)[:6]
    for b, t, k in top:
        print(f"   {st.name.get(t, t):24} +{b:4.1f} FP when {st.name.get(k, k)} sits (sits normally {st.sit_base(k):.0%})")
    p = Path(__file__).resolve().parent / "model_params.json"
    params = json.loads(p.read_text(encoding="utf-8"))
    params["return_curve"] = curve
    p.write_text(json.dumps(params, indent=2), encoding="utf-8")
    print("saved return_curve to model_params.json")
