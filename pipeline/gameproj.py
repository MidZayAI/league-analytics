"""One projection per player-game, used by every view (win predictor, waiver wire, lineup plans).

  model    our formula: a starting estimate pulled toward this season's average and the last 15 days
           as games come in (projections.blend_mean). The starting estimate is Sleeper's average
           projection for his upcoming games (updated daily); ESPN's preseason projection only when
           Sleeper has none, then last season, then replacement level.
  sleeper  Sleeper's projection for that specific game, re-scored with the league's settings
  market   DraftKings player props for that game turned into fantasy points (market.py)
  fp       weighted blend of whichever are available, plus the injured-teammate boost

The weights start at model 0.25 / Sleeper 0.25 / market 0.50 (without a line: 0.5 / 0.5) and are
re-fit as games are graded: every refresh records the latest pre-game numbers for rostered and
top free-agent players, and grade() fits non-negative weights (summing to 1) to actual fantasy
points, shrunk toward the defaults by n / (n + SHRINK_N).
"""
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import market  # noqa: E402
import nba_logs  # noqa: E402
import pool as player_pool  # noqa: E402
import projections as pj  # noqa: E402
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
LOG = ROOT / "data" / "history" / f"gameproj_{CURRENT}.json"
BLEND = ROOT / "data" / "history" / f"blend_{CURRENT}.json"
DEFAULT = {"market": [0.25, 0.25, 0.50], "nomarket": [0.5, 0.5]}  # [model, sleeper(, market)]
SHRINK_N = 1500
PROP_DAYS = 2  # sportsbooks post lines about a day ahead


def _num(x):
    return float("nan") if x is None else float(x)


class GameProjector:
    def __init__(self, pool, abbrev, avail, dates, upcoming_sps, repl, sleeper=None):
        self.pool, self.avail, self.dates, self.repl = pool, avail, dates, repl
        self.params = pj.PARAMS
        self.sleeper, self.sleeper_inj, self.match = sleeper or player_pool.sleeper_games(pool, abbrev)
        days = sorted({dates[sp].isoformat() for sp in upcoming_sps})[:PROP_DAYS]
        try:
            self.props = market.fetch_props(days)
        except Exception as e:  # lines only sharpen the estimate; never block a refresh on them
            print(f"market: unavailable ({e})")
            self.props = {}
        market.SHOOT_PER_PT = market.shooting_ratio(self.sleeper)
        self.weights = load_weights()
        self.base = float(pj.play_prob(0, 0, self.params["p_base"], self.params["p_k"]))
        self._mu, self.logged = {}, {}

    def prior(self, aid):
        p = self.pool.get(aid) or {}
        sl = [g["fp"] for g in (self.sleeper.get(aid) or {}).values() if g.get("min")]
        if sl:
            return float(np.mean(sl)), "sleeper"
        if p.get("proj"):
            return p["proj"], "espn"
        if (p.get("prev_gp") or 0) >= 10:
            return p["prev"], "last season"
        return self.repl, "replacement"

    def mu(self, aid):
        """Our formula's per-game estimate (no specific game, no injury boost)."""
        if aid not in self._mu:
            p = self.pool.get(aid) or {}
            prior, _ = self.prior(aid)
            self._mu[aid] = float(pj.blend_mean(prior, _num(p.get("avg")), p.get("n") or 0, _num(p.get("l15")),
                                                p.get("n15") or 0, self.params["k"], self.params["r"]))
        return self._mu[aid]

    def game(self, aid, pro, sp, base=None, tag=None, record=False):
        """{fp, p, parts, boost, why} for this player's game on scoring period sp."""
        day = self.dates[sp].isoformat()
        model = self.mu(aid)
        sl = (self.sleeper.get(aid) or {}).get(day)
        sleeper = sl["fp"] if sl else None
        mk = market.market_fp((self.props.get(aid) or {}).get(day), sl and sl.get("box"))
        if mk is not None and sleeper is not None:
            w = self.weights["market"]
            fp = w[0] * model + w[1] * sleeper + w[2] * mk
        elif mk is not None:  # a line but no Sleeper row: split the Sleeper share between the two
            w = self.weights["market"]
            fp = (w[0] + w[1] / 2) * model + (w[2] + w[1] / 2) * mk
        elif sleeper is not None:
            w = self.weights["nomarket"]
            fp = w[0] * model + w[1] * sleeper
        else:
            fp = model
        b, why = self.avail.boost(aid, pro, sp)
        p = self.avail.p_play(aid, self.base if base is None else base,
                              tag if tag is not None else (self.pool.get(aid) or {}).get("inj"), pro, sp)
        if sl and sl.get("min") == 0:
            p *= 0.25  # Sleeper doesn't expect him to play
        if record:
            self.logged[(aid, day)] = [round(model, 2), sleeper, mk]
        return {"fp": fp + b, "p": p, "parts": {"model": round(model, 1), "sleeper": sleeper, "market": mk},
                "boost": b, "why": why}

    def save_log(self):
        """Keep the latest pre-game numbers for each player-game (games already started are frozen)."""
        log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {}
        for (aid, day), parts in self.logged.items():
            log.setdefault(day, {})[str(aid)] = parts
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text(json.dumps(log, separators=(",", ":")), encoding="utf-8")


def load_weights():
    if BLEND.exists():
        w = json.loads(BLEND.read_text(encoding="utf-8"))
        return {"market": w["market"], "nomarket": w["nomarket"]}
    return {k: list(v) for k, v in DEFAULT.items()}


def _fit(X, y, default):
    """Non-negative weights summing to 1 minimizing squared error (simplex-projected least squares)."""
    from scipy.optimize import minimize
    k = X.shape[1]
    res = minimize(lambda w: np.mean((X @ w - y) ** 2), np.array(default, float), method="SLSQP",
                   bounds=[(0, 1)] * k, constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}])
    return res.x if res.success else np.array(default, float)


def grade(today=None, verbose=True):
    """Fit the blend to graded games (pre-game numbers vs actual fantasy points). Games he didn't play
    don't count: the projection is points *if* he plays; availability is modelled separately."""
    if not LOG.exists():
        return load_weights()
    today = today or date.today().isoformat()
    log = json.loads(LOG.read_text(encoding="utf-8"))
    actual = {}
    for aid, a in nba_logs.load(CURRENT).items():
        for _, day, _, mins, fp in a["games"]:
            if mins and mins > 0:
                actual[(aid, day)] = fp
    rows3, rows2 = [], []
    for day, players in log.items():
        if day >= today:
            continue
        for aid, (model, sleeper, mk) in players.items():
            y = actual.get((aid, day))
            if y is None or sleeper is None:
                continue
            (rows3 if mk is not None else rows2).append((model, sleeper, mk, y))
    out = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for key, rows, cols in (("market", rows3, 3), ("nomarket", rows2 + [r for r in rows3], 2)):
        default = np.array(DEFAULT[key], float)
        if len(rows) < 30:
            out[key], out[f"{key}_n"] = default.tolist(), len(rows)
            continue
        R = np.array([r[:cols] + (r[3],) for r in rows], float)
        X, y = R[:, :cols], R[:, cols]
        fit = _fit(X, y, default)
        s = len(rows) / (len(rows) + SHRINK_N)
        w = s * fit + (1 - s) * default
        out[key], out[f"{key}_n"] = [round(float(v), 3) for v in w], len(rows)
        rmse = {n: float(np.sqrt(np.mean((X[:, i] - y) ** 2))) for i, n in enumerate(["model", "sleeper", "market"][:cols])}
        rmse["blend"] = float(np.sqrt(np.mean((X @ w - y) ** 2)))
        out[f"{key}_rmse"] = {k: round(v, 2) for k, v in rmse.items()}
        if verbose:
            print(f"gameproj grade ({key}, {len(rows)} games): " + ", ".join(f"{k} {v:.2f}" for k, v in rmse.items())
                  + f" -> weights {out[key]}")
    BLEND.parent.mkdir(parents=True, exist_ok=True)
    BLEND.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return {"market": out["market"], "nomarket": out["nomarket"]}


if __name__ == "__main__":
    print(grade())
