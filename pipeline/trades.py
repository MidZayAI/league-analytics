"""Trades: find the league's accepted trades on ESPN, measure each one going forward, and track how it
has panned out so far.

Detection: ESPN's pending and completed transactions for the last week of scoring periods. A trade
counts once every team involved has accepted (it may still be in the league's review period) or it
has been processed; canceled, declined and vetoed proposals are ignored. Found trades are kept in
data/history/trades_{season}.json with the analysis from when they were first seen. Whether a trade
has been processed is read from the league's daily rosters (the day the players show up on their new
teams), not from ESPN's transaction status.

Going forward (per team): the season simulation is run twice with the same random numbers, rosters
with vs without the trade, so the change in playoff / bye / title odds comes from the trade alone;
plus the change in expected points per remaining week (cap-aware).

So far (processed trades, from the daily lineup data):
  counted     fantasy points the incoming players scored in started lineups for their new team
  total       all their fantasy points for the new team, started or not
  roster spot a 2-for-1 frees a roster spot for one team: its non-IR pickups after the trade fill
              that spot (followed through drops) and their counted points are credited to the trade;
              the other team's forced drop is charged with that player's total points since
  swung       weeks where the trade's net counted points (incoming + spot pickups, minus what the
              outgoing players counted for their new team) were bigger than the final margin
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nba_logs  # noqa: E402
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
OUT = ROOT / "site" / "data" / "trades.json"
HIST = ROOT / "data" / "history" / f"trades_{CURRENT}.json"
ODDS_HIST = ROOT / "data" / "history" / f"odds_{CURRENT}.json"
LOOKBACK = 7
DEAD = {"CANCELED", "CANCELLED", "DECLINED", "VETOED", "EXPIRED", "FAILED_INVALIDPLAYERSOURCE"}
SEED_EXP, SEED_RUN = 4242, 5151
IR_SLOT = 13


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


def processed_sp(trade, daily):
    """First scoring period where every traded player is on his new team (None if not yet)."""
    sps = []
    for m in trade["items"]:
        on_new = daily[(daily.player_id == m["pid"]) & (daily.team_id == m["to"])]
        if on_new.empty:
            return None
        sps.append(int(on_new.sp.min()))
    return max(sps)


def forward(trade, ctx, players, exp, processed):
    """Exact with-vs-without trade odds and weekly expected points. Current rosters are 'with' once the
    trade is processed, 'without' before; the other version applies or reverses the moves."""
    where = {pl["pid"]: tid for tid, plist in players.items() for pl in plist}
    moves = [m for m in trade["items"] if m["pid"] in where]
    if not moves:
        return None, None
    teams = sorted({m["from"] for m in moves} | {m["to"] for m in moves})
    by_pid = {pl["pid"]: pl for plist in players.values() for pl in plist}
    current = {tid: list(players[tid]) for tid in teams}
    other = {tid: list(players[tid]) for tid in teams}
    for m in moves:
        src, dst = (m["to"], m["from"]) if processed else (m["from"], m["to"])  # undo vs apply
        other[src] = [pl for pl in other[src] if pl["pid"] != m["pid"]]
        if m["pid"] not in {pl["pid"] for pl in other[dst]}:
            other[dst] = other[dst] + [by_pid[m["pid"]]]
    with_t, without = (current, other) if processed else (other, current)
    exp_w = {**exp, **ctx.expected(with_t, seed=SEED_EXP)}
    exp_o = {**exp, **ctx.expected(without, seed=SEED_EXP)}
    res_w, res_o = ctx.run(exp_w, seed=SEED_RUN), ctx.run(exp_o, seed=SEED_RUN)
    out = {}
    for tid in teams:
        out[tid] = {"weekly": round(ctx.weekly(exp_w, tid) - ctx.weekly(exp_o, tid), 1),
                    "odds": {k: round(res_w[tid][k] - res_o[tid][k], 4) for k in ("playoff", "bye", "title")},
                    "odds_after": {k: round(res_w[tid][k], 4) for k in ("playoff", "bye", "title")}}
    return teams, out


def so_far(trade, t, p_sp, sp_now):
    """How a processed trade has played out, per team, from the league's daily lineups."""
    daily, dates = t["daily"], dict(zip(t["calendar"].sp, t["calendar"].date))
    d = daily[daily.sp >= p_sp]
    moves = trade["items"]
    teams = sorted({m["from"] for m in moves} | {m["to"] for m in moves})
    traded = {m["pid"] for m in moves}
    max_sp = int(daily.sp.max())
    logs = nba_logs.load(CURRENT)
    since_iso = dates[p_sp].isoformat()

    def counted(team, pids, sp_range=None):
        x = d[(d.team_id == team) & d.player_id.isin(pids)]
        if sp_range:
            x = x[(x.sp >= sp_range[0]) & (x.sp <= sp_range[1])]
        return x

    out = {}
    for tid in teams:
        ins = [m["pid"] for m in moves if m["to"] == tid]
        outs = [m["pid"] for m in moves if m["from"] == tid]
        mine = counted(tid, ins)
        players = []
        for pid in ins + outs:
            team = tid if pid in ins else next(m["to"] for m in moves if m["pid"] == pid)
            x = counted(team, [pid])
            gp = float(x.gp.sum())
            players.append({"id": pid, "side": "in" if pid in ins else "out",
                            "counted": round(float(x[x.counted].pts.sum()), 1), "total": round(float(x.pts.sum()), 1),
                            "gp": int(gp), "fpg": round(float(x.pts.sum()) / gp, 1) if gp else None})
        # roster spot: a team that sent out more players than it got has free spots; its non-IR pickups
        # after the trade fill them, followed through drops
        spot = []
        extra = len(outs) - len(ins)
        if extra > 0:
            team_rows = daily[daily.team_id == tid]
            first = team_rows.groupby("player_id").sp.min()
            last = team_rows.groupby("player_id").sp.max()
            adds = []
            for pid, f in first.items():
                if f < p_sp or pid in traded:
                    continue
                joined = team_rows[(team_rows.player_id == pid) & (team_rows.sp == f)]
                if joined.slot.iloc[0] == IR_SLOT:
                    continue  # went straight to IR: doesn't use a roster spot
                adds.append((int(f), int(pid), int(last[pid]), joined.name.iloc[0]))
            chains = [[] for _ in range(extra)]
            for f, pid, l, name in sorted(adds):
                for ch in chains:  # into the first chain whose holder is gone (or that's still empty)
                    if not ch or (ch[-1]["until"] < max_sp and f >= ch[-1]["until"]):
                        ch.append({"id": pid, "name": name, "from": f, "until": l})
                        break
            for ch in chains:
                for h in ch:
                    x = counted(tid, [h["id"]], (h["from"], h["until"]))
                    spot.append({**h, "since": dates[h["from"]].isoformat(), "to": dates[h["until"]].isoformat() if h["until"] < max_sp else None,
                                 "counted": round(float(x[x.counted].pts.sum()), 1), "total": round(float(x.pts.sum()), 1)})
        # forced drop: a team that got more players than it sent dropped someone around the trade
        drops = []
        if extra < 0:
            team_rows = daily[daily.team_id == tid]
            last = team_rows.groupby("player_id").sp.max()
            for pid, l in last.items():
                if pid not in traded and p_sp - 2 <= l < p_sp + 1 and l < max_sp:
                    games = [g for g in (logs.get(str(pid)) or {}).get("games", []) if g[1] >= since_iso]
                    name = team_rows[team_rows.player_id == pid].name.iloc[0]
                    drops.append({"id": int(pid), "name": name, "total": round(sum(g[4] for g in games), 1), "gp": len(games)})
        # cumulative counted points by day (incoming players + roster-spot pickups)
        cum, run = [], 0.0
        spot_ids = {s["id"]: (s["from"], s["until"]) for s in spot}
        for sp in range(p_sp, min(sp_now, max_sp) + 1):
            day = d[(d.team_id == tid) & (d.sp == sp)]
            v = day[day.player_id.isin(ins) & day.counted].pts.sum()
            v += sum(day[(day.player_id == pid) & day.counted].pts.sum() for pid, (a, b) in spot_ids.items() if a <= sp <= b)
            run += float(v)
            cum.append({"date": dates[sp].isoformat(), "pts": round(run, 1)})
        out[tid] = {"players": players, "spot": spot, "drops": drops, "cum": cum,
                    "counted": round(float(mine[mine.counted].pts.sum()), 1), "total": round(float(mine.pts.sum()), 1),
                    "spot_counted": round(sum(s["counted"] for s in spot), 1), "drop_total": round(sum(x["total"] for x in drops), 1)}

    # matchups swung: the trade's net counted points that week vs the final margin
    m = t["matchups"]
    weeks = sorted({int(x) for x in daily[daily.sp >= p_sp].mp.unique()})
    for tid in teams:
        ins = [mv["pid"] for mv in moves if mv["to"] == tid]
        outs = [mv["pid"] for mv in moves if mv["from"] == tid]
        swung = []
        for mp in weeks:
            row = m[(m.team_id == tid) & (m.mp == mp)]
            if row.empty or not isinstance(row.result.iloc[0], str) or not row.result.iloc[0]:
                continue  # week still in progress
            wk = d[d.mp == mp]
            got = wk[(wk.team_id == tid) & wk.player_id.isin(ins) & wk.counted].pts.sum()
            got += sum(wk[(wk.team_id == tid) & (wk.player_id == s["id"]) & wk.counted & (wk.sp >= s["from"]) & (wk.sp <= s["until"])].pts.sum()
                       for s in out[tid]["spot"])
            gave = sum(wk[(wk.player_id == pid) & wk.counted & (wk.team_id == next(mv["to"] for mv in moves if mv["pid"] == pid))].pts.sum()
                       for pid in outs)
            net = float(got - gave)
            margin = float(row.pts.iloc[0] - row.opp_pts.iloc[0])
            res = row.result.iloc[0]
            decided = (res == "W" and 0 < margin < net) or (res == "L" and 0 < -margin < -net)
            swung.append({"mp": mp, "result": res, "margin": round(margin, 1), "net": round(net, 1), "decided": bool(decided)})
        out[tid]["weeks"] = swung
    return out


def odds_series(teams, p_sp, t):
    if not ODDS_HIST.exists():
        return None
    h = json.loads(ODDS_HIST.read_text(encoding="utf-8"))
    mps = sorted(int(k) for k in h)
    trade_mp = int(t["daily"][t["daily"].sp == p_sp].mp.iloc[0]) if p_sp else None
    return {"mps": mps, "trade_mp": trade_mp,
            "teams": {str(tid): {k: [h[str(mp)].get(str(tid), {}).get(k) for mp in mps] for k in ("playoff", "title")} for tid in teams}}


def analyze(trade, ctx, players, exp, roster_rows, names, t, sp_now):
    p_sp = processed_sp(trade, t["daily"])
    processed = p_sp is not None
    teams, fwd = forward(trade, ctx, players, exp, processed)
    if not teams:
        return None
    rows = {r["id"]: r for tm in roster_rows.values() for r in tm["players"]}
    past = so_far(trade, t, p_sp, sp_now) if processed else None
    dates = dict(zip(t["calendar"].sp, t["calendar"].date))
    sides = []
    for tid in teams:
        info = lambda pid: {k: rows.get(pid, {}).get(k) for k in ("id", "name", "pro", "pos", "ros", "upside", "tags", "week", "inj", "gpw", "avail")}
        ins = [m["pid"] for m in trade["items"] if m["to"] == tid]
        outs = [m["pid"] for m in trade["items"] if m["from"] == tid]
        side = {"team_id": tid, "name": names.get(tid), "in": [info(p) for p in ins], "out": [info(p) for p in outs], **fwd[tid]}
        if past:
            sf = past[tid]
            since = {p["id"]: p for p in sf["players"]}
            for p in side["in"] + side["out"]:
                p["since"] = since.get(p["id"])
            side["so_far"] = {k: sf[k] for k in ("counted", "total", "spot", "drops", "cum", "spot_counted", "drop_total", "weeks")}
            # current roster-spot holder's rest-of-season value counts toward this side going forward
            holder = next((s for s in reversed(sf["spot"]) if s["to"] is None), None)
            side["spot_holder"] = {**info(holder["id"]), "since": holder["since"]} if holder else None
        sides.append(side)

    def score(s):
        return (s["so_far"]["counted"] + s["so_far"]["spot_counted"]) if s.get("so_far") else 0.0
    fwd_best = max(sides, key=lambda s: s["odds"]["title"] + s["odds"]["playoff"] / 4)
    verdict = {"forward": f"{fwd_best['name']} wins it going forward: {fwd_best['weekly']:+.0f} expected pts/week, "
                          f"title odds {100 * fwd_best['odds']['title']:+.1f} pts"}
    if past:
        a, b = sides
        lead = a if score(a) >= score(b) else b
        gap = abs(score(a) - score(b))
        verdict["so_far"] = f"So far {lead['name']} leads by {gap:.0f} counted pts since {dates[p_sp].strftime('%b')} {dates[p_sp].day}"
        verdict["split"] = lead["team_id"] != fwd_best["team_id"]
    even = abs(sides[0]["odds"]["title"] - sides[-1]["odds"]["title"]) < 0.01 and abs(sides[0]["weekly"]) < 8
    return {**trade, "status": "Processed" if processed else "In review",
            "processed_date": dates[p_sp].isoformat() if processed else None,
            "teams": sides, "verdict": verdict, "even": even,
            "odds_series": odds_series(teams, p_sp, t) if processed else None}


def build(client, sp_now, ctx, players, exp, roster_rows, cap_avg, names, t):
    hist = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else {}
    try:
        for tr in detect(client, sp_now):
            hist.setdefault(tr["id"], {"trade": tr})
    except Exception as e:  # transactions are a nice-to-have; keep analyzing known trades
        print(f"trades: detection failed ({e})")
    out = []
    for _, h in sorted(hist.items(), key=lambda kv: kv[1]["trade"]["date"], reverse=True):
        a = analyze(h["trade"], ctx, players, exp, roster_rows, names, t, sp_now)
        if not a:
            continue
        h["trade"]["status"] = a["status"]
        h.setdefault("first", {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "teams": [{k: s[k] for k in ("team_id", "weekly", "odds")} for s in a["teams"]]})
        out.append({**a, "first": h["first"], "cap_avg": cap_avg})
    HIST.parent.mkdir(parents=True, exist_ok=True)
    HIST.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    OUT.write_text(json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "trades": out},
                              default=float, separators=(",", ":")), encoding="utf-8")
    for a in out:
        print(f"trade {a['date']} ({a['status']}): " + " | ".join(
            f"{s['name']} {s['weekly']:+.0f}/wk, playoff {100 * s['odds']['playoff']:+.1f}, title {100 * s['odds']['title']:+.1f}"
            + (f", so far {s['so_far']['counted'] + s['so_far']['spot_counted']:.0f} counted" if s.get("so_far") else "")
            for s in a["teams"]))
    return out
