"""Build every analytics table and write the site's JSON.

Usage:
  python pipeline/build.py           # normalize + analytics -> site/data/*.json (no ESPN calls)
  python pipeline/build.py --fetch   # pull fresh ESPN data first
"""
import json
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coaching  # noqa: E402
import expected  # noqa: E402
import franchises  # noqa: E402
import logos  # noqa: E402
import luck  # noqa: E402
from espn_client import ROOT  # noqa: E402
from normalize import TIDY, cached_seasons, load_season  # noqa: E402
from projections import HistoryModel  # noqa: E402

SITE_DATA = ROOT / "site" / "data"
CURRENT = int(os.environ.get("SEASON", 2027))


def clean(o):
    """Make pandas/numpy output JSON-safe (NaN -> null)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if math.isnan(o) else round(float(o), 4)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if o is pd.NA or o is pd.NaT:
        return None
    return o


def write_odds_history():
    """Playoff/bye/title odds week by week: past seasons from the odds backtest, the current
    season from the live snapshots (data/history/odds_{season}.json, one point per week)."""
    out = {}
    bt = SITE_DATA / "odds_backtest.json"
    if bt.exists():
        out.update(json.loads(bt.read_text(encoding="utf-8")).get("history", {}))
    live = ROOT / "data" / "history" / f"odds_{CURRENT}.json"
    if live.exists():
        cur = {}
        for mp, teams in sorted(json.loads(live.read_text(encoding="utf-8")).items(), key=lambda x: int(x[0])):
            for tid, o in teams.items():
                h = cur.setdefault(tid, {"after": [], "playoff": [], "bye": [], "title": []})
                h["after"].append(int(mp))
                for k in ("playoff", "bye", "title"):
                    h[k].append(o[k])
        out[str(CURRENT)] = cur
    write("odds_history.json", out)


def stamp_assets():
    """Add ?v=<content hash> to the page's local script tags so browsers never pair a new page
    with stale cached scripts (GitHub Pages lets browsers cache files for ~10 minutes)."""
    import hashlib
    import re
    page = ROOT / "site" / "index.html"
    html = page.read_text(encoding="utf-8")

    def tag(m):
        f = ROOT / "site" / m.group(1)
        # normalize line endings so Windows and CI checkouts produce the same stamp
        v = hashlib.sha1(f.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:10] if f.exists() else "0"
        return f'src="{m.group(1)}?v={v}"'

    out = re.sub(r'src="(js/[\w.-]+\.js)(?:\?v=[\w]+)?"', tag, html)
    if out != html:
        page.write_text(out, encoding="utf-8")


def write(name, data):
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / name).write_text(json.dumps(clean(data), separators=(",", ":")), encoding="utf-8")


def coach_cached(season, t):
    path = TIDY / f"coach_{season}.pkl"
    if season != CURRENT and path.exists():
        return pd.read_pickle(path)
    c = coaching.coach_season(t)
    pd.to_pickle(c, path)
    return c


def expected_cached(season, t, model):
    path = TIDY / f"expected_v3_{season}.pkl"  # v2: per-step detail lists, betting-line projections where logged
    if season != CURRENT and path.exists():
        return pd.read_pickle(path)
    dates = dict(zip(t["calendar"].sp, t["calendar"].date))
    gm = expected.logged_game_mu(dates) if season == CURRENT else {}
    e = expected.expected_season(season, t, model, game_mu=gm, cv=model.cv)
    pd.to_pickle(e, path)
    return e


EXP_COLS = ["e0", "availability", "moves", "lineup", "breakouts", "hot_cold", "actual"]


def build_season(season, t, model):
    coach = coach_cached(season, t)
    exp_w, exp_players = expected_cached(season, t, model)
    caps = t["caps"].copy()
    dates = dict(zip(t["calendar"].sp, t["calendar"].date))
    caps["start"] = caps.sp_start.map(dates)
    caps["end"] = caps.sp_end.map(dates)
    playoff_mps = set(t["matchups"][t["matchups"].playoff].mp)
    caps["playoff"] = caps.mp.isin(playoff_mps)

    owner = dict(zip(t["teams"].team_id, t["teams"].owner_id))
    w = luck.weekly(t)
    w["owner_id"] = w.team_id.map(owner)
    w["opp_owner_id"] = w.opp_id.map(owner)
    w = w.merge(caps[["mp", "days"]], on="mp", how="left")
    w = w.merge(coach.drop(columns=["season", "playoff", "cap"]), on=["mp", "team_id"], how="left")
    w = w.merge(luck.absences(t), on=["mp", "team_id"], how="left")
    w["cap_deficit"] = (w.cap - w.max_games).clip(lower=0)
    if not exp_w.empty:
        w = w.merge(exp_w.drop(columns=["result", "days_done"]), on=["mp", "team_id"], how="left")

    sl = luck.season_luck(w)
    rows = franchises.season_rows(season, t, sl, coach)
    reg = w[~w.playoff & w.result.notna()]
    extra = reg.groupby("team_id").agg(cap_deficit=("cap_deficit", "sum"),
                                       missed_pts=("missed_pts", "sum"),
                                       gp_used=("gp", "sum"), cap_total=("cap", "sum")).reset_index()
    opp_def = reg.assign(team_id=reg.opp_id).groupby("team_id").cap_deficit.sum().rename("opp_cap_deficit")
    rows = rows.merge(extra, on="team_id", how="left").merge(opp_def, on="team_id", how="left")
    if "e0" in reg:
        x = reg.assign(x_w=reg.x_result == "W", x_l=reg.x_result == "L",
                       flips_won=reg.flipped & (reg.result == "W"), flips_lost=reg.flipped & (reg.result == "L"))
        tot = x.groupby("team_id").agg(**{c: (c, "sum") for c in EXP_COLS + ["e3", "e4", "x_w", "x_l", "flips_won", "flips_lost"]})
        # coaching already has a season "actual" column; keep the breakdown's separate
        rows = rows.merge(tot.rename(columns={"actual": "x_actual"}).reset_index(), on="team_id", how="left")
        b = reg[EXP_COLS].mean()
        print(f"  {season} expected pts, league avg per team-week: " + ", ".join(f"{c} {b[c]:+.1f}" for c in EXP_COLS[1:6]))
    return t, w, rows, coach, caps, luck.schedule_swap(w), exp_players


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def add_breakdown_percentiles(built):
    """Percentile of every step for every finished team-week (per counted game, vs every team-week on
    record) and a season roll-up per team (vs every team-season), the same scale the live card uses."""
    weeks = []
    for s, b in built.items():
        w = b[1]
        if "availability" not in w:
            continue
        reg = w[~w.playoff & w.result.notna() & w.availability.notna()]
        weeks += reg[["availability", "moves", "lineup", "breakouts", "hot_cold", "gp"]].to_dict("records")
    H = expected.step_history(weeks)

    def pcts(r, hist, gp):
        gp = max(gp or 0, 1)
        return {"availability": expected.percentile(hist["availability"], r["availability"] / gp),
                "moves": expected.percentile(hist["moves"], r["moves"] / gp),
                "lineup": expected.percentile(hist["lineup"], r["lineup"] / gp),
                "luck": expected.percentile(hist["luck"], (r["availability"] + r["breakouts"] + r["hot_cold"]) / gp)}

    seasons_rows = []
    for s, b in built.items():
        w = b[1]
        if "availability" not in w:
            continue
        w["pct"] = [({**pcts(r, H, r["gp"]), "perf": _num(r.get("pct_perf"))} if _num(r.get("availability")) is not None else None)
                    for r in w.to_dict("records")]
        reg = w[~w.playoff & w.result.notna() & w.availability.notna()]
        for tid, g in reg.groupby("team_id"):
            seasons_rows.append((s, tid, g))
    # season roll-up and its percentile vs every team-season
    tot = [{k: float(g[k].sum()) for k in ("availability", "moves", "lineup", "breakouts", "hot_cold", "gp")} for _, _, g in seasons_rows]
    HS = expected.step_history(tot)
    bd = {}
    for (s, tid, g), t in zip(seasons_rows, tot):
        def agg(col, key, val, extra=()):
            out = {}
            for lst in g[col].dropna():
                for x in (lst if isinstance(lst, list) else []):
                    o = out.setdefault(x["id"], {"id": x["id"], "name": x["name"], "games": 0, key: 0.0})
                    o["games"] += x.get("games", 0)
                    o[key] += x.get(val, 0.0)
            return sorted(out.values(), key=lambda o: -abs(o[key]))[:4]
        perf = {}
        for lst in g["players"].dropna() if "players" in g else []:
            for x in (lst if isinstance(lst, list) else []):
                o = perf.setdefault(x["id"], {"id": x["id"], "name": x["name"], "games": 0, "pts": 0.0, "proj": 0.0, "bench_games": 0, "bench_pts": 0.0})
                for k in ("games", "pts", "proj", "bench_games", "bench_pts"):
                    o[k] += x.get(k, 0)
        bd[(s, tid)] = {
            "e0": float(g.e0.sum()), "availability": t["availability"], "moves": t["moves"], "lineup": t["lineup"],
            "breakouts": t["breakouts"], "hot_cold": t["hot_cold"], "actual": float(g.actual.sum()),
            "games_used": int(t["gp"]), "weeks": int(len(g)),
            "lined_games": int(g["lined_games"].fillna(0).sum()) if "lined_games" in g else 0,
            "missed": [{**m, "lost": round(m["lost"], 1)} for m in agg("missed", "lost", "lost")],
            "added": [{**a, "pts": round(a["pts"], 1)} for a in agg("added", "pts", "pts")],
            "dropped": [], "benched": [{**x, "pts": round(x["pts"], 1)} for x in agg("benched", "pts", "pts")],
            "players": sorted([{**p, "pts": round(p["pts"], 1), "proj": round(p["proj"], 1), "bench_pts": round(p["bench_pts"], 1)}
                               for p in perf.values()], key=lambda p: -p["pts"]),
            "pct": {**pcts(t, HS, t["gp"]), "perf": None},
        }
    for s, b in built.items():
        rows = b[2]
        rows["breakdown"] = [bd.get((s, tid)) for tid in rows.team_id]


def main():
    if "--fetch" in sys.argv:
        import fetch
        fetch.main()

    seasons = cached_seasons()
    tables = {s: load_season(s, rebuild=s == CURRENT) for s in seasons}
    model = HistoryModel(tables)
    built = {s: build_season(s, tables[s], model) for s in seasons}

    all_teams = pd.concat([b[0]["teams"] for b in built.values()])
    names = franchises.owner_names(all_teams)
    profiles = franchises.owner_profiles(all_teams, CURRENT if CURRENT in built else max(built))
    for p in profiles.values():
        p["logo"] = logos.localize(p["logo"])

    week_cols = ["mp", "matchup_id", "team_id", "opp_id", "owner_id", "opp_owner_id", "pts", "opp_pts", "result",
                 "playoff", "tier", "rank", "opp_rank", "allplay_w", "beat", "n_others", "margin",
                 "week_avg", "bad_beat", "lucky_win", "gp", "opp_gp", "cap", "cap_hit_sp", "opt_pts",
                 "opt_gp", "max_games", "efficiency", "bench_lost", "coaching_loss", "over_cap",
                 "missed_starts", "missed_games", "missed_pts", "missed_top", "cap_deficit", "days",
                 "e0", "e1", "e2", "e3", "e4", "opp_e4", "availability", "moves", "lineup", "breakouts", "hot_cold", "actual",
                 "x_result", "flipped", "hot", "cold",
                 "missed", "added", "dropped", "benched", "players", "pct", "lined_games", "games_used"]
    team_cols = ["team_id", "owner_id", "name", "abbrev", "logo", "seed", "rank_final", "w", "l", "t", "pf", "pa",
                 "exp_w", "luck", "allplay_pct", "pa_vs_avg", "bad_beats", "lucky_wins", "efficiency",
                 "bench_lost", "coaching_losses", "over_cap_weeks", "cap_deficit", "opp_cap_deficit",
                 "missed_pts", "gp_used", "cap_total", "playoffs", "champion", "runner_up",
                 "e0", "e3", "e4", "availability", "moves", "lineup", "breakouts", "hot_cold", "x_actual",
                 "x_w", "x_l", "flips_won", "flips_lost", "breakdown"]

    add_breakdown_percentiles(built)

    for s, (t, w, rows, coach, caps, swap, exp_players) in built.items():
        rows = rows.assign(owner_name=rows.owner_id.map(names), logo=rows.logo.map(logos.localize))
        w = w.reindex(columns=list(dict.fromkeys(list(w.columns) + week_cols)))
        rows = rows.reindex(columns=list(dict.fromkeys(list(rows.columns) + team_cols)))
        players = exp_players[exp_players.games >= 3] if len(exp_players) else exp_players
        write(f"season_{s}.json", {
            "season": s, "complete": t["complete"], "playoff_teams": t["playoff_teams"],
            "caps": caps.drop(columns=["season"]).to_dict("records"),
            "teams": rows[team_cols + ["owner_name"]].to_dict("records"),
            "weeks": w[week_cols].to_dict("records"),
            "schedule_swap": swap,
            "players_expected": players.to_dict("records") if len(players) else [],
        })

    season_df = pd.concat([b[2] for b in built.values()])
    season_df = season_df.reindex(columns=list(dict.fromkeys(list(season_df.columns) + team_cols)))
    season_df["logo"] = season_df.logo.map(logos.localize)
    weekly_all = pd.concat([b[1].assign(season=s) for s, b in built.items()])
    coach_all = pd.concat([b[3] for b in built.values()])
    done = season_df[season_df.complete]
    write("history.json", {
        "owners": names,
        "profiles": profiles,
        "seasons": season_df[["season", "complete"] + [c for c in team_cols if c != "breakdown"]].to_dict("records"),
        "all_time": franchises.all_time(season_df).to_dict("records"),
        "all_time_complete": franchises.all_time(done).to_dict("records"),
        "h2h": franchises.head_to_head(weekly_all),
        "records": franchises.record_book(weekly_all, coach_all),
    })

    cur = built.get(CURRENT, built[max(built)])[0]
    write("meta.json", {
        "league_name": cur["league_name"], "current_season": CURRENT, "seasons": seasons,
        "latest_sp": int(cur["daily"].sp.max()) if not cur["daily"].empty else None,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    write_odds_history()
    try:  # draft & waiver value (WAR-style); never fails the refresh
        import draft
        draft.build()
    except Exception as e:
        print(f"draft step skipped: {e}")
    stamp_assets()
    print(f"wrote {len(seasons) + 2} files to {SITE_DATA}")

    if CURRENT in built and not built[CURRENT][0]["complete"]:
        import live
        live.main()
        write_odds_history()  # include the point the live run just recorded
    try:  # day-by-day win chances, swings and clutch (finished seasons cached); never fails the refresh
        import swings
        swings.build()
    except Exception as e:
        print(f"swings step skipped: {e}")


if __name__ == "__main__":
    main()
