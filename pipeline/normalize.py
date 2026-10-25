"""Raw ESPN JSON -> tidy pandas tables, plus validation against ESPN's own totals.

Usage:
  python pipeline/normalize.py           # build + validate every cached season
  python pipeline/normalize.py 2026      # one season

Other modules call load_season(season) -> dict of DataFrames:
  teams     season, team_id, name, abbrev, owner_id, owner
  calendar  season, sp, date, mp, game_day
  caps      season, mp, sp_start, sp_end, days, game_days, cap
  matchups  one row per team per matchup: season, mp, matchup_id, playoff, team_id, opp_id,
            pts, opp_pts, result (W/L/T/None), gp, opp_gp, cap, cap_hit_sp
  daily     one row per rostered player per day: season, sp, mp, team_id, player_id, name, pro,
            elig, slot, inj, pts, gp, started, counted
"""
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from espn_client import ROOT  # noqa: E402

RAW = ROOT / "data" / "raw"
TIDY = ROOT / "data" / "tidy"

TIDY_VERSION = 4 # bump when table shapes change so cached pickles rebuild
BENCH, IR = 12, 13
GP_LIMIT_SLOT = "15"  # lineupSlotStatLimits key holding the games-played (stat 42) rate
ET = timezone(timedelta(hours=-5))  # NBA tip-offs are never near midnight, so a fixed offset is safe


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def cap_rate(settings):
    lim = (settings.get("rosterSettings", {}).get("lineupSlotStatLimits") or {}).get(GP_LIMIT_SLOT)
    return lim["limitValue"] if lim and lim.get("statId") == 42 else None


def cap_for(rate, game_days):
    return int(math.floor(rate * game_days + 0.5)) if rate else None


# ---------------------------------------------------------------- calendar

def pro_games(proschedule):
    """Scoring period -> set of NBA team ids playing that day."""
    out = {}
    for t in proschedule["settings"]["proTeams"]:
        for sp, games in (t.get("proGamesByScoringPeriod") or {}).items():
            if games:
                out.setdefault(int(sp), set()).add(t["id"])
    return out


def sp_dates(proschedule):
    """Scoring period -> calendar date (ET) and the set of SPs with NBA games."""
    first = {}
    for t in proschedule["settings"]["proTeams"]:
        for sp, games in (t.get("proGamesByScoringPeriod") or {}).items():
            for g in games:
                d = datetime.fromtimestamp(g["date"] / 1000, ET).date()
                first[int(sp)] = min(first.get(int(sp), d), d)
    game_days = set(first)
    anchor_sp = min(first)
    anchor = first[anchor_sp]
    return (lambda sp: anchor + timedelta(days=sp - anchor_sp)), game_days


def derived_weeks(sp_first, sp_last, date_of, game_days):
    """ESPN weeks: SP1 to the first Sunday, then Mon-Sun; the All-Star-break week merges with the next."""
    weeks, cur = [], []
    for sp in range(sp_first, sp_last + 1):
        cur.append(sp)
        if date_of(sp).weekday() == 6:  # Sunday closes a week
            weeks.append(cur)
            cur = []
    if cur:
        weeks.append(cur)

    # All-Star break = longest run (>=3) of no-game days in mid-season
    best, run = (0, None), []
    for sp in range(sp_first + 60, sp_last - 20):
        if sp in game_days:
            run = []
            continue
        run.append(sp)
        if len(run) > best[0]:
            best = (len(run), run[0])
    if best[0] >= 3:
        i = next(i for i, w in enumerate(weeks) if best[1] in w)
        if i + 1 < len(weeks):
            weeks[i:i + 2] = [weeks[i] + weeks[i + 1]]
    return weeks


def build_calendar(season, league, proschedule):
    st = league["settings"]["scheduleSettings"]
    date_of, game_days = sp_dates(proschedule)

    # Observed day ranges per matchup period (authoritative for played weeks)
    observed = {}
    for m in league.get("schedule", []):
        for side in ("home", "away"):
            for k in ((m.get(side) or {}).get("pointsByScoringPeriod") or {}):
                observed.setdefault(m["matchupPeriodId"], set()).add(int(k))

    status = league.get("status") or {}
    sp_last = status.get("finalScoringPeriod") or max(max(v) for v in observed.values())
    weeks = derived_weeks(1, sp_last, date_of, game_days)
    derived = {}
    for mp, wk_ids in st["matchupPeriods"].items():
        sps = [sp for w in wk_ids if w - 1 < len(weeks) for sp in weeks[w - 1]]
        if sps:
            derived[int(mp)] = sps

    # Derived weeks are authoritative: ESPN's per-day scores omit days nobody scored, so observed
    # ranges can stop short. Observed days must still fall inside the derived week.
    ranges, mismatches = dict(derived), []
    for mp, o in observed.items():
        d = derived.get(mp)
        if not d or min(o) < d[0] or max(o) > d[-1]:
            mismatches.append((mp, (d[0], d[-1]) if d else None, (min(o), max(o))))
            ranges[mp] = list(range(min(o), max(o) + 1))

    rate = cap_rate(league["settings"])
    cal_rows, cap_rows = [], []
    for mp, sps in sorted(ranges.items()):
        gd = sum(1 for sp in sps if sp in game_days)
        cap_rows.append({"season": season, "mp": mp, "sp_start": sps[0], "sp_end": sps[-1],
                         "days": len(sps), "game_days": gd, "cap": cap_for(rate, gd)})
        for sp in sps:
            cal_rows.append({"season": season, "sp": sp, "date": date_of(sp), "mp": mp,
                             "game_day": sp in game_days})
    return pd.DataFrame(cal_rows), pd.DataFrame(cap_rows), mismatches


def season_complete(league):
    # ESPN leaves isActive true on old seasons; the final day having passed is the reliable signal
    st = league.get("status") or {}
    return st.get("latestScoringPeriod", 0) >= st.get("finalScoringPeriod", 10 ** 6)


# ---------------------------------------------------------------- tables

def build_teams(season, league):
    members = {m["id"]: m for m in league.get("members", [])}
    rows = []
    for t in league.get("teams", []):
        owner_id = (t.get("primaryOwner") or (t.get("owners") or [None])[0])
        m = members.get(owner_id, {})
        name = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()
        rows.append({"season": season, "team_id": t["id"], "name": name.strip(),
                     "abbrev": t.get("abbrev"), "owner_id": owner_id,
                     "owner": m.get("displayName") or "Unknown",
                     "rank_final": t.get("rankCalculatedFinal") or None,
                     "seed": t.get("playoffSeed"), "logo": t.get("logo")})
    return pd.DataFrame(rows)


def build_matchups(season, league, caps):
    cap_by_mp = dict(zip(caps.mp, caps.cap))
    rows = []
    for m in league.get("schedule", []):
        home, away = m.get("home"), m.get("away")
        playoff = (m.get("playoffTierType") or "NONE") != "NONE"
        for me, opp, tag in ((home, away, "HOME"), (away, home, "AWAY")):
            if not me:
                continue
            slot = ((me.get("cumulativeScore") or {}).get("statBySlot") or {}).get(GP_LIMIT_SLOT) or {}
            oslot = (((opp or {}).get("cumulativeScore") or {}).get("statBySlot") or {}).get(GP_LIMIT_SLOT) or {}
            w = m.get("winner")
            result = None
            if opp and w in ("HOME", "AWAY"):
                result = "W" if w == tag else "L"
            elif opp and w == "TIE":
                result = "T"
            rows.append({
                "season": season, "mp": m["matchupPeriodId"], "matchup_id": m["id"],
                "playoff": playoff, "tier": m.get("playoffTierType"),
                "team_id": me["teamId"], "opp_id": opp["teamId"] if opp else None,
                "pts": me.get("totalPoints"), "opp_pts": opp.get("totalPoints") if opp else None,
                "result": result,
                "gp": slot.get("value"), "opp_gp": oslot.get("value"),
                "cap": cap_by_mp.get(m["matchupPeriodId"]),
                "cap_hit_sp": slot.get("exceededOnScoringPeriod") if slot.get("limitExceeded") else None,
                "espn_by_sp": me.get("pointsByScoringPeriod") or {},
            })
    return pd.DataFrame(rows)


def build_daily(season, calendar, matchups):
    mp_of = dict(zip(calendar.sp, calendar.mp))
    hit = {(r.mp, r.team_id): r.cap_hit_sp for r in matchups.itertuples()}
    rows = []
    for f in sorted((RAW / str(season) / "days").glob("*.json"), key=lambda p: int(p.stem)):
        day = _read(f)
        sp = day["sp"]
        mp = mp_of.get(sp)
        for tid, players in day["teams"].items():
            tid = int(tid)
            cap_hit = hit.get((mp, tid))
            for p in players:
                gp = p["gp"] or 0
                started = p["slot"] not in (BENCH, IR)
                rows.append({
                    "season": season, "sp": sp, "mp": mp, "team_id": tid, "player_id": p["id"],
                    "name": p["name"], "pro": p["pro"], "elig": tuple(p["elig"]), "slot": p["slot"],
                    "inj": p["inj"], "pts": p["pts"] or 0.0, "gp": gp, "proj": p["proj"],
                    "started": started,
                    # ESPN counts every game on the day the cap is crossed, nothing after
                    "counted": started and gp > 0 and (cap_hit is None or pd.isna(cap_hit) or sp <= cap_hit),
                })
    return pd.DataFrame(rows)


def build_season(season):
    league = _read(RAW / str(season) / "league.json")
    pro = _read(RAW / str(season) / "proschedule.json")
    if not league or not pro:
        raise FileNotFoundError(f"No raw data for {season}; run pipeline/fetch.py first")
    calendar, caps, mismatches = build_calendar(season, league, pro)
    teams = build_teams(season, league)
    matchups = build_matchups(season, league, caps)
    daily = build_daily(season, calendar, matchups)
    slots = {int(k): v for k, v in league["settings"]["rosterSettings"]["lineupSlotCounts"].items()
             if v and int(k) not in (BENCH, IR)}
    tables = {"version": TIDY_VERSION, "teams": teams, "calendar": calendar, "caps": caps,
              "matchups": matchups, "daily": daily, "week_mismatches": mismatches,
              "slots": slots, "pro_games": pro_games(pro),
              "league_name": league["settings"].get("name"),
              "playoff_teams": league["settings"]["scheduleSettings"].get("playoffTeamCount"),
              "complete": season_complete(league),
              "espn_records": {t["id"]: t.get("record", {}).get("overall", {}) for t in league["teams"]}}
    TIDY.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(tables, TIDY / f"{season}.pkl")
    return tables


def load_season(season, rebuild=False):
    path = TIDY / f"{season}.pkl"
    if not rebuild and path.exists():
        tables = pd.read_pickle(path)
        if tables.get("version") == TIDY_VERSION:
            return tables
    return build_season(season)


def cached_seasons():
    return sorted(int(p.name) for p in RAW.iterdir() if p.is_dir() and p.name.isdigit())


# ---------------------------------------------------------------- validation

def validate(season, t):
    m, d = t["matchups"], t["daily"]
    print(f"\n=== {season} ===")
    print(f"teams {len(t['teams'])} | matchup periods {t['caps'].mp.nunique()} | "
          f"days {d.sp.nunique()} | player-day rows {len(d)}")

    # 1. Our counted daily points vs ESPN's per-day team scores
    ours = d[d.counted].groupby(["team_id", "sp"]).pts.sum()
    total = exact = 0
    worst = []
    for r in m.itertuples():
        for sp, espn_pts in r.espn_by_sp.items():
            mine = ours.get((r.team_id, int(sp)), 0.0)
            total += 1
            if abs(mine - espn_pts) < 0.51:
                exact += 1
            else:
                worst.append((abs(mine - espn_pts), r.team_id, int(sp), mine, espn_pts))
    pct = 100 * exact / total if total else 0
    print(f"daily team points match ESPN: {exact}/{total} ({pct:.1f}%)")
    for diff, tid, sp, mine, espn in sorted(worst, reverse=True)[:3]:
        print(f"   off by {diff:.0f}: team {tid} sp {sp} ours={mine:.0f} espn={espn:.0f}")

    # 2. W-L vs ESPN standings (regular season)
    reg = m[~m.playoff]
    rec = reg.groupby("team_id").result.value_counts().unstack(fill_value=0)
    bad = []
    for tid, r in t["espn_records"].items():
        w = int(rec.loc[tid].get("W", 0)) if tid in rec.index else 0
        l = int(rec.loc[tid].get("L", 0)) if tid in rec.index else 0
        if (w, l) != (r.get("wins", 0), r.get("losses", 0)):
            bad.append((tid, (w, l), (r.get("wins"), r.get("losses"))))
    print(f"W-L matches ESPN standings: {len(t['espn_records']) - len(bad)}/{len(t['espn_records'])}"
          + (f"  mismatches {bad}" if bad else ""))

    # 3. Derived week boundaries vs observed (matters only for future weeks of the current season)
    mm = t["week_mismatches"]
    print("calendar rule matches ESPN weeks" if not mm else f"calendar rule mismatches (mp, derived, actual): {mm}")

    # 4. Cap compliance
    over = m[(m.gp.notna()) & (m.cap.notna()) & (m.gp > m.cap)]
    names = dict(zip(t["teams"].team_id, t["teams"].name))
    if over.empty:
        print("cap: no team exceeded the cap")
    for r in over.itertuples():
        print(f"   OVER CAP: wk {r.mp} {names.get(r.team_id)} used {r.gp:.0f} / cap {r.cap}")


def main():
    seasons = [int(a) for a in sys.argv[1:]] or cached_seasons()
    for s in seasons:
        validate(s, build_season(s))


if __name__ == "__main__":
    main()
