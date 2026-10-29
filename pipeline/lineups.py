"""Best lineup for the rest of the matchup week, per team, under the weekly games cap.

Every rostered player's remaining games this week (not yet started) are worth
    P(plays) x projected fantasy points        (gameproj.py: our model + Sleeper + betting lines + boost)
and the exact lineup solver (coaching.optimal_lineup) picks which games to start:
  each player-day fills at most one starting slot he's eligible for (PG, SG, SF, PF, C, G, F, 3 UTIL),
  and total starts <= the games left under this week's cap. That's a hard stop: the plan can never go
  over, and if a starter sits his game isn't used, the next refresh simply re-plans.
Compared with "start your best players every day until the cap runs out" (the naive way), the plan
saves cap games for better days. It also tries the top waiver-wire players: add one, drop one of the
team's weakest healthy players, re-solve, and reports the best gain.

Writes site/data/lineups.json.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coaching  # noqa: E402
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
OUT = ROOT / "site" / "data" / "lineups.json"
SLOT_NAME = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 11: "UTIL"}
IR_SLOT = 13
ADD_CANDIDATES = 25
DROP_CANDIDATES = 3
MIN_GAIN = 5.0


def _rows(players, slots):
    """players: [{id, elig, games: {sp: (p, fp)}}] -> one row per player-game for the solver."""
    out = []
    for pl in players:
        elig = tuple(s for s in pl["elig"] if s in slots)
        for sp, (p, fp) in pl["games"].items():
            out.append({"sp": sp, "player_id": pl["id"], "pts": p * fp, "elig": elig})
    return pd.DataFrame(out, columns=["sp", "player_id", "pts", "elig"])


def solve(players, slots, cap):
    rows = _rows(players, slots)
    if rows.empty or cap <= 0:
        return 0.0, 0, {}, rows
    pts, games, assign = coaching.optimal_lineup(rows, slots, cap=cap, value=rows.pts.to_numpy(), with_slots=True)
    # guard the two promises the page makes
    assert games <= cap, (games, cap)
    for sp, day in rows.loc[list(assign)].groupby("sp"):
        used = pd.Series([assign[i] for i in day.index]).value_counts()
        assert all(used[s] <= slots[s] for s in used.index), (sp, used.to_dict())
    return pts, games, assign, rows


def naive(players, slots, cap):
    """Start the best lineup every day, in order, until the cap runs out (no saving games for later)."""
    rows = _rows(players, slots)
    left, total = cap, 0.0
    for sp in sorted(rows.sp.unique()):
        if left <= 0:
            break
        day = rows[rows.sp == sp]
        pts, games, _ = coaching.optimal_lineup(day, slots, cap=left, value=day.pts.to_numpy(), with_slots=True)
        total, left = total + pts, left - games
    return total


def build(proj, t, mp, sp_now, starts, now, roster, rem_caps, pool, abbrev, waiver_rows, week, team_out):
    slots = t["slots"]
    days = [sp for sp in week if sp >= sp_now]
    dates = proj.dates

    def games_of(aid, pro, base=None, tag=None, skip_today=False):
        g = {}
        for sp in days:
            if pro not in t["pro_games"].get(sp, ()) or (sp == sp_now and (skip_today or starts.get((pro, sp), 0) <= now)):
                continue
            x = proj.game(aid, pro, sp, base, tag)
            g[sp] = (x["p"], x["fp"])
        return g

    # free agents worth trying, with their remaining games (a waiver claim can't play today)
    adds = []
    for r in [r for r in waiver_rows if not r.get("stash")][:ADD_CANDIDATES]:
        p = pool.get(r["id"]) or {}
        adds.append({"id": r["id"], "name": r["name"], "pro": p.get("pro"), "team": r["team"], "status": r.get("status"),
                     "elig": p.get("slots") or [], "games": games_of(r["id"], p.get("pro"), skip_today=r.get("status") == "WAIVERS")})

    out = {}
    for team in roster["teams"]:
        tid = team["id"]
        cap = int(rem_caps.get(tid, 0))
        players = []
        for e in team["roster"]["entries"]:
            pl = e["playerPoolEntry"]["player"]
            if e.get("lineupSlotId") == IR_SLOT:
                continue
            aid, pro = pl["id"], pl.get("proTeamId")
            # the live predictor's own numbers for these games (play chance from his history, same fp)
            c = team_out[tid]["cands"]
            mine = c[c.player_id == aid]
            players.append({"id": aid, "name": pl.get("fullName"), "pro": pro, "inj": pl.get("injuryStatus"),
                            "elig": pl.get("eligibleSlots") or [],
                            "games": {int(r.sp): (float(r.p), float(r.mu)) for r in mine.itertuples() if r.sp in days}})
        pts, games, assign, rows = solve(players, slots, cap)
        base_naive = naive(players, slots, cap)
        start = {(rows.loc[i, "player_id"], rows.loc[i, "sp"]): SLOT_NAME.get(s, str(s)) for i, s in assign.items()}
        grid = []
        for pl in players:
            cells = {}
            for sp, (p, fp) in pl["games"].items():
                slot = start.get((pl["id"], sp))
                cells[str(sp)] = {"start": bool(slot), "slot": slot, "fp": round(fp, 1), "p": round(p, 2)}
            grid.append({"id": pl["id"], "name": pl["name"], "pro": abbrev.get(pl["pro"], "").upper(), "inj": pl["inj"],
                         "starts": sum(1 for c in cells.values() if c["start"]),
                         "proj": round(sum(c["p"] * c["fp"] for c in cells.values() if c["start"]), 1), "days": cells})
        grid.sort(key=lambda g: (-g["starts"], -g["proj"]))

        # best add/drop: drop one of the weakest healthy players (by their best-case contribution)
        value = {pl["id"]: sum(p * fp for p, fp in pl["games"].values()) for pl in players}
        drops = sorted([pl for pl in players if (pl["inj"] or "ACTIVE") in ("ACTIVE", "NORMAL", None)],
                       key=lambda pl: value[pl["id"]])[:DROP_CANDIDATES]
        best = None
        for add in adds:
            if not add["games"]:
                continue
            for drop in drops:
                trial = [pl for pl in players if pl["id"] != drop["id"]] + [add]
                tp, _, _, _ = solve(trial, slots, cap)
                if best is None or tp - pts > best["gain"]:
                    best = {"gain": round(tp - pts, 1), "plan_pts": round(tp, 1),
                            "add": {k: add[k] for k in ("id", "name", "team", "status")},
                            "drop": {"id": drop["id"], "name": drop["name"]}}
        out[str(tid)] = {"rem_cap": cap, "plan_pts": round(pts, 1), "starts": games, "naive_pts": round(base_naive, 1),
                         "days": [{"sp": sp, "date": dates[sp].isoformat()} for sp in days if sp in t["pro_games"]],
                         "players": grid, "pickup": best if best and best["gain"] >= MIN_GAIN else None}
    OUT.write_text(json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mp": int(mp),
                               "teams": out}, default=float, separators=(",", ":")), encoding="utf-8")
    summary = ", ".join(f"{v['starts']}/{v['rem_cap']} starts, +{v['plan_pts'] - v['naive_pts']:.0f} vs naive"
                        + (f", pickup +{v['pickup']['gain']:.0f}" if v["pickup"] else "") for v in out.values())
    print(f"lineups: {summary}")
