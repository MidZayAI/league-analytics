"""Per-player projections for the matchup predictor.

mean points per game = season-to-date average shrunk toward a prior (ESPN projection live,
last season's average in the backtest), then blended with the last-15-day average.
chance of playing    = recent games played / NBA team games, shrunk toward a base rate,
                       overridden live by ESPN injury tags.
game-to-game spread  = sd proportional to the mean (fit from league history).
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

# Defaults; pipeline/backtest.py writes tuned values to model_params.json
PARAMS = {"k": 10.0, "r": 0.25, "p_base": 0.80, "p_k": 4.0, "team_sd": 0.0}
_tuned = Path(__file__).resolve().parent / "model_params.json"
if _tuned.exists():
    PARAMS.update(json.loads(_tuned.read_text(encoding="utf-8")))
STATUS_PLAY = {"OUT": 0.0, "INJURY_RESERVE": 0.0, "SUSPENSION": 0.0, "DAY_TO_DAY": 0.5}
OUT_RETURN = 0.15  # chance an OUT player is back for a game 3+ days away
RECENT_DAYS = 15
PLAY_WINDOW = 14


def blend_mean(prior, mean, n, recent_mean, n_recent, k, r):
    prior, mean, n = np.asarray(prior, float), np.nan_to_num(np.asarray(mean, float)), np.asarray(n, float)
    mu = (k * prior + n * mean) / (k + n)
    recent_mean, n_recent = np.nan_to_num(np.asarray(recent_mean, float)), np.asarray(n_recent, float)
    return np.where(n_recent >= 3, (1 - r) * mu + r * recent_mean, mu)


def play_prob(played, opportunities, p_base, p_k):
    return (p_k * p_base + np.asarray(played, float)) / (p_k + np.asarray(opportunities, float))


def fit_cv(played):
    """Pooled game-to-game coefficient of variation for player-seasons averaging 15+ points."""
    g = played.groupby(["season", "player_id"]).pts.agg(["mean", "std", "size"])
    g = g[(g["size"] >= 15) & (g["mean"] >= 15)]
    return float((g["std"] / g["mean"]).median())


def spread(mu, cv):
    return np.maximum(6.0, cv * np.asarray(mu, float))


class HistoryModel:
    """Player state as of the start of any past day, from the league's own daily data."""

    def __init__(self, tables_by_season):
        frames = []
        for s, t in tables_by_season.items():
            d, games = t["daily"], t["pro_games"]
            has_game = [p in games.get(sp, ()) for p, sp in zip(d.pro, d.sp)]
            frames.append(d.loc[has_game, ["sp", "player_id", "pts", "gp"]].assign(season=s)
                          .drop_duplicates(["sp", "player_id"]))
        self.G = pd.concat(frames, ignore_index=True)
        played = self.G[self.G.gp > 0]
        sm = played.groupby(["season", "player_id"]).pts.agg(["mean", "size"])
        self.season_mean = sm
        self.repl = float(sm[sm["size"] >= 20]["mean"].quantile(0.25))  # replacement-level prior
        self.cv = fit_cv(played)
        self._by_season = {s: g for s, g in self.G.groupby("season")}

    def raw_state(self, season, day):
        """Unblended stats for every player seen this season, as of the start of `day`."""
        g = self._by_season.get(season)
        g = g[g.sp < day]
        p = g[g.gp > 0]
        out = pd.DataFrame({"n": p.groupby("player_id").size(), "mean": p.groupby("player_id").pts.mean()})
        rp = p[p.sp >= day - RECENT_DAYS]
        out["n15"] = rp.groupby("player_id").size()
        out["mean15"] = rp.groupby("player_id").pts.mean()
        w = g[g.sp >= day - PLAY_WINDOW]
        out = out.join(pd.DataFrame({"opps": w.groupby("player_id").size(),
                                     "played": w[w.gp > 0].groupby("player_id").size()}), how="outer")
        prev = self.season_mean.xs(season - 1, level="season") if season - 1 in self._by_season else None
        out = out.fillna({"n": 0, "n15": 0, "opps": 0, "played": 0})
        if prev is not None:
            prior = prev[prev["size"] >= 10]["mean"]
            out["prior"] = out.index.map(prior)
        else:
            out["prior"] = np.nan
        return out

    def finish(self, raw, player_ids, params=PARAMS):
        """Apply the model to raw state for these players -> DataFrame mu, sd, p (index player_id)."""
        r = raw.reindex(player_ids)
        r = r.fillna({"n": 0, "n15": 0, "opps": 0, "played": 0})
        prior = r["prior"].fillna(self.repl)
        mu = blend_mean(prior, r["mean"], r["n"], r["mean15"], r["n15"], params["k"], params["r"])
        return pd.DataFrame({"mu": mu, "sd": spread(mu, self.cv),
                             "p": play_prob(r["played"], r["opps"], params["p_base"], params["p_k"])},
                            index=player_ids)
