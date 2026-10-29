"""Waiver wire: the best free agents for the games still to come, in the league's scoring.

Every upcoming NBA game (not yet started) in the rest of this matchup week and next week is valued
with the shared per-game projection (gameproj.py: our model + Sleeper's game projection + DraftKings
player props when posted, + injured-teammate boost), times the chance he plays.
Ranked by projected points per game played (the weekly games cap makes per-game quality what matters).
Writes site/data/waivers.json; the first run of each week also stores a snapshot in
data/history/waivers_{season}.json so the rankings can be checked against what happened.
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
OUT = ROOT / "site" / "data" / "waivers.json"
SNAP = ROOT / "data" / "history" / f"waivers_{CURRENT}.json"
CANDIDATES = 200   # free agents scored (by projection / season average)
SHOWN = 60
STASH_SHOWN = 10
MIN_PLAYS = 1.0   # expected games played in the window to be ranked normally
AVAILABLE_SHARE = 0.6  # expected to play at least this share of his games, or he's listed as a stash
IR_SLOT = 13


def windows(t, mp, sp_now):
    caps = t["caps"].set_index("mp")
    weeks = {"this": list(range(sp_now, int(caps.loc[mp].sp_end) + 1))}
    if mp + 1 in caps.index:
        weeks["next"] = list(range(int(caps.loc[mp + 1].sp_start), int(caps.loc[mp + 1].sp_end) + 1))
    return weeks


def score(proj, aid, p, weeks, t, sp_now, starts, now, record=False):
    """Per-week {games, plays, pts, fpg} and the overall fpg for one player."""
    pro = p.get("pro")
    out = {"mu": round(proj.mu(aid), 1)}
    sl_vals, mk_vals, best_boost = [], [], (0.0, None)
    tot_val = tot_play = 0.0
    for wk, sps in weeks.items():
        pts = games = plays = 0.0
        for sp in sps:
            if pro not in t["pro_games"].get(sp, ()) or (sp == sp_now and starts.get((pro, sp), 0) <= now):
                continue
            g = proj.game(aid, pro, sp, record=record)
            if g["parts"]["sleeper"] is not None:
                sl_vals.append(g["parts"]["sleeper"])
            if g["parts"]["market"] is not None:
                mk_vals.append(g["parts"]["market"])
            if g["boost"] > best_boost[0]:
                best_boost = (g["boost"], g["why"])
            pts += g["p"] * g["fp"]
            plays += g["p"]
            games += 1
        # with a weekly games cap, a pickup's value is points per game played, not total volume
        out[wk] = {"games": int(games), "plays": round(plays, 1), "pts": round(pts, 1),
                   "fpg": round(pts / plays, 1) if plays > 0.05 else None}
        tot_val, tot_play = tot_val + pts, tot_play + plays
    out["fpg"] = round(tot_val / tot_play, 1) if tot_play > 0.05 else None
    out["plays"] = round(tot_play, 1)
    out["sleeper"] = round(sum(sl_vals) / len(sl_vals), 1) if sl_vals else None
    out["market"] = round(sum(mk_vals) / len(mk_vals), 1) if mk_vals else None
    out["boost"] = round(best_boost[0], 1) if best_boost[0] >= 0.5 else None
    out["why"] = best_boost[1] if best_boost[0] >= 0.5 else None
    return out


def build(pool, abbrev, proj, t, mp, sp_now, starts, now, roster):
    dates = proj.dates
    weeks = windows(t, mp, sp_now)
    sleeper_inj, match = proj.sleeper_inj, proj.match
    rostered = {e["playerId"]: (tm["id"], e.get("lineupSlotId")) for tm in roster["teams"] for e in tm["roster"]["entries"]}

    free = [(a, p) for a, p in pool.items() if p.get("on_team", 0) == 0 and a not in rostered and p.get("pro")]
    free.sort(key=lambda x: -max(x[1].get("sl") or 0, x[1].get("proj") or 0, x[1].get("avg") or 0, x[1].get("l15") or 0))
    rows = []
    for aid, p in free[:CANDIDATES]:
        s = score(proj, aid, p, weeks, t, sp_now, starts, now)
        tot = sum(s.get(w, {}).get("pts", 0) for w in weeks)
        rows.append({"id": aid, "name": p["name"], "team": abbrev.get(p["pro"], "").upper(), "pos": p.get("pos"),
                     "inj": None if p.get("inj") in (None, "ACTIVE") else p["inj"], "sleeper_inj": sleeper_inj.get(aid),
                     "status": p.get("status"), "owned": p.get("owned"), "owned_chg": p.get("owned_chg"),
                     "espn": round(p["proj"], 1) if p.get("proj") else None,
                     "avg": round(p["avg"], 1) if p.get("avg") else None, "n": int(p.get("n") or 0),
                     "l15": round(p["l15"], 1) if p.get("l15") else None,
                     "outlook": p.get("outlook"), "total": round(tot, 1), **s})
    # rank by projected points per game played; injured stash candidates (likely to miss many of their
    # games in the window) come after everyone who should actually be playing
    for r in rows:
        games = sum(r.get(w, {}).get("games", 0) for w in weeks)
        r["stash"] = r["plays"] < MIN_PLAYS or r["plays"] < AVAILABLE_SHARE * games
    rows.sort(key=lambda r: (r["stash"], -(r["fpg"] or 0)))

    # each league team's weakest healthy players (lowest projected FP per game), for "upgrade over your worst"
    weakest = {}
    for aid, (tid, slot) in rostered.items():
        p = pool.get(aid)
        if not p or slot == IR_SLOT or not p.get("pro"):
            continue
        s = score(proj, aid, p, weeks, t, sp_now, starts, now)
        if s["plays"] < MIN_PLAYS or s["fpg"] is None:
            continue  # injured or no games: not who you'd drop for a per-game upgrade comparison
        weakest.setdefault(tid, []).append({"id": aid, "name": p["name"], "fpg": s["fpg"],
                                            "this": s.get("this", {}).get("fpg"), "next": s.get("next", {}).get("fpg")})
    for tid in weakest:
        weakest[tid] = sorted(weakest[tid], key=lambda r: r["fpg"])[:3]

    out = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mp": int(mp),
           "weeks": {w: {"mp": int(mp) + (w == "next"), "start": dates[sps[0]].isoformat(), "end": dates[sps[-1]].isoformat()}
                     for w, sps in weeks.items()},
           "sleeper_ok": bool(proj.sleeper), "sleeper_match": round(match, 3), "weights": proj.weights,
           "lines": sum(len(v) for v in proj.props.values()),
           "players": [r for r in rows if not r["stash"]][:SHOWN] + [r for r in rows if r["stash"]][:STASH_SHOWN],
           "weakest": weakest}
    OUT.write_text(json.dumps(out, default=float, separators=(",", ":")), encoding="utf-8")
    for r in out["players"]:  # log the shown free agents' pre-game numbers for grading the blend
        score(proj, r["id"], pool[r["id"]], weeks, t, sp_now, starts, now, record=True)
    snaps = json.loads(SNAP.read_text(encoding="utf-8")) if SNAP.exists() else {}
    if str(mp) not in snaps:  # first look at the week, before its games: the honest forecast to grade later
        snaps[str(mp)] = {"ts": now, "players": [{k: r.get(k) for k in ("id", "mu", "sleeper", "espn", "fpg", "this", "next")}
                                                  for r in rows[:50]]}
        SNAP.parent.mkdir(parents=True, exist_ok=True)
        SNAP.write_text(json.dumps(snaps, separators=(",", ":")), encoding="utf-8")
    top = ", ".join(f"{r['name']} {r['fpg']}/g" for r in rows[:5])
    print(f"waivers: {len(rows)} free agents scored, Sleeper match {match:.0%}; top: {top}")
    if any(math.isnan(r["mu"]) for r in rows):
        print("waivers: warning, NaN projections")
    return out["players"]
