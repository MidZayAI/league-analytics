"""Pull and cache raw ESPN data for every season of the league.

Usage:
  python pipeline/fetch.py            # all seasons (past seasons cached once, current refreshed)
  python pipeline/fetch.py 2026       # one season
  python pipeline/fetch.py --force    # refetch everything

Layout:
  data/raw/{season}/league.json        settings, teams, members, matchups (+ games used per side)
  data/raw/{season}/proschedule.json   NBA schedule by scoring period (day)
  data/raw/{season}/days/{sp}.json     compact daily rosters: slot + points + games played per player
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from espn_client import ESPNClient, ROOT  # noqa: E402

RAW = ROOT / "data" / "raw"
CURRENT = int(os.environ.get("SEASON", 2027))
REFRESH_DAYS = 3  # re-pull the last N days of the current season (stat corrections)
WORKERS = 4


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _day_stat(stats, sp, source):
    """The single-day stat line (split 5) for scoring period sp; source 0=actual, 1=projected."""
    for s in stats or []:
        if s.get("scoringPeriodId") == sp and s.get("statSourceId") == source and s.get("statSplitTypeId") == 5:
            return s
    return None


def scrub_members(members):
    """Keep only id + ESPN handle; real names and settings never get written (the repo is public)."""
    return [{"id": m["id"], "displayName": m.get("displayName")} for m in members or []]


def compact_day(raw, sp):
    teams = {}
    for t in raw.get("teams", []):
        rows = []
        for e in t.get("roster", {}).get("entries", []):
            p = e.get("playerPoolEntry", {}).get("player", {})
            act = _day_stat(p.get("stats"), sp, 0)
            proj = _day_stat(p.get("stats"), sp, 1)
            rows.append({
                "id": e["playerId"],
                "name": p.get("fullName"),
                "pro": p.get("proTeamId"),
                "elig": p.get("eligibleSlots", []),
                "slot": e.get("lineupSlotId"),
                "inj": e.get("injuryStatus") or p.get("injuryStatus"),
                "pts": act.get("appliedTotal") if act else None,
                "gp": (act.get("stats") or {}).get("42") if act else None,
                "proj": proj.get("appliedTotal") if proj else None,
            })
        teams[str(t["id"])] = rows
    return {"sp": sp, "teams": teams}


def season_days(league, season):
    """Scoring periods to pull: every day covered by a matchup (past) or played so far (current)."""
    if season == CURRENT:
        return list(range(1, (league.get("status") or {}).get("latestScoringPeriod", 1) + 1))
    sps = set()
    for m in league.get("schedule", []):
        for side in ("home", "away"):
            sps |= {int(k) for k in ((m.get(side) or {}).get("pointsByScoringPeriod") or {})}
    return sorted(range(min(sps), max(sps) + 1)) if sps else []


def fetch_season(c, season, force=False):
    d = RAW / str(season)
    is_current = season == CURRENT

    league_path = d / "league.json"
    league = None if (force or is_current) else _read(league_path)
    if league is None:
        league = c.league(season, ["mSettings", "mTeam", "mMatchupScore", "mStatus"])
        if league is None:
            print(f"{season}: not available")
            return
        league["members"] = scrub_members(league.get("members"))
        _write(league_path, league)

    sched_path = d / "proschedule.json"
    if force or is_current or not sched_path.exists():
        _write(sched_path, c.pro_schedule(season))

    # ESPN's preseason draft ranks (set before the season): the starting estimate for rookies
    ranks_path = d / "ranks.json"
    if force or not ranks_path.exists():
        pool = (c.players(season, limit=1000) or {}).get("players", [])
        ranks = {str(p["id"]): r["rank"] for p in pool
                 if (r := (p["player"].get("draftRanksByRankType") or {}).get("STANDARD")) and r.get("rank")}
        _write(ranks_path, ranks)

    days = season_days(league, season)
    recent = set(days[-REFRESH_DAYS:]) if is_current else set()
    todo = [sp for sp in days if force or sp in recent or not (d / "days" / f"{sp}.json").exists()]

    def pull(sp):
        raw = c.league(season, ["mRoster"], scoring_period=sp)
        _write(d / "days" / f"{sp}.json", compact_day(raw, sp))

    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(pull, todo))
    print(f"{season}: {len(days)} days ({len(todo)} fetched, {len(days) - len(todo)} cached)")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    c = ESPNClient()
    if args:
        seasons = [int(a) for a in args]
    else:
        cur = c.league(CURRENT, ["mStatus"])
        seasons = (cur.get("status", {}).get("previousSeasons") or []) + [CURRENT]
    for s in seasons:
        fetch_season(c, s, force)


if __name__ == "__main__":
    main()
