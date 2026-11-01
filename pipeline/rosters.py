"""Roster projections for every team (site/data/rosters.json): per player this week and rest of season,
per team a cap-aware weekly projection and how sensitive its playoff/title odds are to it.

  this week       the live predictor's numbers for his games left this matchup week (chance he
                  plays x per-game projection), as FP per game played, games left and points
  rest of season  longterm.py's FP/g, upside and reasons; players without games this season fall
                  back to the per-game model
  weekly (sim)    the season simulation's expected score per remaining regular-season week
  weekly (fill)   a quick cap-aware estimate the trade builder can redo in the browser: each player
                  supplies (games per week x availability) games at his rest-of-season FP/g, and the
                  best games fill the average cap
  sensitivity     change in playoff and title odds per +10 expected points a week, from re-running
                  the season with that team's weekly expectation raised (same random numbers); sens_on
                  also records how that move shifts every other team's odds
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
OUT = ROOT / "site" / "data" / "rosters.json"
SHIFT = 10.0
SEED = 20271


def fill(players, cap):
    """Cap-aware weekly points: best rest-of-season games up to the cap (mirrored in site/js/trades.js)."""
    left, total = cap, 0.0
    for pl in sorted(players, key=lambda p: -p["ros"]):
        g = min(left, pl["gpw"] * pl["avail"])
        total += g * pl["ros"]
        left -= g
        if left <= 0:
            break
    return total


def build(ctx, players, exp, lt, proj, team_out, roster, pool, abbrev, names):
    reg_weeks = [mp for mp in ctx.later if mp not in ctx.rounds]
    caps = ctx.caps
    cap_avg = float(np.mean([caps.loc[mp].cap for mp in reg_weeks])) if reg_weeks else 0.0
    future_sps = [sp for mp in reg_weeks for sp in range(int(caps.loc[mp].sp_start), int(caps.loc[mp].sp_end) + 1)]
    game_no = {}
    for sp in sorted(s for s in ctx.games if s >= ctx.sp_now):
        for pro in ctx.games[sp]:
            game_no.setdefault(pro, {})[sp] = len(game_no.get(pro, {})) + 1

    slot_of = {e["playerId"]: e.get("lineupSlotId") for tm in roster["teams"] for e in tm["roster"]["entries"]}
    base = ctx.run(exp, seed=SEED)
    teams = {}
    for tid, plist in players.items():
        cands = team_out[tid]["cands"]
        rows = []
        for pl in plist:
            pid, pro = pl["pid"], pl["pro"]
            mine = cands[cands.player_id == pid]
            plays = float(mine.p.sum())
            pts = float((mine.p * mine.mu).sum())
            sps = [sp for sp in future_sps if pro in ctx.games.get(sp, ())]
            avail = float(np.mean([ctx.avail(pl, sp, game_no[pro][sp])[0] for sp in sps])) if sps else 0.0
            info = lt.get(pid) or {}
            ros = info.get("ros") or (proj.mu(pid) if proj and pid in pool else pl["mu"])
            p = pool.get(pid) or {}
            rows.append({"id": pid, "name": p.get("name") or str(pid), "pro": abbrev.get(pro, "").upper(), "pos": p.get("pos"),
                         "slot": slot_of.get(pid), "inj": pl["inj"] if pl["inj"] not in ("ACTIVE", None) else None,
                         "week": {"games": int(len(mine)), "plays": round(plays, 1), "pts": round(pts, 1),
                                  "fpg": round(pts / plays, 1) if plays > 0.05 else None},
                         "ros": round(float(ros), 1), "upside": info.get("upside"), "tags": info.get("tags", []),
                         "min_proj": info.get("min_proj"),
                         "gpw": round(len(sps) / max(len(reg_weeks), 1), 2), "avail": round(avail, 3)})
        rows.sort(key=lambda r: -r["ros"])
        # odds sensitivity: the same season with this team +SHIFT expected points every remaining week
        shifted = {k: v + (SHIFT if k[0] == tid else 0.0) for k, v in exp.items()}  # every week left, playoffs too
        up = ctx.run(shifted, seed=SEED)
        teams[str(tid)] = {"name": names.get(tid), "players": rows,
                           "weekly_sim": round(ctx.weekly(exp, tid), 1), "weekly_fill": round(fill(rows, cap_avg), 1),
                           "sens": {k: round(up[tid][k] - base[tid][k], 4) for k in ("playoff", "bye", "title")},
                           # effect of THIS team getting stronger on every team's odds (a trade moves two teams)
                           "sens_on": {str(o): {k: round(up[o][k] - base[o][k], 4) for k in ("playoff", "title")}
                                       for o in up}}
    OUT.write_text(json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "cap_avg": round(cap_avg, 1), "weeks_left": len(reg_weeks), "shift": SHIFT,
                               "teams": teams}, default=float, separators=(",", ":")), encoding="utf-8")
    print("rosters: " + ", ".join(f"{v['name']} {v['weekly_sim']:.0f}/wk" for v in teams.values()))
    return teams, cap_avg
