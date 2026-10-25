"""Live forecast for the current matchup week -> site/data/live.json (+ win-probability snapshots).

Usage: python pipeline/live.py     (build.py calls this when the current season is in progress)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expected  # noqa: E402
import projections as pj  # noqa: E402
from espn_client import ESPNClient, ROOT  # noqa: E402
from normalize import RAW, cached_seasons, load_season  # noqa: E402
from simulate import SIMS, simulate_team, win_prob  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
SNAP_DIR = ROOT / "data" / "history"
OUT = ROOT / "site" / "data" / "live.json"
SNAP_MIN_GAP = 9 * 60  # seconds between stored win-probability snapshots (refresh runs every 10 min)


def split(stats, season, source, split_type, sp=0):
    for s in stats or []:
        if (s.get("seasonId"), s.get("statSourceId"), s.get("statSplitTypeId"), s.get("scoringPeriodId")) == (season, source, split_type, sp):
            return s.get("appliedTotal") or 0.0, (s.get("stats") or {}).get("42") or 0.0
    return 0.0, 0.0


def per_game(total_gp):
    total, gp = total_gp
    return (total / gp if gp else np.nan), gp


def game_starts(proschedule):
    """(pro team id, sp) -> game start (epoch seconds)."""
    out = {}
    for t in proschedule["settings"]["proTeams"]:
        for sp, games in (t.get("proGamesByScoringPeriod") or {}).items():
            for g in games:
                out[(t["id"], int(sp))] = g["date"] / 1000
    return out


def play_chance(status, days_ahead, base):
    if status in pj.STATUS_PLAY:
        if status in ("OUT", "INJURY_RESERVE"):
            return pj.OUT_RETURN * base if days_ahead >= 3 else 0.0
        if status == "DAY_TO_DAY":
            return pj.STATUS_PLAY[status] if days_ahead <= 1 else base
        return pj.STATUS_PLAY[status]
    return base


def histogram(values, bins=40):
    counts, edges = np.histogram(values, bins=bins)
    return {"edges": [round(float(e), 1) for e in edges], "counts": counts.tolist()}


def main():
    now = time.time()
    c = ESPNClient()
    league = c.league(CURRENT, ["mMatchupScore", "mLiveScoring", "mStatus", "mTeam"])
    status = league["status"]
    t = load_season(CURRENT)
    caps = t["caps"].set_index("mp")
    mp = status.get("currentMatchupPeriod")
    sp_now = status.get("latestScoringPeriod")
    if not mp or mp not in caps.index or t["complete"]:
        OUT.write_text(json.dumps({"active": False}), encoding="utf-8")
        print("live: no matchup in progress")
        return

    cap_row = caps.loc[mp]
    week = list(range(int(cap_row.sp_start), int(cap_row.sp_end) + 1))
    dates = dict(zip(t["calendar"].sp, t["calendar"].date))
    pro_raw = json.loads((RAW / str(CURRENT) / "proschedule.json").read_text(encoding="utf-8"))
    starts = game_starts(pro_raw)
    abbrev = {p["id"]: p.get("abbrev", "").upper() for p in pro_raw["settings"]["proTeams"]}

    # history-based play rates + cv (current season so far, previous seasons for the prior)
    model = pj.HistoryModel({s: load_season(s) for s in cached_seasons() if s >= CURRENT - 1})
    raw_state = model.raw_state(CURRENT, sp_now)
    P = pj.PARAMS
    exp_ctx = expected.SeasonContext(CURRENT, t, model)
    done_days = [sp for sp in week if sp < sp_now]

    roster = c.league(CURRENT, ["mRoster"], scoring_period=sp_now)
    daily = t["daily"]
    names = dict(zip(t["teams"].team_id, t["teams"].name))
    rng = np.random.default_rng()

    team_out = {}
    for team in roster["teams"]:
        tid = team["id"]
        rows, grid = [], []
        for e in team["roster"]["entries"]:
            pl = e["playerPoolEntry"]["player"]
            pid, pro = pl["id"], pl.get("proTeamId")
            st = pl["stats"]
            season_avg, n = per_game(split(st, CURRENT, 0, 0))
            recent_avg, n15 = per_game(split(st, CURRENT, 0, 2))
            proj_pg, proj_gp = per_game(split(st, CURRENT, 1, 0))
            prev_avg, prev_gp = per_game(split(st, CURRENT - 1, 0, 0))
            prior = proj_pg if proj_gp else (prev_avg if prev_gp >= 10 else model.repl)
            mu = float(pj.blend_mean(prior, season_avg, n, recent_avg, n15, P["k"], P["r"]))
            sd = float(pj.spread(mu, model.cv))
            hist = raw_state.loc[pid] if pid in raw_state.index else None
            base = float(pj.play_prob(hist.played if hist is not None else 0,
                                      hist.opps if hist is not None else 0, P["p_base"], P["p_k"]))
            inj = pl.get("injuryStatus") or "ACTIVE"

            days = []
            for sp in week:
                has_game = (pro, sp) in starts
                if sp < sp_now or (sp == sp_now and starts.get((pro, sp), 0) <= now):
                    if sp == sp_now:
                        pts, gp = split(st, CURRENT, 0, 5, sp)
                        counted = e.get("lineupSlotId") not in (12, 13)
                    else:
                        d = daily[(daily.team_id == tid) & (daily.sp == sp) & (daily.player_id == pid)]
                        pts, gp = (float(d.pts.iloc[0]), float(d.gp.iloc[0])) if len(d) else (0.0, 0.0)
                        counted = bool(d.counted.iloc[0]) if len(d) else False
                    days.append({"sp": sp, "state": "played" if gp else ("missed" if has_game else "none"),
                                 "pts": pts if gp else None, "counted": bool(counted and gp)})
                elif has_game:
                    p = play_chance(inj, sp - sp_now, base)
                    rows.append({"sp": sp, "mu": mu, "sd": sd, "p": p, "player_id": pid})
                    days.append({"sp": sp, "state": "game", "p": p})
                else:
                    days.append({"sp": sp, "state": "none"})
            grid.append({"id": pid, "name": pl.get("fullName"), "pro": abbrev.get(pro, ""), "inj": inj,
                         "slot": e.get("lineupSlotId"), "mu": mu, "days": days})

        cands = pd.DataFrame(rows, columns=["sp", "mu", "sd", "p", "player_id"])
        team_out[tid] = {"cands": cands, "grid": grid}

    snaps_path = SNAP_DIR / f"winprob_{CURRENT}.json"
    snaps = json.loads(snaps_path.read_text(encoding="utf-8")) if snaps_path.exists() else []
    matchups, snap = [], {}
    for m in league["schedule"]:
        if m.get("matchupPeriodId") != mp or not m.get("away"):
            continue
        sides = []
        for key in ("home", "away"):
            s = m[key]
            tid = s["teamId"]
            live = s.get("cumulativeScoreLive") or s.get("cumulativeScore") or {}
            gp_used = ((live.get("statBySlot") or {}).get("15") or {}).get("value") or 0
            cur = s.get("totalPointsLive", s.get("totalPoints")) or 0.0
            rem_cap = int(cap_row.cap - gp_used)
            sims, start_p, games = simulate_team(team_out[tid]["cands"], rem_cap, SIMS, rng, P.get("team_sd", 0.0))
            cands = team_out[tid]["cands"].assign(start=start_p)
            for g in team_out[tid]["grid"]:
                for d in g["days"]:
                    if d["state"] == "game":
                        hit = cands[(cands.player_id == g["id"]) & (cands.sp == d["sp"])]
                        d["start"] = float(hit.start.iloc[0]) if len(hit) else 0.0
            final = cur + sims
            so_far = None
            if done_days:
                res = expected.week_breakdown(exp_ctx, tid, int(mp), week, int(cap_row.cap), rng, done_days)
                if res:
                    so_far, players = res
                    so_far["hot"], so_far["cold"] = expected.top_players(players)
            sides.append({"team_id": tid, "name": names.get(tid), "pts": cur, "gp_used": gp_used,
                          "cap": int(cap_row.cap), "rem_cap": rem_cap,
                          "avail_games": float(cands.p.sum()), "exp_games": float(games.mean()),
                          "proj": float(final.mean()), "p10": float(np.percentile(final, 10)),
                          "p90": float(np.percentile(final, 90)), "_final": final, "expected_so_far": so_far,
                          "players": sorted(team_out[tid]["grid"], key=lambda g: -g["mu"])})
        a, b = sides
        fa, fb = a.pop("_final"), b.pop("_final")
        pa = win_prob(a["pts"], fa - a["pts"], b["pts"], fb - b["pts"])
        a["win_prob"], b["win_prob"] = pa, 1 - pa
        matchups.append({"matchup_id": m["id"], "home": a, "away": b,
                         "proj_margin": a["proj"] - b["proj"], "margin_hist": histogram(fa - fb)})
        snap[str(m["id"])] = {str(a["team_id"]): round(pa, 4), str(b["team_id"]): round(1 - pa, 4)}

    # keep one point per SNAP_MIN_GAP window; a rerun inside the window replaces the latest point
    point = {"ts": now, "sp": sp_now, "mp": int(mp), "probs": snap}
    anchor = snaps[-1].get("anchor", snaps[-1]["ts"]) if snaps else None
    if snaps and now - anchor < SNAP_MIN_GAP and snaps[-1]["sp"] == sp_now:
        snaps[-1] = point | {"anchor": anchor}
    else:
        snaps.append(point)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snaps_path.write_text(json.dumps(snaps, separators=(",", ":")), encoding="utf-8")

    out = {
        "active": True, "season": CURRENT, "mp": int(mp), "sp_now": sp_now, "cap": int(cap_row.cap),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "days": [{"sp": sp, "date": dates[sp].isoformat(), "game_day": sp in t["pro_games"]} for sp in week],
        "matchups": matchups, "params": P, "sims": SIMS,
        "snapshots": [s for s in snaps if s["mp"] == mp],
    }
    OUT.write_text(json.dumps(out, default=float, separators=(",", ":")), encoding="utf-8")
    print(f"live: week {mp}, day {sp_now}: " + ", ".join(
        f"{x['home']['name']} {x['home']['win_prob']:.0%} vs {x['away']['name']}" for x in matchups))


if __name__ == "__main__":
    main()
