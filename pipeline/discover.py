"""Phase 0: probe the ESPN API for this league and report what data is available.

Usage: python pipeline/discover.py
Writes raw responses to data/raw/discovery/ and prints a summary.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from espn_client import ESPNClient, ROOT  # noqa: E402

OUT = ROOT / "data" / "raw" / "discovery"
CAP_HINTS = ("limit", "gamesplayed", "maxgames", "matchupperiod")


def dump(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")


def find_keys(obj, hints, path="", hits=None, max_hits=60):
    """Return paths of keys whose names contain any hint (for locating the games cap)."""
    hits = [] if hits is None else hits
    if len(hits) >= max_hits:
        return hits
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if any(h in k.lower() for h in hints):
                preview = json.dumps(v)[:160]
                hits.append(f"{p} = {preview}")
            find_keys(v, hints, p, hits, max_hits)
    elif isinstance(obj, list) and obj:
        find_keys(obj[0], hints, path + "[0]", hits, max_hits)
    return hits


def main():
    season = int(os.environ.get("SEASON", 2027))
    c = ESPNClient()

    print(f"== Season {season}: settings ==")
    s = c.league(season, ["mSettings", "mTeam", "mStatus"])
    if s is None:
        sys.exit("League not found. Check LEAGUE_ID and SEASON in .env")
    dump(f"settings_{season}", s)
    st = s.get("settings", {})
    print("League:", st.get("name"), "| teams:", len(s.get("teams", [])))
    print("Scoring type:", st.get("scoringSettings", {}).get("scoringType"))
    print("Status:", json.dumps(s.get("status", {}))[:300])
    print("Lineup slots:", st.get("rosterSettings", {}).get("lineupSlotCounts"))
    print("\nCandidate games-cap fields:")
    for h in find_keys(st, CAP_HINTS):
        print("  ", h)

    print("\n== Previous seasons available ==")
    prev = s.get("status", {}).get("previousSeasons") or []
    print(prev)
    for yr in prev[:1] + prev[-1:]:
        old = c.league(yr, ["mSettings", "mMatchupScore"])
        if old:
            dump(f"settings_{yr}", old)
            print(f"  {yr}: OK, {len(old.get('schedule', []))} matchups")
        else:
            print(f"  {yr}: NOT available")

    probe = (prev[-1] if prev else season)
    print(f"\n== Daily lineup probe ({probe}, scoring period 10) ==")
    box = c.league(probe, ["mMatchupScore", "mBoxscore", "mRoster"], scoring_period=10)
    dump(f"boxscore_{probe}_sp10", box)
    team = (box or {}).get("teams", [{}])[0]
    entries = team.get("roster", {}).get("entries", [])
    print(f"  roster entries for team {team.get('id')}: {len(entries)}")
    if entries:
        e = entries[0]
        print("  sample entry keys:", list(e.keys()))
        print("  lineupSlotId:", e.get("lineupSlotId"))
    m = next((x for x in (box or {}).get("schedule", []) if x.get("matchupPeriodId")), None)
    if m:
        print("  matchup keys:", list(m.keys()))
        print("  home keys:", list(m.get("home", {}).keys()))
        print("\n  Cap-like fields in matchup:")
        for h in find_keys(m, CAP_HINTS + ("games",)):
            print("    ", h)

    print("\n== Pro schedule ==")
    ps = c.pro_schedule(season)
    dump(f"proschedule_{season}", ps)
    teams = (ps or {}).get("settings", {}).get("proTeams", [])
    print(f"  NBA teams: {len(teams)}")

    print("\n== Player projections ==")
    pl = c.players(season, limit=50)
    dump(f"players_{season}", pl)
    print(f"  players returned: {len((pl or {}).get('players', []))}")
    print(f"\nRaw files in {OUT}")


if __name__ == "__main__":
    main()
