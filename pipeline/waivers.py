"""Waiver wire: the best free agents for the games still to come, in the league's scoring.

For every upcoming NBA game (not yet started) in the rest of this matchup week and in next week:
    value = P(plays) x (0.5 x our model + 0.5 x Sleeper's projection for that game + injury boost)
  our model   = the live predictor's blend: ESPN's season projection as the prior, pulled toward this
                season's average and the last 15 days as games come in (projections.blend_mean)
  Sleeper     = that specific game's projection (updated daily), re-scored with our scoring settings;
                our model alone when Sleeper has no line for the game
  boost       = extra points while a key teammate sits (injuries.py, same as the injury page)
  P(plays)    = injury tag / ESPN return date / miss streak (injuries.play_p)
Writes site/data/waivers.json; the first run of each week also stores a snapshot in
data/history/waivers_{season}.json so the blend can be checked against what happened.
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pool as player_pool  # noqa: E402
import projections as pj  # noqa: E402
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
OUT = ROOT / "site" / "data" / "waivers.json"
SNAP = ROOT / "data" / "history" / f"waivers_{CURRENT}.json"
MODEL_WEIGHT = 0.5
CANDIDATES = 200   # free agents scored (by projection / season average)
SHOWN = 60
IR_SLOT = 13


def _num(x):
    return float("nan") if x is None else float(x)


def _mu(p, repl, params):
    prior = p.get("proj") or (p.get("prev") if (p.get("prev_gp") or 0) >= 10 else None) or repl
    return float(pj.blend_mean(prior, _num(p.get("avg")), p.get("n") or 0, _num(p.get("l15")), p.get("n15") or 0,
                               params["k"], params["r"]))


def build(pool, abbrev, avail, t, mp, sp_now, starts, now, roster, repl, dates):
    params = pj.PARAMS
    caps = t["caps"].set_index("mp")
    weeks = {"this": [sp for sp in range(sp_now, int(caps.loc[mp].sp_end) + 1)]}
    if mp + 1 in caps.index:
        weeks["next"] = list(range(int(caps.loc[mp + 1].sp_start), int(caps.loc[mp + 1].sp_end) + 1))
    sleeper, sleeper_inj, match = player_pool.sleeper_games(pool, abbrev)
    base = float(pj.play_prob(0, 0, params["p_base"], params["p_k"]))
    rostered = {e["playerId"]: (tm["id"], e.get("lineupSlotId")) for tm in roster["teams"] for e in tm["roster"]["entries"]}

    def score(aid, p):
        mu, pro = _mu(p, repl, params), p.get("pro")
        out = {"mu": round(mu, 1)}
        sl_vals, best_boost = [], (0.0, None)
        for wk, sps in weeks.items():
            pts = games = 0.0
            for sp in sps:
                if pro not in t["pro_games"].get(sp, ()) or (sp == sp_now and starts.get((pro, sp), 0) <= now):
                    continue
                day = dates[sp].isoformat()
                sl = sleeper.get(aid, {}).get(day)
                if sl:
                    sl_vals.append(sl["fp"])
                expert = sl["fp"] if sl else mu
                b, why = avail.boost(aid, pro, sp)
                if b > best_boost[0]:
                    best_boost = (b, why)
                play = avail.p_play(aid, base, p.get("inj"), pro, sp)
                if sl and sl["min"] == 0:
                    play *= 0.25  # Sleeper doesn't expect him to play
                pts += play * (MODEL_WEIGHT * mu + (1 - MODEL_WEIGHT) * expert + b)
                games += 1
            out[wk] = {"games": int(games), "pts": round(pts, 1)}
        out["sleeper"] = round(sum(sl_vals) / len(sl_vals), 1) if sl_vals else None
        out["boost"] = round(best_boost[0], 1) if best_boost[0] >= 0.5 else None
        out["why"] = best_boost[1] if best_boost[0] >= 0.5 else None
        return out

    free = [(a, p) for a, p in pool.items() if p.get("on_team", 0) == 0 and a not in rostered and p.get("pro")]
    free.sort(key=lambda x: -max(x[1].get("proj") or 0, x[1].get("avg") or 0, x[1].get("l15") or 0))
    rows = []
    for aid, p in free[:CANDIDATES]:
        s = score(aid, p)
        tot = sum(s.get(w, {}).get("pts", 0) for w in weeks)
        rows.append({"id": aid, "name": p["name"], "team": abbrev.get(p["pro"], "").upper(), "pos": p.get("pos"),
                     "inj": None if p.get("inj") in (None, "ACTIVE") else p["inj"], "sleeper_inj": sleeper_inj.get(aid),
                     "status": p.get("status"), "owned": p.get("owned"), "owned_chg": p.get("owned_chg"),
                     "espn": round(p["proj"], 1) if p.get("proj") else None,
                     "avg": round(p["avg"], 1) if p.get("avg") else None, "n": int(p.get("n") or 0),
                     "l15": round(p["l15"], 1) if p.get("l15") else None,
                     "outlook": p.get("outlook"), "total": round(tot, 1), **s})
    rows.sort(key=lambda r: -r["total"])

    # each league team's weakest healthy players over the same games, for "upgrade over your worst"
    weakest = {}
    for aid, (tid, slot) in rostered.items():
        p = pool.get(aid)
        if not p or slot == IR_SLOT or not p.get("pro"):
            continue
        s = score(aid, p)
        weakest.setdefault(tid, []).append({"id": aid, "name": p["name"], "this": s.get("this", {}).get("pts", 0),
                                            "next": s.get("next", {}).get("pts", 0)})
    for tid in weakest:
        weakest[tid] = sorted(weakest[tid], key=lambda r: r["this"] + r["next"])[:3]

    out = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mp": int(mp),
           "weeks": {w: {"mp": int(mp) + (w == "next"), "start": dates[sps[0]].isoformat(), "end": dates[sps[-1]].isoformat()}
                     for w, sps in weeks.items()},
           "sleeper_ok": bool(sleeper), "sleeper_match": round(match, 3), "model_weight": MODEL_WEIGHT,
           "players": rows[:SHOWN], "weakest": weakest}
    OUT.write_text(json.dumps(out, default=float, separators=(",", ":")), encoding="utf-8")
    snaps = json.loads(SNAP.read_text(encoding="utf-8")) if SNAP.exists() else {}
    if str(mp) not in snaps:  # first look at the week, before its games: the honest forecast to grade later
        snaps[str(mp)] = {"ts": now, "players": [{k: r.get(k) for k in ("id", "mu", "sleeper", "espn", "this", "next")}
                                                  for r in rows[:50]]}
        SNAP.parent.mkdir(parents=True, exist_ok=True)
        SNAP.write_text(json.dumps(snaps, separators=(",", ":")), encoding="utf-8")
    top = ", ".join(f"{r['name']} {r['total']:.0f}" for r in rows[:5])
    print(f"waivers: {len(rows)} free agents scored, Sleeper match {match:.0%}; top: {top}")
    if any(math.isnan(r["mu"]) for r in rows):
        print("waivers: warning, NaN projections")
