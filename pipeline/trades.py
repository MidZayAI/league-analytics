"""Trades: find the league's accepted trades on ESPN and measure each one exactly.

Detection: ESPN's pending and completed transactions for the last week of scoring periods. A trade
counts once every team involved has accepted (it may still be in the league's review period) or it
has been processed; canceled, declined and vetoed proposals are ignored. Found trades are kept in
data/history/trades_{season}.json, with the analysis from when they were first seen.

Analysis (per team): the season simulation is run twice with the same random numbers, rosters
before vs after the trade, so the change in playoff / bye / title odds comes from the trade alone.
Also: change in expected points per remaining week (cap-aware), players in vs out with their
rest-of-season FP/g, and this week's impact if the trade went through now.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
OUT = ROOT / "site" / "data" / "trades.json"
HIST = ROOT / "data" / "history" / f"trades_{CURRENT}.json"
LOOKBACK = 7
DEAD = {"CANCELED", "CANCELLED", "DECLINED", "VETOED", "EXPIRED", "FAILED_INVALIDPLAYERSOURCE"}
SEED_EXP, SEED_RUN = 4242, 5151


def detect(client, sp_now):
    found = {}
    for sp in range(max(1, sp_now - LOOKBACK), sp_now + 1):
        for view in ("mPendingTransactions", "mTransactions2"):
            d = client.league(CURRENT, [view], scoring_period=sp) or {}
            for t in (d.get("transactions") or []) + (d.get("pendingTransactions") or []):
                items = [i for i in t.get("items", []) if i.get("type") == "TRADE"]
                if "TRADE" not in (t.get("type") or "") or len(items) < 2:
                    continue
                status = t.get("status")
                acts = t.get("teamActions") or {}
                teams = {i["fromTeamId"] for i in items} | {i["toTeamId"] for i in items}
                all_in = len(acts) >= len(teams) and all(v == "ACCEPTED" for v in acts.values())
                if status in DEAD or not (all_in or status in ("EXECUTED", "PROCESSED")):
                    continue
                found[t["id"]] = {"id": t["id"], "status": "Processed" if status in ("EXECUTED", "PROCESSED") else "In review",
                                  "date": datetime.fromtimestamp(t.get("proposedDate", 0) / 1000, timezone.utc).date().isoformat(),
                                  "items": [{"pid": i["playerId"], "from": i["fromTeamId"], "to": i["toTeamId"]} for i in items]}
    return list(found.values())


def analyze(trade, ctx, players, exp, roster_rows, cap_avg, names):
    """Exact before/after for one trade. players: current team -> player dicts (as in write_odds)."""
    where = {pl["pid"]: tid for tid, plist in players.items() for pl in plist}
    moves = [m for m in trade["items"] if m["pid"] in where]
    if not moves:
        return None
    processed = all(where[m["pid"]] == m["to"] for m in moves)
    teams = sorted({m["from"] for m in moves} | {m["to"] for m in moves})
    by_pid = {pl["pid"]: pl for plist in players.values() for pl in plist}

    def rosters_for(after):
        out = {tid: [pl for pl in players[tid]] for tid in teams}
        for m in moves:
            src, dst = (m["from"], m["to"]) if after else (m["to"], m["from"])
            if processed:  # current rosters are already "after"
                src, dst = dst, src
            out[src] = [pl for pl in out[src] if pl["pid"] != m["pid"]]
            if m["pid"] not in {pl["pid"] for pl in out[dst]}:
                out[dst] = out[dst] + [by_pid[m["pid"]]]
        return out

    before, after = rosters_for(False), rosters_for(True)
    exp_b = {**exp, **ctx.expected(before, seed=SEED_EXP)}
    exp_a = {**exp, **ctx.expected(after, seed=SEED_EXP)}
    res_b, res_a = ctx.run(exp_b, seed=SEED_RUN), ctx.run(exp_a, seed=SEED_RUN)
    rows = {r["id"]: r for t in roster_rows.values() for r in t["players"]}
    sides = []
    for tid in teams:
        ins = [m["pid"] for m in moves if m["to"] == tid]
        outs = [m["pid"] for m in moves if m["from"] == tid]
        info = lambda pid: {k: rows.get(pid, {}).get(k) for k in ("id", "name", "pro", "pos", "ros", "upside", "tags", "week", "inj")}
        sides.append({
            "team_id": tid, "name": names.get(tid), "in": [info(p) for p in ins], "out": [info(p) for p in outs],
            "weekly": round(ctx.weekly(exp_a, tid) - ctx.weekly(exp_b, tid), 1),
            "odds": {k: round(res_a[tid][k] - res_b[tid][k], 4) for k in ("playoff", "bye", "title")},
            "odds_after": {k: round(res_a[tid][k], 4) for k in ("playoff", "bye", "title")},
            "this_week": round(sum((rows.get(p, {}).get("week") or {}).get("pts", 0) for p in ins)
                               - sum((rows.get(p, {}).get("week") or {}).get("pts", 0) for p in outs), 1),
        })
    best = max(sides, key=lambda s: s["odds"]["title"] + s["odds"]["playoff"] / 4)
    gain = [s for s in sides if s["weekly"] > 0]
    verdict = (f"{best['name']} comes out ahead: {best['weekly']:+.0f} expected pts/week, "
               f"title odds {100 * best['odds']['title']:+.1f} pts")
    return {**trade, "processed": processed, "teams": sides, "verdict": verdict, "cap_avg": cap_avg,
            "even": len(gain) != 1 and abs(sides[0]["odds"]["title"] - sides[-1]["odds"]["title"]) < 0.01}


def build(client, sp_now, ctx, players, exp, roster_rows, cap_avg, names):
    hist = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else {}
    try:
        for t in detect(client, sp_now):
            hist.setdefault(t["id"], {"trade": t})["trade"]["status"] = t["status"]
    except Exception as e:  # transactions are a nice-to-have; keep analyzing known trades
        print(f"trades: detection failed ({e})")
    out = []
    for tid, h in sorted(hist.items(), key=lambda kv: kv[1]["trade"]["date"], reverse=True):
        a = analyze(h["trade"], ctx, players, exp, roster_rows, cap_avg, names)
        if not a:
            continue
        h.setdefault("first", {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "teams": [{k: s[k] for k in ("team_id", "weekly", "odds")} for s in a["teams"]]})
        out.append({**a, "first": h["first"]})
    HIST.parent.mkdir(parents=True, exist_ok=True)
    HIST.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    OUT.write_text(json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "trades": out},
                              default=float, separators=(",", ":")), encoding="utf-8")
    for a in out:
        print(f"trade {a['date']} ({a['status']}): " + " | ".join(
            f"{s['name']} {s['weekly']:+.0f}/wk, playoff {100 * s['odds']['playoff']:+.1f}, title {100 * s['odds']['title']:+.1f}"
            for s in a["teams"]))
    return out
