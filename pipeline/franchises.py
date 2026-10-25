"""Franchise history across seasons. A franchise is an owner (ESPN member id), since team
slots have changed hands; owner display names come from config/owners.json."""
import json

import pandas as pd

from espn_client import ROOT

OWNERS_FILE = ROOT / "config" / "owners.json"


def owner_names(all_teams):
    """Load config/owners.json, adding any new owner with their latest team name as a default."""
    cfg = json.loads(OWNERS_FILE.read_text(encoding="utf-8")) if OWNERS_FILE.exists() else {}
    latest = all_teams.sort_values("season").groupby("owner_id").last()
    changed = False
    for oid, r in latest.iterrows():
        if oid not in cfg:
            cfg[oid] = {"name": r["name"], "espn_handle": r["owner"]}
            changed = True
    if changed:
        OWNERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        OWNERS_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return {k: v["name"] for k, v in cfg.items()}


def owner_profiles(all_teams, current_season):
    """owner_id -> {name, logo, color_slot}. Current-season franchises get a fixed categorical
    color slot (1-8, stored in config/owners.json so it never changes); former owners get none."""
    cfg = json.loads(OWNERS_FILE.read_text(encoding="utf-8"))
    latest = all_teams.sort_values("season").groupby("owner_id").last()
    current = all_teams[all_teams.season == current_season].sort_values("team_id").owner_id.tolist()
    used = {v.get("color_slot") for k, v in cfg.items() if k in current and v.get("color_slot")}
    free = [s for s in range(1, 9) if s not in used]
    changed = False
    for oid in current:
        if oid in cfg and not cfg[oid].get("color_slot") and free:
            cfg[oid]["color_slot"] = free.pop(0)
            changed = True
    if changed:
        OWNERS_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return {oid: {"name": v["name"], "logo": latest.logo.get(oid) if oid in latest.index else None,
                  "color_slot": v.get("color_slot") if oid in current else None}
            for oid, v in cfg.items()}


def season_rows(season, t, luck, coach):
    """One row per team for a season: record, finish, luck, coaching."""
    reg = coach[~coach.playoff & coach.final]
    c = reg.groupby("team_id").agg(actual=("actual_pts", "sum"), opt=("opt_pts", "sum"),
                                   bench_lost=("bench_lost", "sum"),
                                   coaching_losses=("coaching_loss", "sum"),
                                   over_cap_weeks=("over_cap", "sum")).reset_index()
    c["efficiency"] = c.actual / c.opt
    s = t["teams"].merge(luck, on="team_id", how="left").merge(c, on="team_id", how="left")
    s["season"] = season
    s["complete"] = t["complete"]
    s["playoffs"] = t["complete"] & (s.seed <= t["playoff_teams"])
    s["champion"] = t["complete"] & (s.rank_final == 1)
    s["runner_up"] = t["complete"] & (s.rank_final == 2)
    return s


def all_time(seasons_df):
    g = seasons_df.groupby("owner_id")
    a = pd.DataFrame({
        "seasons": g.season.nunique(),
        "w": g.w.sum(), "l": g.l.sum(), "t": g.t.sum(),
        "pf": g.pf.sum(), "pa": g.pa.sum(),
        "titles": g.champion.sum(), "runner_ups": g.runner_up.sum(),
        "playoff_apps": g.playoffs.sum(),
        "avg_finish": g.rank_final.mean(),
        "luck": g.luck.sum(), "exp_w": g.exp_w.sum(),
        "actual": g.actual.sum(), "opt": g.opt.sum(),
        "bench_lost": g.bench_lost.sum(),
        "allplay_w": g.allplay_w.sum(), "allplay_g": g.allplay_g.sum(),
    })
    a["win_pct"] = (a.w + 0.5 * a.t) / (a.w + a.l + a.t)
    a["efficiency"] = a.actual / a.opt
    a["allplay_pct"] = a.allplay_w / a.allplay_g
    return a.reset_index()


def head_to_head(weekly_all):
    """owner -> opp owner -> {w, l, t, pf, pa, playoff_w, playoff_l}, all seasons."""
    out = {}
    for r in weekly_all[weekly_all.result.notna()].itertuples():
        d = out.setdefault(r.owner_id, {}).setdefault(
            r.opp_owner_id, {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0, "playoff_w": 0, "playoff_l": 0})
        key = {"W": "w", "L": "l", "T": "t"}[r.result]
        d[key] += 1
        d["pf"] += r.pts
        d["pa"] += r.opp_pts
        if r.playoff and r.result in ("W", "L"):
            d["playoff_" + key] += 1
    return out


def record_book(weekly_all, coach_all, n=5):
    """Single-week records (7-day-or-shorter matchups so long weeks don't dominate).
    Weeks where either side scored 0 (an abandoned roster) are excluded."""
    w = weekly_all[weekly_all.result.notna() & (weekly_all.days <= 7)
                   & (weekly_all.pts > 0) & (weekly_all.opp_pts > 0)]
    cols = ["season", "mp", "owner_id", "opp_owner_id", "pts", "opp_pts", "margin", "playoff"]
    c = coach_all[coach_all.final & (coach_all.actual_pts > 0)].merge(
        weekly_all[["season", "mp", "team_id", "owner_id", "days"]], on=["season", "mp", "team_id"])
    c = c[c.days <= 7]
    return {
        "highest_score": w.nlargest(n, "pts")[cols].to_dict("records"),
        "lowest_score": w.nsmallest(n, "pts")[cols].to_dict("records"),
        "biggest_blowout": w[w.margin > 0].nlargest(n, "margin")[cols].to_dict("records"),
        "closest_game": w[w.margin > 0].nsmallest(n, "margin")[cols].to_dict("records"),
        "highest_losing_score": w[w.result == "L"].nlargest(n, "pts")[cols].to_dict("records"),
        "most_bench_points": c.nlargest(n, "bench_lost")[
            ["season", "mp", "owner_id", "actual_pts", "opt_pts", "bench_lost"]].to_dict("records"),
    }
