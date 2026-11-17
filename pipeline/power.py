"""Power rankings (site/data/power.json): how good each team is now, who is built for the playoffs,
how much of the roster's worth each manager is getting, and where each roster leaks points.

All scores are fantasy points per week at the average remaining games cap, so they compare directly.

  talent        the current roster with perfect lineups: rest-of-season FP/g x games x availability,
                best games filling the cap (rosters.json weekly_fill). Coaching-agnostic.
  results       points per game actually counted this season x the cap
  optimal       points per game the best possible lineups would have counted x the cap (roster only)
  power         talent blended with results; results weigh more as weeks are played
                (w = 0.5 n / (n + 4)). "Roster only" swaps results for optimal.
  playoff push  expected points per week in the playoff weeks (season simulation: playoff-week
                games and caps, injured players returning, rest-of-season projections), plus an
                upside case at each player's realistic good-case FP/g
  grade / tier  from the all-play win chance the score implies: the chance of beating each other
                team's score in a week, with the season simulation's week-to-week spread

Management (how well each team plays its hand), each ranked within the league:
  lineup efficiency  actual vs optimal points this season (hindsight)
  bad starts         a counted game while a benched teammate who played that day, eligible for the
                     slot, was projected 8+ FP better that morning (no hindsight; same day, so the
                     cap doesn't excuse it)
  waiver work        waiver points above replacement per week (draft.json)
  dead spots         non-IR players 3+ FP/g (rest of season) below the best free agent available now
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import projections  # noqa: E402
from espn_client import ROOT  # noqa: E402
from normalize import cached_seasons, load_season  # noqa: E402
from rosters import fill  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
DATA = ROOT / "site" / "data"
OUT = DATA / "power.json"
HIST = ROOT / "data" / "history" / f"power_{CURRENT}.json"
IR_SLOT, BENCH_SLOT = 13, 12
BAD_GAP = 8.0
DEAD_GAP = 3.0
MIN_GAIN = 5.0            # dead spot: the swap must add 5+ points a week under the cap
TIERS = [(0.62, "Contender"), (0.52, "Playoff team"), (0.45, "Bubble"), (0.36, "Long shot"), (0.0, "Rebuild")]


def _load(name):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def grade(p):
    """Letter from an all-play win chance: A .68+, B .56+, C .44+, D .32+, else F, with +/- in thirds."""
    for lo, hi, g in ((0.68, 0.80, "A"), (0.56, 0.68, "B"), (0.44, 0.56, "C"), (0.32, 0.44, "D")):
        if p >= lo:
            third = (hi - lo) / 3
            return g + ("+" if p >= hi - third else "-" if p < lo + third else "")
    return "F"


def pct_grade(pct):
    for cut, g, hi in ((0.85, "A", 1.0), (0.60, "B", 0.85), (0.35, "C", 0.60), (0.15, "D", 0.35)):
        if pct >= cut:
            third = (hi - cut) / 3
            return g + ("+" if pct >= hi - third else "-" if pct < cut + third else "")
    return "F"


def allplay(scores, cv):
    """Chance each team's weekly score beats each other team's, averaged (normal, sd = cv x mean)."""
    out = {}
    for t, a in scores.items():
        ps = []
        for o, b in scores.items():
            if o != t:
                sd = math.hypot(cv * a, cv * b) or 1.0
                ps.append(0.5 * (1 + math.erf((a - b) / sd / math.sqrt(2))))
        out[t] = float(np.mean(ps)) if ps else 0.5
    return out


def tier(p):
    return next(name for cut, name in TIERS if p >= cut)


def bad_starts(t, model):
    """Counted games where a benched, slot-eligible teammate who played that day was projected 8+ better."""
    d = t["daily"]
    today = int(d.sp.max()) if len(d) else 0
    out = {}
    for sp, day in d[d.sp < today].groupby("sp"):
        raw = model.raw_state(CURRENT, sp)
        est = model.finish(raw, day.player_id.unique().tolist())
        mu = est.mu.to_dict()
        for tid, g in day.groupby("team_id"):
            bench = g[(g.slot == BENCH_SLOT) & (g.gp > 0)]
            if bench.empty:
                continue
            for s in g[g.counted & (g.gp > 0) & (g.slot != BENCH_SLOT) & (g.slot != IR_SLOT)].itertuples():
                elig = bench[[s.slot in (e or ()) for e in bench.elig]]
                if elig.empty:
                    continue
                b = max(elig.itertuples(), key=lambda r: mu.get(r.player_id, 0))
                gap = mu.get(b.player_id, 0) - mu.get(s.player_id, 0)
                if gap >= BAD_GAP:
                    out.setdefault(int(tid), []).append({
                        "sp": int(sp), "date": t["calendar"].set_index("sp").date.get(sp).isoformat(),
                        "start": s.name, "start_proj": round(mu.get(s.player_id, 0), 1), "start_pts": float(s.pts),
                        "bench": b.name, "bench_proj": round(mu.get(b.player_id, 0), 1), "bench_pts": float(b.pts),
                        "gap": round(gap, 1)})
    return out


def dead_spots(players, free_agents, cap):
    """Non-IR players whose rest-of-season FP/g is 3+ below the best free agent still unclaimed."""
    fas = sorted([f for f in free_agents if not f.get("stash") and (f.get("lt") or {}).get("ros")],
                 key=lambda f: -f["lt"]["ros"])
    out, used = [], set()
    base = fill(players, cap)
    for pl in sorted([p for p in players if p.get("slot") != IR_SLOT], key=lambda p: p["ros"]):
        fa = next((f for f in fas if f["id"] not in used), None)
        if not fa or fa["lt"]["ros"] - pl["ros"] < DEAD_GAP:
            break
        used.add(fa["id"])
        swap = [p for p in players if p["id"] != pl["id"]] + [{"id": fa["id"], "ros": fa["lt"]["ros"], "gpw": pl["gpw"], "avail": 0.92}]
        gain = fill(swap, cap) - base
        if gain < MIN_GAIN:  # wouldn't crack the capped lineup often enough to matter
            used.discard(fa["id"])
            continue
        out.append({"drop": pl["name"], "drop_id": pl["id"], "drop_ros": pl["ros"], "drop_upside": pl.get("upside"),
                    "drop_note": (pl.get("tags") or [None])[0], "add": fa["name"], "add_id": fa["id"],
                    "add_ros": fa["lt"]["ros"], "add_team": fa.get("team"), "add_note": (fa["lt"].get("tags") or [None])[0],
                    "gain": round(gain, 1)})
    # injured, not on IR, not expected back soon: clogging a spot
    clog = [{"name": p["name"], "id": p["id"], "avail": p["avail"]} for p in players
            if p.get("slot") != IR_SLOT and p.get("inj") and p.get("avail", 1) < 0.35]
    return out, clog


def build():
    R, O, W, S, D = _load("rosters.json"), _load("odds.json"), _load("waivers.json"), _load(f"season_{CURRENT}.json"), _load("draft.json")
    if not (R and S):
        return None
    cap = R["cap_avg"] or 38.0
    cv = (O or {}).get("params", {}).get("week_cv", 0.15)
    team_rows = {str(t["team_id"]): t for t in S["teams"]}
    odds = {str(t["team_id"]): t for t in (O or {}).get("teams", [])}
    waiver = {str(w["team_id"]): w for w in ((D or {}).get("seasons", {}).get(str(CURRENT), {}) or {}).get("waivers", [])}
    weeks = [w for w in S["weeks"] if not w["playoff"] and w.get("result")]
    n_weeks = len({w["mp"] for w in weeks})
    w_res = 0.5 * n_weeks / (n_weeks + 4)

    # bad starts need each morning's projections
    try:
        tables = {s: load_season(s) for s in cached_seasons() if s >= CURRENT - 1}
        bad = bad_starts(tables[CURRENT], projections.HistoryModel(tables))
    except Exception as e:  # never fail the page over this
        print(f"power: bad starts skipped ({e})")
        bad = {}

    teams = {}
    for tid, r in R["teams"].items():
        mine = [w for w in weeks if str(w["team_id"]) == tid]
        gp = sum(w["gp"] or 0 for w in mine)
        res = sum(w["pts"] for w in mine) / gp * cap if gp else None
        ogp = sum(w["opt_gp"] or 0 for w in mine)
        opt = sum(w["opt_pts"] for w in mine) / ogp * cap if ogp else None
        talent = r["weekly_fill"]
        up_ratio = (fill([{**p, "ros": p.get("upside") or p["ros"]} for p in r["players"]], cap) / talent) if talent else 1.0
        po = r.get("playoff_weekly") or r["weekly_sim"]
        dead, clog = dead_spots(r["players"], (W or {}).get("longterm", []), cap)
        b = sorted(bad.get(int(tid), []), key=lambda x: -x["gap"])
        teams[tid] = {
            "team_id": int(tid), "name": r["name"],
            "w": team_rows.get(tid, {}).get("w"), "l": team_rows.get(tid, {}).get("l"), "t": team_rows.get(tid, {}).get("t"),
            "talent": round(talent, 1), "results": round(res, 1) if res else None, "optimal": round(opt, 1) if opt else None,
            "left_on_table": round(opt - res, 1) if res and opt else None,
            "power": round((1 - w_res) * talent + w_res * (res if res else talent), 1),
            "power_roster": round((1 - w_res) * talent + w_res * (opt if opt else talent), 1),
            "playoff": round(po, 1), "playoff_upside": round(po * up_ratio, 1),
            "title": odds.get(tid, {}).get("title"), "playoff_odds": odds.get(tid, {}).get("playoff"),
            "proj_w": odds.get(tid, {}).get("proj_w"),
            "mgmt": {"efficiency": team_rows.get(tid, {}).get("efficiency"),
                     "bad_starts": {"n": len(b), "cost": round(sum(x["gap"] for x in b), 1), "top": b[:3]},
                     "waiver_par": waiver.get(tid, {}).get("par_per_week"),
                     "dead_spots": dead, "clogged": clog},
        }

    # grades and tiers from the all-play win chance each score implies
    for key in ("power", "power_roster", "talent", "playoff"):
        ap = allplay({t: v[key] for t, v in teams.items()}, cv)
        for t, v in teams.items():
            v.setdefault("ap", {})[key] = round(ap[t], 3)
            v.setdefault("grades", {})[key] = grade(ap[t])
        for i, t in enumerate(sorted(teams, key=lambda t: -teams[t][key])):
            teams[t].setdefault("ranks", {})[key] = i + 1
    for v in teams.values():
        v["tier"] = tier(v["ap"]["power"])
        v["tier_roster"] = tier(v["ap"]["power_roster"])
        v["tier_playoff"] = tier(v["ap"]["playoff"])

    # management grade: average league percentile of the four components (higher = better)
    comps = {
        "efficiency": lambda v: v["mgmt"]["efficiency"],
        "bad_starts": lambda v: -v["mgmt"]["bad_starts"]["cost"] / max(n_weeks, 1),
        "waivers": lambda v: v["mgmt"]["waiver_par"],
        "dead_spots": lambda v: -sum(d["gain"] for d in v["mgmt"]["dead_spots"]),
    }
    for name, f in comps.items():
        vals = {t: f(v) for t, v in teams.items() if f(v) is not None}
        for t, x in vals.items():
            below = sum(y < x for y in vals.values())
            same = sum(y == x for y in vals.values()) - 1
            teams[t]["mgmt"].setdefault("pct", {})[name] = round((below + 0.5 * same) / max(len(vals) - 1, 1), 3)
    for v in teams.values():
        ps = list(v["mgmt"].get("pct", {}).values())
        v["mgmt"]["score"] = round(float(np.mean(ps)), 3) if ps else None
    order = sorted(teams.values(), key=lambda v: v["mgmt"]["score"] or 0)
    for i, v in enumerate(order):
        v["mgmt"]["grade"] = pct_grade(i / max(len(order) - 1, 1)) if v["mgmt"]["score"] is not None else None
        v["mgmt"]["rank"] = len(order) - i

    notes(teams, n_weeks)
    history(teams)
    out = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": CURRENT,
           "cap_avg": cap, "weeks_played": n_weeks, "results_weight": round(w_res, 3), "week_cv": cv,
           "tiers": [n for _, n in TIERS], "teams": sorted(teams.values(), key=lambda v: v["ranks"]["power"])}
    OUT.write_text(json.dumps(out, default=float, separators=(",", ":")), encoding="utf-8")
    print("power: " + ", ".join(f"{v['ranks']['power']}. {v['name']} {v['grades']['power']}" for v in out["teams"]))
    return out


def notes(teams, n_weeks):
    """Two strengths and two weaknesses per team, in plain words."""
    rk = lambda key, v: sorted(teams.values(), key=lambda x: -(x[key] or 0)).index(v) + 1
    n = len(teams)
    for v in teams.values():
        good, bad = [], []
        if rk("talent", v) == 1:
            good.append("Most talented roster in the league")
        elif rk("talent", v) <= 3:
            good.append(f"Top-3 roster talent ({v['talent']:.0f}/wk)")
        if rk("playoff", v) <= 2:
            good.append("Built for the playoffs")
        lot = [x["left_on_table"] for x in teams.values() if x["left_on_table"] is not None]
        avg_lot = float(np.mean(lot)) if lot else 0.0
        if v["left_on_table"] is not None and v["left_on_table"] <= avg_lot - 12:
            good.append(f"Sharp lineups: {v['left_on_table']:.0f} pts/wk left on the bench (league {avg_lot:.0f})")
        elif v["left_on_table"] is not None and v["left_on_table"] >= avg_lot + 12:
            bad.append(f"Leaving {v['left_on_table']:.0f} pts/wk on the bench (league {avg_lot:.0f})")
        if (v["mgmt"]["waiver_par"] or 0) > 40:
            good.append("Strong waiver pickups")
        ds = v["mgmt"]["dead_spots"]
        if ds:
            bad.append(f"{len(ds)} droppable player{'s' if len(ds) > 1 else ''} ({ds[0]['drop']} → {ds[0]['add']}, +{ds[0]['gain']:.0f}/wk)")
        bs = v["mgmt"]["bad_starts"]
        if bs["n"] >= 3:
            bad.append(f"{bs['n']} bad starts ({bs['cost']:.0f} projected pts)")
        if v["mgmt"]["clogged"]:
            bad.append(f"{v['mgmt']['clogged'][0]['name']} injured and not on IR")
        if rk("talent", v) >= n - 1:
            bad.append("Thin roster talent")
        if rk("playoff", v) >= n - 1:
            bad.append("Weak playoff-week outlook")
        v["strengths"], v["weaknesses"] = good[:2], bad[:2]


def history(teams):
    """One snapshot per day (a rerun the same day replaces it): rank and score for movement and trends."""
    snaps = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else []
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    point = {"date": day, "teams": {t: {"rank": v["ranks"]["power"], "power": v["power"], "rank_roster": v["ranks"]["power_roster"],
                                        "rank_playoff": v["ranks"]["playoff"]} for t, v in teams.items()}}
    if snaps and snaps[-1]["date"] == day:
        snaps[-1] = point
    else:
        snaps.append(point)
    HIST.parent.mkdir(parents=True, exist_ok=True)
    HIST.write_text(json.dumps(snaps, separators=(",", ":")), encoding="utf-8")
    week_ago = snaps[-8] if len(snaps) >= 8 else snaps[0]
    for t, v in teams.items():
        v["rank_7d"] = week_ago["teams"].get(t, {}).get("rank")
        v["trend"] = [[s["date"], s["teams"][t]["rank"]] for s in snaps[-30:] if t in s["teams"]]


if __name__ == "__main__":
    build()
