"""Day-by-day win chances for every matchup on record: swings, comebacks, chokes and clutch.

For each matchup and each day, the win chance at the start of that day comes from the same model
the backtest grades (pipeline/backtest.py): scores so far, games used against the cap, that day's
rosters and each player's history up to then, nothing later. Each line ends at the real result.
This season's finished and live weeks also carry the live snapshots (every refresh) for the charts.

Per matchup: Sunday-morning win chance (start of the matchup's final day), a drama score, the winner's lowest point,
the biggest one-day move, lead changes, and per day a "why it swung" line for each side (best and
worst counted game vs that morning's expectation, regulars who sat).

Usage: python pipeline/swings.py [--rebuild]   # --rebuild recomputes finished seasons too
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import projections  # noqa: E402
from backtest import team_candidates  # noqa: E402
from espn_client import ROOT  # noqa: E402
from normalize import cached_seasons, load_season  # noqa: E402
from simulate import simulate_team, win_prob  # noqa: E402

HIST = ROOT / "data" / "history"
OUT = ROOT / "site" / "data" / "swings.json"
SIMS = 3000
VERSION = 3  # 3: NBA-wide priors (2020-21 logs)
IR_SLOT = 13
FLIP_BAND = 0.02
SHRINK = 8                # clutch grade: matchups of "average" added to every team's record          # a lead change has to cross 50% by more than this
COMEBACK, BLOWN = 0.35, 0.65
TOP = 15


def _side_why(td, est, day, games):
    """What one team did on `day`: counted points, best/worst counted game vs expectation, regulars who sat."""
    rows = td[td.sp == day]
    if rows.empty:
        return None
    counted = rows[rows.counted & (rows.gp > 0)]
    out = {"pts": round(float(rows[rows.counted].pts.sum()), 1)}
    if len(counted):
        d = counted.assign(mu=est.mu.reindex(counted.player_id).to_numpy())
        d = d.assign(delta=d.pts - d.mu.fillna(d.pts))
        hi, lo = d.loc[d.delta.idxmax()], d.loc[d.delta.idxmin()]
        out["top"] = [hi["name"], round(float(hi.pts)), round(float(hi.delta))]
        if lo.player_id != hi.player_id and lo.delta < -5:
            out["low"] = [lo["name"], round(float(lo.pts)), round(float(lo.delta))]
    sat = []
    playing = games.get(day, ())
    for r in rows[(rows.slot != IR_SLOT) & (rows.gp == 0)].itertuples():
        if r.pro in playing and r.player_id in est.index:
            e = est.loc[r.player_id]
            if e.p >= 0.7 and e.mu >= 25:
                sat.append(r.name)
    if sat:
        out["out"] = sat[:3]
    return out


def season_series(season, tables, model, params, upto=None):
    """Every matchup's start-of-day win chances for one season. upto: first day not yet started."""
    t = tables[season]
    caps = t["caps"].set_index("mp")
    games = t["pro_games"]
    date_of = {r.sp: r.date for r in t["calendar"].itertuples()}
    daily = {k: g for k, g in t["daily"].groupby(["team_id", "mp"])}
    m = t["matchups"]
    m = m[m.opp_id.notna()]
    rng = np.random.default_rng(season)
    raw_cache, out = {}, []
    for (mp, mid), pair in m.groupby(["mp", "matchup_id"]):
        if len(pair) != 2 or mp not in caps.index:
            continue
        c = caps.loc[mp]
        days = list(range(int(c.sp_start), int(c.sp_end) + 1))
        if upto is not None:
            days_run = [d for d in days if d <= upto]
        else:
            days_run = days
        if not days_run:
            continue
        a, b = pair.iloc[0], pair.iloc[1]
        tds = [daily.get((row.team_id, mp)) for row in (a, b)]
        if any(td is None for td in tds):
            continue
        pts, why = [], []
        for day in days_run:
            if day not in raw_cache:
                raw_cache[day] = model.raw_state(season, day)
            res, ests = [], []
            for td in tds:
                done = td[(td.sp < day) & td.counted]
                roster = td[td.sp == day]
                if roster.empty:
                    roster = td[td.sp == td[td.sp <= day].sp.max()]
                roster = roster[["player_id", "pro"]].drop_duplicates("player_id")
                est = model.finish(raw_cache[day], roster.player_id.tolist(), params)
                cands = team_candidates(roster, est, days[days.index(day):], games)
                sims, _, _ = simulate_team(cands, int(c.cap - done.gp.sum()), SIMS, rng, params.get("team_sd", 0.0))
                res.append((float(done.pts.sum()), sims))
                ests.append(est)
            pts.append({"d": date_of[day].isoformat(), "p": round(win_prob(res[0][0], res[0][1], res[1][0], res[1][1]), 4)})
            if upto is None or day < upto:  # the day's games are done: say what happened
                why.append([_side_why(td, est, day, games) for td, est in zip(tds, ests)])
        for pt, w in zip(pts, why):
            pt["why"] = w
        finished = isinstance(a.result, str) and (upto is None or days[-1] < upto)
        if finished:
            pts.append({"d": (date_of[days[-1]] + timedelta(days=1)).isoformat(),
                        "p": {"W": 1.0, "L": 0.0, "T": 0.5}[a.result], "final": True})
        out.append({"season": season, "mp": int(mp), "matchup_id": int(mid), "playoff": bool(a.playoff),
                    "tier": a.tier if a.playoff and isinstance(a.tier, str) else None,
                    "home": int(a.team_id), "away": int(b.team_id),
                    "home_pts": float(a.pts), "away_pts": float(b.pts),
                    "result": a.result if finished else None,
                    "start": date_of[days[0]].isoformat(), "end": date_of[days[-1]].isoformat(),
                    "series": pts})
    return out


def summarize(mu):
    """Sunday-morning chance, drama ingredients, lead changes and key moments for one matchup (home's view)."""
    s = mu["series"]
    ps = [x["p"] for x in s]
    pre = [x["p"] for x in s if not x.get("final")]
    # Sunday morning = the start of the matchup's final day (every matchup period ends on a Sunday)
    sun = [i for i, x in enumerate(s) if x["d"] == mu["end"] and not x.get("final")]
    mu["sun_i"] = sun[0] if sun else None
    mu["sun_p"] = ps[mu["sun_i"]] if sun and mu["result"] else None
    mu["swing"] = round(max(ps) - min(ps), 4)
    moves = [(ps[i + 1] - ps[i], i) for i in range(len(ps) - 1)]
    mu["total_move"] = round(sum(abs(d) for d, _ in moves), 4)
    big = max(moves, key=lambda x: abs(x[0])) if moves else (0.0, 0)
    mu["move"], mu["move_i"] = round(big[0], 4), big[1]
    side, flips = None, 0
    for p in ps:
        now = 1 if p > 0.5 + FLIP_BAND else -1 if p < 0.5 - FLIP_BAND else None
        if now is not None:
            flips += side is not None and now != side
            side = now
    mu["lead_changes"] = flips
    if mu["result"] in ("W", "L"):
        home_won = mu["result"] == "W"
        mu["winner"] = mu["home"] if home_won else mu["away"]
        mu["loser"] = mu["away"] if home_won else mu["home"]
        if mu["sun_p"] is not None:
            mu["winner_sun"] = round(mu["sun_p"] if home_won else 1 - mu["sun_p"], 4)
        mu["winner_min"] = round(min(pre) if home_won else 1 - max(pre), 4)
        mu["margin"] = abs(mu["home_pts"] - mu["away_pts"])
        # the winner's best counted game on the final day (Mr. Clutch candidates)
        last = s[mu["sun_i"]] if mu["sun_i"] is not None else None
        why = (last or {}).get("why") or [None, None]
        w = why[0 if home_won else 1]
        if w and w.get("top"):
            mu["sunday_hero"] = w["top"]
    # raw drama: how much it moved, how close it was going into Sunday, how often the lead flipped
    close = 1 - 2 * abs(mu["sun_p"] - 0.5) if mu["sun_p"] is not None else 0.0
    mu["_drama"] = 0.4 * min(1.0, mu["total_move"] / 2) + 0.3 * close + 0.3 * min(flips, 3) / 3
    return mu


def drama_pct(ms):
    """Drama 0-100: percentile of the raw drama score among every finished matchup on record."""
    ref = sorted(m["_drama"] for m in ms if m["result"])
    n = len(ref) or 1
    for m in ms:
        v = m.pop("_drama")
        below = sum(x < v for x in ref)
        same = sum(x == v for x in ref)
        m["drama"] = round(100 * (below + 0.5 * same) / n)


def clutch_table(ms, owner_of):
    """Per franchise (owner): wins above what the Sunday-morning chances expected, comeback wins, blown leads.
    owner_of[(season, team_id)] -> owner; team ids get reused when a team changes hands."""
    rows = {}
    for m in ms:
        if m["sun_p"] is None:
            continue
        y = {"W": 1.0, "L": 0.0, "T": 0.5}[m["result"]]
        for tid, p, r in ((m["home"], m["sun_p"], y), (m["away"], 1 - m["sun_p"], 1 - y)):
            own = owner_of.get((m["season"], tid), f"{m['season']}-{tid}")
            t = rows.setdefault(own, {"owner": own, "team_id": tid, "season": m["season"], "n": 0, "clutch": 0.0,
                                      "comebacks": 0, "blown": 0, "wins": 0.0, "exp": 0.0})
            if m["season"] >= t["season"]:  # label with the franchise's latest team name
                t["team_id"], t["season"] = tid, m["season"]
            t["n"] += 1
            t["wins"] += r
            t["exp"] += p
            t["clutch"] += r - p
            t["comebacks"] += int(p < COMEBACK and r == 1)
            t["blown"] += int(p > BLOWN and r == 0)
    out = sorted(rows.values(), key=lambda t: -t["clutch"])
    for t in out:
        # per matchup, shrunk toward 0 so a few weeks can't earn an A+ or F on luck alone
        t["rate"] = round(t["clutch"] / (t["n"] + SHRINK), 4)
        t["clutch"], t["exp"] = round(t["clutch"], 2), round(t["exp"], 2)
    return out


def grade_rows(rows, ref):
    """Letter grade from the percentile of wins-above-odds per matchup among `ref` rates."""
    from draft import letter
    n = len(ref) or 1
    for t in rows:
        below = sum(v < t["rate"] for v in ref)
        same = sum(v == t["rate"] for v in ref)
        t["pct"] = round((below + 0.5 * same) / n, 3)
        t["grade"] = letter(t["pct"])


def attach_live(ms, season):
    """This season: the live win-chance snapshots (every refresh) for the chart, and as the
    start-of-day chances, since they're what the site actually showed that morning."""
    path = HIST / f"winprob_{season}.json"
    snaps = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    by = {(m["mp"], m["matchup_id"]): m for m in ms}
    for s in snaps:
        for mid, probs in s["probs"].items():
            m = by.get((s["mp"], int(mid)))
            if m and str(m["home"]) in probs:
                m.setdefault("live", []).append([int(s["ts"]), probs[str(m["home"])]])
    # one source of truth: each day's point = the live forecast the site showed first that day (ET),
    # and the latest point of a week in progress = the current live forecast
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    for m in ms:
        pts = m.get("live") or []
        if not pts:
            continue
        first = {}
        for ts, p in sorted(pts):
            first.setdefault(datetime.fromtimestamp(ts, et).date().isoformat(), p)
        for x in m["series"]:
            if not x.get("final") and x["d"] in first:
                x["p"] = round(first[x["d"]], 4)
        if not m["result"]:
            m["series"][-1]["p"] = round(sorted(pts)[-1][1], 4)
    return ms


def build(rebuild=False):
    seasons = cached_seasons()
    current = max(seasons)
    tables = {s: load_season(s) for s in seasons}
    model = projections.HistoryModel(tables)
    params = dict(projections.PARAMS)
    all_ms = []
    for s in seasons:
        cache = HIST / f"winprob_daily_{s}.json"
        done = tables[s]["complete"]
        if done and cache.exists() and not rebuild:
            c = json.loads(cache.read_text(encoding="utf-8"))
            if c.get("version") == VERSION and c.get("params") == params:
                all_ms += c["matchups"]
                continue
        upto = None
        if not done:
            played = tables[s]["daily"]
            upto = int(played.sp.max()) if len(played) else 0
            if upto == 0:
                continue
            # the last stored day is today (rosters known, games not final): forecast it, no recap yet
        ms = season_series(s, tables, model, params, upto=upto)
        if done:
            cache.write_text(json.dumps({"version": VERSION, "params": params, "matchups": ms},
                                        separators=(",", ":")), encoding="utf-8")
        all_ms += ms
        print(f"  swings {s}: {len(ms)} matchups")
    attach_live([m for m in all_ms if m["season"] == current], current)
    all_ms = [summarize(m) for m in all_ms]
    drama_pct(all_ms)
    names = {str(s): {str(k): v for k, v in zip(t["teams"].team_id, t["teams"]["name"])} for s, t in tables.items()}
    owner_of = {(s, int(k)): v for s, t in tables.items() for k, v in zip(t["teams"].team_id, t["teams"].owner_id)}
    owners = {str(s): {str(k): v for k, v in zip(t["teams"].team_id, t["teams"].owner_id)} for s, t in tables.items()}
    per_season = {str(s): clutch_table([m for m in all_ms if m["season"] == s], owner_of) for s in seasons}
    # team-season grades vs every team-season on record (min 5 matchups); all-time vs the franchises
    ref = [t["rate"] for s_, rows in per_season.items() for t in rows if int(s_) != current or tables[current]["complete"]]
    for rows in per_season.values():
        grade_rows(rows, ref)
    alltime = clutch_table(all_ms, owner_of)
    grade_rows(alltime, [t["rate"] for t in alltime])
    for t in alltime:  # a franchise with a handful of matchups (new owner) isn't graded all-time
        if t["n"] < 20:
            t["grade"] = None
    out = {"seasons": seasons, "current": current, "team_names": names, "owners": owners,
           "matchups": all_ms,
           "clutch": alltime, "clutch_by_season": per_season,
           "thresholds": {"comeback": COMEBACK, "blown": BLOWN, "flip_band": FLIP_BAND}}
    OUT.write_text(json.dumps(out, separators=(",", ":"), default=float), encoding="utf-8")
    return out


if __name__ == "__main__":
    import time
    t0 = time.time()
    o = build("--rebuild" in sys.argv)
    print(f"{len(o['matchups'])} matchups in {time.time() - t0:.0f}s")
