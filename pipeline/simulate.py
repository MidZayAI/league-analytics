"""Monte Carlo for the rest of a matchup week under the games cap.

Each simulated week: every remaining player-game happens with probability p; a team starts its
best 10 available players per day (by projected mean; positions barely bind, see coaching.py
comparison) and spends the remaining cap on its highest-projected games. Points per game are
drawn from a gamma distribution (non-negative, right-skewed) with the player's mean and sd.
"""
import numpy as np

STARTERS = 10
SIMS = 10000


def allocate(avail, mu, sp, cap):
    """Which available games a team starts: best STARTERS per day by projected mean, then the
    cap spent on the highest-projected of those. avail: bool [sims, n] -> keep: bool [sims, n].

    Lineup choice depends only on availability and projections (never realized points), so a
    game's expected contribution is mu * P(started)."""
    # rank columns by projected mean (static across sims); available games fill slots in that order
    order = np.argsort(-mu, kind="stable")
    keep = np.zeros_like(avail)
    for day in np.unique(sp):
        cols = order[sp[order] == day]
        a = avail[:, cols]
        keep[:, cols] = a & (np.cumsum(a, axis=1) <= STARTERS)
    k = keep[:, order]
    keep[:, order] = k & (np.cumsum(k, axis=1) <= cap)
    return keep


def simulate_team(cands, remaining_cap, sims=SIMS, rng=None, team_sd=0.0):
    """cands: DataFrame with sp, mu, sd, p (one row per remaining player-game).

    team_sd scales each simulated week's remaining total by (1 + N(0, team_sd)): shared,
    team-wide uncertainty (mid-week pickups, streaming, minutes) that player-level draws miss.
    Returns (points per sim [sims], start probability per candidate [n], games used per sim [sims]).
    """
    rng = rng or np.random.default_rng()
    n = len(cands)
    if n == 0 or remaining_cap <= 0:
        return np.zeros(sims), np.zeros(n), np.zeros(sims)
    mu = np.maximum(cands.mu.to_numpy(float), 1.0)
    sd = cands.sd.to_numpy(float)
    p = cands.p.to_numpy(float)
    sp = cands.sp.to_numpy()

    avail = rng.random((sims, n)) < p
    shape, scale = (mu / sd) ** 2, sd ** 2 / mu
    pts = rng.gamma(shape, scale, size=(sims, n))
    keep = allocate(avail, mu, sp, remaining_cap)

    total = (pts * keep).sum(axis=1)
    if team_sd:
        total = np.maximum(0.0, total * (1 + rng.normal(0, team_sd, sims)))
    return total, keep.mean(axis=0), keep.sum(axis=1)


def win_prob(cur_a, sims_a, cur_b, sims_b):
    fa, fb = cur_a + sims_a, cur_b + sims_b
    return float((fa > fb).mean() + 0.5 * (fa == fb).mean())
