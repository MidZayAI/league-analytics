"""Draft and waiver-wire value, WAR-style (site/data/draft.json).

  replacement   per season, over the same dates: the average fantasy points per game of the players
                ranked just outside the league's rostered pool (ranks teams x rounds + 1 .. +16) among
                players who played 40%+ of the games in that window: roughly the best free agent you
                could have grabbed
  PAR           points above replacement: sum over games of (FP - replacement FP/game)
                draft:   every game a drafted player played while on the drafting team (his production,
                         started or not; lineup decisions are rated by Coaching)
                waivers: only games that counted in the lineup, while the pickup was on the roster
  WAR           PAR / points per win; points per win = sd(weekly margin) x sqrt(2 pi), i.e. the extra
                points a week that turn one expected loss into a win at an even matchup
  vs slot       a pick's PAR minus what that draft slot usually delivers to its drafter over the same
                window (a fit of credited PAR per team-game on ln(overall pick) across completed seasons)

At a glance, "played like": every NBA player is ranked by total fantasy points over the same window
(his whole season, whichever team he ended up on). Spots gained = overall pick - that rank, with the
rank capped at the replacement band (an unrosterable bust counts as replacement level, not rank 300).
A hit = a pick that outperformed its slot. Teams get a letter grade from their spots gained per pick,
as a percentile of every team-draft on record (A 85th+, B 60th+, C 35th+, D 15th+, else F).

Pick numbers: ESPN's offline-draft records have the right player on the right team but can have the
wrong pick numbers. config/draft_order_{season}.json (transcribed from the league's draft board) sets
the real overall pick for every player; otherwise seasons in config/draft_rules.json get the
third-round-reversal fix (rounds 3+: real spot within the round = teams + 1 - ESPN's spot).

Usage: python pipeline/draft.py
"""
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nba_logs  # noqa: E402
from espn_client import ESPNClient, ROOT  # noqa: E402
from normalize import RAW, cached_seasons, load_season  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
OUT = ROOT / "site" / "data" / "draft.json"
RULES = ROOT / "config" / "draft_rules.json"
REPL_BAND = 16
MIN_SHARE = 0.4


def picks_for(season):
    path = RAW / str(season) / "draft.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    d = ESPNClient().league(season, ["mDraftDetail", "mSettings"]) or {}
    picks = (d.get("draftDetail") or {}).get("picks") or []
    if not picks:
        return None
    out = {"type": ((d.get("settings") or {}).get("draftSettings") or {}).get("type"),
           "picks": [{k: p.get(k) for k in ("overallPickNumber", "roundId", "roundPickNumber", "teamId", "playerId", "keeper")}
                     for p in picks]}
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return out


def _norm(name):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def fix_order(picks, season, n_teams, names=None):
    """Real pick numbers: from the league's draft board when transcribed, else the third-round-reversal
    fix for seasons that used it, else ESPN's. Returns (picks, how) with how in board/reversal/espn."""
    board = ROOT / "config" / f"draft_order_{season}.json"
    if board.exists() and names:
        cfg = json.loads(board.read_text(encoding="utf-8"))
        overall_of = {_norm(n): ov for slot in cfg["slots"] for ov, n in slot}
        out = []
        for p in picks:
            ov = overall_of.get(_norm(names.get(p["playerId"])))
            if ov is None:
                raise ValueError(f"draft board {season}: no pick for {names.get(p['playerId'])}")
            r = (ov - 1) // n_teams + 1
            out.append({**p, "round": r, "spot": ov - (r - 1) * n_teams, "overall": ov})
        if sorted(p["overall"] for p in out) != list(range(1, len(out) + 1)):
            raise ValueError(f"draft board {season}: overall picks don't run 1..{len(out)}")
        return sorted(out, key=lambda p: p["overall"]), "board"
    rules = json.loads(RULES.read_text(encoding="utf-8")) if RULES.exists() else {}
    flip = season in set(rules.get("third_round_reversal", []))
    out = []
    for p in picks:
        r, spot = p["roundId"], p["roundPickNumber"]
        if flip and r >= 3:
            spot = n_teams + 1 - spot
        out.append({**p, "round": r, "spot": spot, "overall": (r - 1) * n_teams + spot})
    return sorted(out, key=lambda p: p["overall"]), ("reversal" if flip else "espn")


def stints(daily):
    """One row per run of consecutive days a player was on a team: player, team, first/last sp, how acquired."""
    d = daily.sort_values(["player_id", "sp"])[["player_id", "team_id", "sp"]].drop_duplicates()
    rows = []
    for pid, g in d.groupby("player_id"):
        prev_team, prev_sp, start = None, None, None
        for team, sp in zip(g.team_id, g.sp):
            if team != prev_team or sp != prev_sp + 1:
                if prev_team is not None:
                    rows.append((pid, prev_team, start, prev_sp))
                start = sp
            prev_team, prev_sp = team, sp
        rows.append((pid, prev_team, start, prev_sp))
    s = pd.DataFrame(rows, columns=["player_id", "team_id", "first", "last"])
    s["prev_team"] = s.groupby("player_id").team_id.shift()
    s["prev_last"] = s.groupby("player_id")["last"].shift()
    return s


def replacement(season, d0, d1, rostered):
    """Replacement-level FP/game over [d0, d1] (ISO dates) from NBA-wide logs."""
    logs = nba_logs.load(season)
    rows = [(aid, fp) for aid, a in logs.items() for _, day, _, mins, fp in a["games"] if d0 <= day <= d1 and mins]
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["aid", "fp"]).groupby("aid").fp.agg(["mean", "size"])
    team_games = df["size"].quantile(0.95)  # games a regular played in the window
    df = df[df["size"] >= MIN_SHARE * team_games].sort_values("mean", ascending=False)
    band = df["mean"].iloc[rostered: rostered + REPL_BAND]
    return float(band.mean()) if len(band) else None


def season_ranks(season, d0, d1):
    """Every NBA player's total fantasy points over [d0, d1] and his rank by them (1 = most)."""
    tot = {}
    for aid, a in nba_logs.load(season).items():
        g = [fp for _, day, _, mins, fp in a["games"] if d0 <= day <= d1 and mins]
        if g:
            tot[int(aid)] = (float(sum(g)), len(g))
    order = sorted(tot, key=lambda a: -tot[a][0])
    return tot, {a: i + 1 for i, a in enumerate(order)}


def points_per_win(m):
    reg = m[~m.playoff & m.result.isin(["W", "L", "T"])]
    margin = (reg.pts - reg.opp_pts).to_numpy(float)
    return float(np.std(margin) * math.sqrt(2 * math.pi)) if len(margin) > 8 else None


def season_window_team_games(season, d0, d1):
    logs = nba_logs.load(season)
    games = {}
    for a in logs.values():
        for ev, day, team, mins, fp in a["games"]:
            if d0 <= day <= d1:
                games.setdefault(team, set()).add(ev)
    return float(np.median([len(v) for v in games.values()])) if games else 0.0


def analyze_season(season):
    t = load_season(season)
    daily = t["daily"]
    if daily.empty:
        return None
    teams = t["teams"]
    n_teams = len(teams)
    raw = picks_for(season)
    if not raw:
        return None
    names = dict(zip(daily.player_id, daily.name))
    for p in raw["picks"]:
        names.setdefault(p["playerId"], _logname(season, p["playerId"]))
    picks, how = fix_order(raw["picks"], season, n_teams, names)
    rounds = max(p["round"] for p in picks)
    dates = dict(zip(t["calendar"].sp, t["calendar"].date))
    sp0, sp1 = int(daily.sp.min()), int(daily.sp.max())
    d0, d1 = dates[sp0].isoformat(), dates[sp1].isoformat()
    repl = replacement(season, d0, d1, n_teams * rounds)
    ppw = points_per_win(t["matchups"])
    if repl is None or not ppw:
        return None
    tg = season_window_team_games(season, d0, d1)
    season_fp, rank = season_ranks(season, d0, d1)
    cap = n_teams * rounds + REPL_BAND
    tname = dict(zip(teams.team_id, teams.name))
    st = stints(daily)
    played = daily[daily.gp > 0]

    def prod(pid, team, a, b, counted_only=False):
        x = played[(played.player_id == pid) & (played.team_id == team) & (played.sp >= a) & (played.sp <= b)]
        if counted_only:
            x = x[x.counted]
        return float(x.pts.sum()), int(x.gp.sum())

    # ---- draft picks: production while on the drafting team (all stints there that began as the draft one)
    pick_rows = []
    for p in picks:
        pid, team = p["playerId"], p["teamId"]
        mine = st[(st.player_id == pid) & (st.team_id == team)].sort_values("first")
        first_stint = mine[mine["first"] == sp0] if len(mine) else mine
        if first_stint.empty:
            stint = None
            fp, gp = 0.0, 0
        else:
            stint = first_stint.iloc[0]
            fp, gp = prod(pid, team, int(stint["first"]), int(stint["last"]))
        par = fp - repl * gp
        status, when = "rostered", None
        if stint is not None and int(stint["last"]) < sp1:
            nxt = st[(st.player_id == pid) & (st["first"] == int(stint["last"]) + 1)]
            status = "traded" if len(nxt) else "dropped"
            when = dates[int(stint["last"]) + 1].isoformat()
        elif stint is None:
            status = "never rostered"
        pick_rows.append({"overall": p["overall"], "round": p["round"], "spot": p["spot"], "espn_overall": p["overallPickNumber"],
                          "team_id": int(team), "player_id": int(pid), "name": names.get(pid) or _logname(season, pid),
                          "gp": gp, "fp": round(fp, 1), "fpg": round(fp / gp, 1) if gp else None,
                          "par": round(par, 1), "war": round(par / ppw, 2), "status": status, "left": when,
                          # played like: his whole season (any team), ranked by total fantasy points
                          "season_fp": round(season_fp.get(pid, (0.0, 0))[0], 1), "season_gp": season_fp.get(pid, (0.0, 0))[1],
                          "rank": rank.get(pid), "spots": p["overall"] - min(rank.get(pid, cap), cap),
                          "hit": rank.get(pid, cap + 1) <= p["overall"]})
    out = {"season": season, "teams": n_teams, "rounds": rounds, "reversal_fixed": how == "reversal", "order_source": how, "cap_rank": cap, "replacement": round(repl, 1),
           "points_per_win": round(ppw, 1), "window": [d0, d1], "team_games": tg, "complete": bool(t["complete"]),
           "order": [p["team_id"] for p in sorted(pick_rows, key=lambda r: r["overall"]) if p["round"] == 1],
           "picks": pick_rows, "team_names": {str(k): v for k, v in tname.items()},
           "owners": {str(k): v for k, v in zip(teams.team_id, teams.owner_id)}}

    # ---- waiver / free-agent pickups: counted games above replacement while on the roster
    adds = []
    drafted = {(p["playerId"], p["teamId"]) for p in picks}
    for r in st.itertuples():
        if r.first == sp0 and (r.player_id, r.team_id) in drafted:
            continue
        from_trade = pd.notna(r.prev_team) and r.prev_last == r.first - 1 and r.prev_team != r.team_id
        if from_trade or r.first == sp0:
            continue
        fp, gp = prod(r.player_id, r.team_id, int(r.first), int(r.last), counted_only=True)
        tot, tgp = prod(r.player_id, r.team_id, int(r.first), int(r.last))
        par = fp - repl * gp
        adds.append({"team_id": int(r.team_id), "player_id": int(r.player_id), "name": names.get(r.player_id),
                     "added": dates[int(r.first)].isoformat(), "dropped": dates[int(r.last) + 1].isoformat() if r.last < sp1 else None,
                     "counted_gp": gp, "counted_fp": round(fp, 1), "par": round(par, 1), "war": round(par / ppw, 2),
                     "gp": tgp, "fp": round(tot, 1)})
    # "played like the #N player": his FP/game while on the roster vs the season's regulars
    pg = sorted((tot_ / g_ for tot_, g_ in season_fp.values() if g_ >= MIN_SHARE * tg), reverse=True)
    for a in adds:
        a["fpg"] = round(a["fp"] / a["gp"], 1) if a["gp"] else None
        a["pg_rank"] = (1 + sum(v > a["fpg"] for v in pg)) if a["gp"] >= 3 else None
    out["adds"] = sorted(adds, key=lambda a: -a["par"])
    reg = t["matchups"][~t["matchups"].playoff & t["matchups"].result.isin(["W", "L", "T"])]
    out["weeks_done"] = int(reg.mp.nunique())
    return out


def _logname(season, pid):
    a = nba_logs.load(season).get(str(pid))
    return a.get("name") if a else str(pid)


def fit_slot(seasons):
    """Credited PAR per team-game ~ a + b ln(pick, scaled to 8 teams), on completed seasons."""
    xs, ys = [], []
    for s in seasons:
        if not s["complete"]:
            continue
        for p in s["picks"]:
            xs.append(math.log(p["overall"] * 8 / s["teams"]))
            # credited PAR per team-game: what drafters typically keep from a slot (drops and trades
            # included), so "vs slot" averages zero for a typical pick
            ys.append(p["par"] / s["team_games"] if s["team_games"] else 0.0)
    if len(xs) < 20:
        return None
    b, a = np.polyfit(xs, ys, 1)
    return {"a": float(a), "b": float(b), "n": len(xs)}


def team_tables(s, fit):
    picks = s["picks"]
    for p in picks:
        exp_rate = (fit["a"] + fit["b"] * math.log(p["overall"] * 8 / s["teams"])) if fit else 0.0
        exp = exp_rate * s["team_games"]
        p["expected_par"] = round(exp, 1)
        p["vs_slot"] = round(p["par"] - exp, 1)
        p["vs_slot_war"] = round((p["par"] - exp) / s["points_per_win"], 2)
    # center within the season: "vs slot" = versus a typical pick that year (league-wide scoring varies by year)
    shift = float(np.mean([p["vs_slot"] for p in picks])) if picks else 0.0
    for p in picks:
        p["vs_slot"] = round(p["vs_slot"] - shift, 1)
        p["vs_slot_war"] = round(p["vs_slot"] / s["points_per_win"], 2)
    grades = []
    for tid in sorted({p["team_id"] for p in picks}):
        mine = [p for p in picks if p["team_id"] == tid]
        groups = {"1-3": [1, 3], "4-7": [4, 7], "8+": [8, 99]}
        grades.append({"spots": sum(p["spots"] for p in mine), "spots_per_pick": round(sum(p["spots"] for p in mine) / len(mine), 2),
                       "hits": sum(p["hit"] for p in mine), "season_fp": round(sum(p["season_fp"] for p in mine), 1),
                       "by_round": {k: sum(p["spots"] for p in mine if a <= p["round"] <= b) for k, (a, b) in groups.items()},
                       "best_spots": max(mine, key=lambda p: p["spots"])["player_id"],
                       "worst_spots": min(mine, key=lambda p: p["spots"])["player_id"],
                       "team_id": tid, "par": round(sum(p["par"] for p in mine), 1),
                       "war": round(sum(p["war"] for p in mine), 2),
                       "vs_slot": round(sum(p["vs_slot"] for p in mine), 1),
                       "vs_slot_war": round(sum(p["vs_slot_war"] for p in mine), 2),
                       "best": max(mine, key=lambda p: p["vs_slot"])["player_id"],
                       "worst": min(mine, key=lambda p: p["vs_slot"])["player_id"],
                       "still": sum(p["status"] == "rostered" for p in mine), "picks": len(mine)})
    # team totals read best against the league that year (injuries and breakout free agents pull every
    # draft's raw total down)
    avg = float(np.mean([g["spots"] for g in grades])) if grades else 0.0
    for g in grades:
        g["vs_avg"] = round(g["spots"] - avg)
    grades.sort(key=lambda g: -g["spots"])
    for i, g in enumerate(grades):
        g["rank"] = i + 1
    s["grades"] = grades
    wv = []
    for tid in sorted({p["team_id"] for p in picks}):
        mine = [a for a in s["adds"] if a["team_id"] == tid]
        weeks = max(s.get("weeks_done") or 1, 1)
        wv.append({"team_id": tid, "adds": len(mine), "par": round(sum(a["par"] for a in mine), 1),
                   "war": round(sum(a["war"] for a in mine), 2),
                   "counted_fp": round(sum(a["counted_fp"] for a in mine), 1),
                   "hits": sum(a["par"] > 0 for a in mine), "used": sum(a["counted_gp"] > 0 for a in mine),
                   "par_per_week": round(sum(a["par"] for a in mine) / weeks, 1),
                   "best": max(mine, key=lambda a: a["par"])["player_id"] if mine else None,
                   "worst": min((a for a in mine if a["counted_gp"] > 0), key=lambda a: a["par"], default={"player_id": None})["player_id"]})
    avg = float(np.mean([w["par"] for w in wv])) if wv else 0.0
    for w in wv:
        w["vs_avg"] = round(w["par"] - avg)
    wv.sort(key=lambda w: -w["par"])
    for i, w in enumerate(wv):
        w["rank"] = i + 1
    s["waivers"] = wv


def letter(pct):
    for cut, g in ((0.85, "A"), (0.60, "B"), (0.35, "C"), (0.15, "D")):
        if pct >= cut:
            lo, hi = cut, {"A": 1.0, "B": 0.85, "C": 0.60, "D": 0.35}[g]
            third = (hi - lo) / 3
            return g + ("+" if pct >= hi - third and g != "A" or (g == "A" and pct >= 0.95) else "-" if pct < lo + third else "")
    return "F"


def letter_grades(seasons):
    """Grade = percentile of spots gained per pick among every team-draft on record."""
    allv = sorted(g["spots_per_pick"] for s in seasons for g in s["grades"])
    wallv = sorted(w["par_per_week"] for s in seasons for w in s["waivers"])
    n = len(allv)
    for s in seasons:
        for g in s["grades"]:
            below = sum(v < g["spots_per_pick"] for v in allv)
            same = sum(v == g["spots_per_pick"] for v in allv)
            pct = (below + 0.5 * same) / n
            g["pct"] = round(pct, 3)
            g["grade"] = letter(pct)
        for w in s["waivers"]:
            below = sum(v < w["par_per_week"] for v in wallv)
            same = sum(v == w["par_per_week"] for v in wallv)
            w["pct"] = round((below + 0.5 * same) / len(wallv), 3)
            w["grade"] = letter(w["pct"])
        tw = s["waivers"][0]
        bw = next((a for a in s["adds"] if a["player_id"] == tw["best"] and a["team_id"] == tw["team_id"]), None)
        s["waiver_headline"] = {"team_id": tw["team_id"], "grade": tw["grade"], "par": tw["par"], "counted_fp": tw["counted_fp"],
                                "hits": tw["hits"], "adds": tw["adds"], "best": bw["name"] if bw else None,
                                "best_par": bw["par"] if bw else None, "best_rank": bw["pg_rank"] if bw else None,
                                "best_gp": bw["counted_gp"] if bw else None}
        top = s["grades"][0]
        best = next(p for p in s["picks"] if p["player_id"] == top["best_spots"] and p["team_id"] == top["team_id"])
        s["headline"] = {"team_id": top["team_id"], "grade": top["grade"], "hits": top["hits"], "picks": top["picks"],
                         "spots": top["spots"], "vs_avg": top["vs_avg"], "best": best["name"], "best_pick": best["overall"], "best_rank": best["rank"]}


def build():
    seasons = []
    for season in cached_seasons():
        try:
            s = analyze_season(season)
        except Exception as e:  # one bad season shouldn't take the page down
            print(f"draft {season}: skipped ({e})")
            s = None
        if s:
            seasons.append(s)
    fit = fit_slot(seasons)
    for s in seasons:
        team_tables(s, fit)
    letter_grades(seasons)
    OUT.write_text(json.dumps({"fit": fit, "seasons": {str(s["season"]): s for s in seasons}}, default=float,
                              separators=(",", ":")), encoding="utf-8")
    for s in seasons:
        top = s["grades"][0]
        print(f"draft {s['season']}: replacement {s['replacement']:.1f} FP/g, {s['points_per_win']:.0f} pts/win, "
              f"best draft {s['team_names'][str(top['team_id'])]} {top['war']:+.1f} WAR"
              + (" (reversal fixed)" if s["reversal_fixed"] else ""))
    return seasons


if __name__ == "__main__":
    build()
