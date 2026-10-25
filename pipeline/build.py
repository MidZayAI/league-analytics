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
    path = TIDY / f"expected_{season}.pkl"
    if season != CURRENT and path.exists():
        return pd.read_pickle(path)
    e = expected.expected_season(season, t, model)
    pd.to_pickle(e, path)
    return e


EXP_COLS = ["e0", "availability", "moves", "lineup", "hot_cold", "actual"]


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
        tot = x.groupby("team_id").agg(**{c: (c, "sum") for c in EXP_COLS + ["e3", "x_w", "x_l", "flips_won", "flips_lost"]})
        # coaching already has a season "actual" column; keep the breakdown's separate
        rows = rows.merge(tot.rename(columns={"actual": "x_actual"}).reset_index(), on="team_id", how="left")
        b = reg[EXP_COLS].mean()
        print(f"  {season} expected pts, league avg per team-week: " + ", ".join(f"{c} {b[c]:+.1f}" for c in EXP_COLS[1:5]))
    return t, w, rows, coach, caps, luck.schedule_swap(w), exp_players


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

    week_cols = ["mp", "team_id", "opp_id", "owner_id", "opp_owner_id", "pts", "opp_pts", "result",
                 "playoff", "tier", "rank", "opp_rank", "allplay_w", "beat", "n_others", "margin",
                 "week_avg", "bad_beat", "lucky_win", "gp", "opp_gp", "cap", "cap_hit_sp", "opt_pts",
                 "opt_gp", "max_games", "efficiency", "bench_lost", "coaching_loss", "over_cap",
                 "missed_starts", "missed_games", "missed_pts", "missed_top", "cap_deficit", "days",
                 "e0", "e1", "e2", "e3", "opp_e3", "availability", "moves", "lineup", "hot_cold", "actual",
                 "x_result", "flipped", "hot", "cold"]
    team_cols = ["team_id", "owner_id", "name", "abbrev", "seed", "rank_final", "w", "l", "t", "pf", "pa",
                 "exp_w", "luck", "allplay_pct", "pa_vs_avg", "bad_beats", "lucky_wins", "efficiency",
                 "bench_lost", "coaching_losses", "over_cap_weeks", "cap_deficit", "opp_cap_deficit",
                 "missed_pts", "gp_used", "cap_total", "playoffs", "champion", "runner_up",
                 "e0", "e3", "availability", "moves", "lineup", "hot_cold", "x_actual",
                 "x_w", "x_l", "flips_won", "flips_lost"]

    for s, (t, w, rows, coach, caps, swap, exp_players) in built.items():
        rows = rows.assign(owner_name=rows.owner_id.map(names))
        w = w.reindex(columns=list(dict.fromkeys(list(w.columns) + week_cols)))
        rows = rows.reindex(columns=list(dict.fromkeys(list(rows.columns) + team_cols)))
        players = exp_players[exp_players.games >= 3] if len(exp_players) else exp_players
        write(f"season_{s}.json", {
            "season": s, "complete": t["complete"], "playoff_teams": t["playoff_teams"],
            "caps": caps.drop(columns=["season"]).to_dict("records"),
            "teams": rows[team_cols + ["owner_name"]].to_dict("records"),
            "weeks": w[week_cols].to_dict("records"),
            "schedule_swap": swap,
            "players_expected": players.drop(columns=["player_id"]).to_dict("records") if len(players) else [],
        })

    season_df = pd.concat([b[2] for b in built.values()])
    season_df = season_df.reindex(columns=list(dict.fromkeys(list(season_df.columns) + team_cols)))
    weekly_all = pd.concat([b[1].assign(season=s) for s, b in built.items()])
    coach_all = pd.concat([b[3] for b in built.values()])
    done = season_df[season_df.complete]
    write("history.json", {
        "owners": names,
        "seasons": season_df[["season", "complete"] + team_cols].to_dict("records"),
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
    print(f"wrote {len(seasons) + 2} files to {SITE_DATA}")

    if CURRENT in built and not built[CURRENT][0]["complete"]:
        import live
        live.main()


if __name__ == "__main__":
    main()
