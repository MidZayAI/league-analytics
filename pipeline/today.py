"""Today's picks from the betting lines only (site/data/today.json).

Sportsbooks re-price players within hours of injury news and role changes, faster than any model
built on game logs. So this view uses nothing but DraftKings player props (market.py), turned into
fantasy points with the league's scoring:
  free agents   everyone with a line for a game today that hasn't tipped off, ranked by line FP
  line moved    players whose line-implied FP moved 3+ points (or 15%), or whose points line moved
                2+, since the line opened: the "why" is the lines that moved
  my bench      every league team's players with a game today, line FP (or our model's number when
                the book hasn't priced him), and the team's remaining cap games this week
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import market  # noqa: E402
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
OUT = ROOT / "site" / "data" / "today.json"
MOVE_FP, MOVE_SHARE, MOVE_PTS = 3.0, 0.15, 2.0
STAT_LABEL = {"PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK", "3PM": "3PM"}


def _tip(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else 0


def build(proj, pool, abbrev, roster, t, sp_now, starts, now, rem_caps, team_out):
    day = proj.dates[sp_now].isoformat()
    try:
        props = market.fetch_props([day])
    except Exception as e:
        print(f"today: lines unavailable ({e})")
        props = {}
    rostered = {e["playerId"]: (tm["id"], e.get("lineupSlotId")) for tm in roster["teams"] for e in tm["roster"]["entries"]}

    def line_row(aid):
        g = (props.get(aid) or {}).get(day)
        if not g or _tip(g.get("_tip")) <= now:
            return None
        sl = (proj.sleeper.get(aid) or {}).get(day)
        box = sl and sl.get("box")
        fp = market.market_fp(g, box)
        if fp is None:
            return None
        fp_open = market.market_fp(g.get("_open"), box) if g.get("_open", {}).get("PTS") is not None else None
        lines = g.get("_lines") or {}
        moved = [f"{STAT_LABEL[k]} {o:g} → {c:g}" for k, (o, c) in lines.items() if o is not None and o != c]
        delta = round(fp - fp_open, 1) if fp_open is not None else None
        pts = lines.get("PTS") or [None, None]
        alert = delta is not None and (abs(delta) >= MOVE_FP or (fp_open and abs(delta) / fp_open >= MOVE_SHARE)
                                       or (pts[0] is not None and abs(pts[1] - pts[0]) >= MOVE_PTS))
        p = pool.get(aid) or {}
        team = abbrev.get(p.get("pro"), "").upper()
        opp = next((x for x in g.get("_teams", []) if x.upper() != team), None)
        return {"id": aid, "name": p.get("name"), "team": team, "pos": p.get("pos"), "opp": opp, "tip": g.get("_tip"),
                "fp": round(fp, 1), "fp_open": round(fp_open, 1) if fp_open is not None else None, "delta": delta,
                "alert": bool(alert), "moved": moved,
                "lines": {k: v for k, v in lines.items()}, "means": {k: v for k, v in g.items() if not k.startswith("_")},
                "inj": None if p.get("inj") in (None, "ACTIVE") else p.get("inj"), "sleeper_inj": proj.sleeper_inj.get(aid),
                "owned": p.get("owned"), "owned_chg": p.get("owned_chg"), "status": p.get("status")}

    free = []
    for aid, p in pool.items():
        if p.get("on_team", 0) != 0 or aid in rostered:
            continue
        r = line_row(aid)
        if r:
            free.append(r)
    free.sort(key=lambda r: -r["fp"])

    teams = {}
    for team in roster["teams"]:
        tid = team["id"]
        cands = team_out[tid]["cands"]
        rows = []
        for e in team["roster"]["entries"]:
            pl = e["playerPoolEntry"]["player"]
            aid, pro = pl["id"], pl.get("proTeamId")
            if pro not in t["pro_games"].get(sp_now, ()) or starts.get((pro, sp_now), 0) <= now:
                continue  # no game today, or it has already started
            r = line_row(aid)
            mine = cands[(cands.player_id == aid) & (cands.sp == sp_now)]
            model = float(mine.mu.iloc[0]) if len(mine) else None
            p_play = float(mine.p.iloc[0]) if len(mine) else None
            rows.append({**(r or {"id": aid, "name": pl.get("fullName"), "team": abbrev.get(pro, "").upper(), "fp": None}),
                         "model": round(model, 1) if model is not None else None, "p": p_play,
                         "slot": e.get("lineupSlotId"), "inj": pl.get("injuryStatus") if pl.get("injuryStatus") not in (None, "ACTIVE") else None})
        rows.sort(key=lambda r: -(r["fp"] if r["fp"] is not None else (r["model"] or 0) - 0.01))
        teams[str(tid)] = {"rem_cap": int(rem_caps.get(tid, 0)), "players": rows}

    OUT.write_text(json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "date": day,
                               "lines": sum(1 for g in props.values() if day in g),
                               "free": free[:60], "moved": [r for r in free if r["alert"]][:12], "teams": teams},
                              default=float, separators=(",", ":")), encoding="utf-8")
    print(f"today: {len(free)} free agents with lines, {sum(r['alert'] for r in free)} moved; top: "
          + ", ".join(f"{r['name']} {r['fp']:.0f}" for r in free[:4]))
