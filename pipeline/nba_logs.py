"""NBA-wide game logs from ESPN's public API (no login): every player's minutes and fantasy points
per game, rostered in the league or not. Used for teammate usage bumps and injury return curves.

Usage: python pipeline/nba_logs.py [season ...]   (default: current + previous season)

Writes data/raw/nba/{season}/logs.json:
  {athlete_id: {"name", "pos", "games": [[event_id, date_et, team_id, minutes, fp], ...]}}
plus data/raw/nba/{season}/teams.json (team id -> event ids it played). Past seasons are fetched
once; the current season is refreshed at most once a day.
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
OUT = ROOT / "data" / "raw" / "nba"
SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
LOG = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{}/gamelog"
ET = timezone(timedelta(hours=-5))
# ESPN fantasy stat id -> box-score field
STAT_FIELD = {0: "PTS", 1: "BLK", 2: "STL", 3: "AST", 6: "REB", 11: "TO", 13: "FGM", 14: "FGA", 15: "FTM", 16: "FTA", 17: "3PM"}

session = requests.Session()
session.headers["User-Agent"] = "league-analytics/1.0"


def get(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def scoring_weights():
    league = json.loads((ROOT / "data" / "raw" / str(CURRENT) / "league.json").read_text(encoding="utf-8"))
    return {STAT_FIELD[i["statId"]]: i["points"] for i in league["settings"]["scoringSettings"]["scoringItems"] if i["statId"] in STAT_FIELD}


def box(labels, stats):
    """Game-log stat row -> box-score dict (FG '7-19' style splits into made/attempted)."""
    raw = dict(zip(labels, stats))
    out = {}
    for label, made, att in (("FG", "FGM", "FGA"), ("3PT", "3PM", "3PA"), ("FT", "FTM", "FTA")):
        m = re.match(r"(\d+)-(\d+)", str(raw.get(label, "")))
        out[made], out[att] = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    for k in ("MIN", "REB", "AST", "BLK", "STL", "TO", "PTS"):
        try:
            out[k] = float(raw.get(k) or 0)
        except ValueError:
            out[k] = 0.0
    return out


def athlete_ids(season):
    """Current NBA rosters, plus everyone the league has rostered in that season."""
    ids = {}
    teams = (get(f"{SITE}/teams") or {}).get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
    for t in teams:
        r = get(f"{SITE}/teams/{t['team']['id']}/roster") or {}
        for a in r.get("athletes", []):
            ids[int(a["id"])] = {"name": a.get("fullName"), "pos": (a.get("position") or {}).get("abbreviation")}
    days = ROOT / "data" / "raw" / str(season) / "days"
    for f in days.glob("*.json") if days.exists() else []:
        for players in json.loads(f.read_text(encoding="utf-8"))["teams"].values():
            for p in players:
                ids.setdefault(int(p["id"]), {"name": p.get("name"), "pos": None})
    return ids


def fetch_season(season, force=False):
    d = OUT / str(season)
    path, stamp = d / "logs.json", d / "fetched.txt"
    today = datetime.now(ET).date().isoformat()
    # file mtimes are useless after a git checkout (CI), so the fetch date is recorded explicitly
    if path.exists() and not force:
        fetched = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else ""
        if season != CURRENT or fetched == today:
            return json.loads(path.read_text(encoding="utf-8"))
    weights = scoring_weights()
    ids = athlete_ids(season)

    def one(aid):
        g = get(LOG.format(aid), {"season": season})
        if not g:
            return aid, None
        labels, events = g.get("labels") or [], g.get("events") or {}
        rows = []
        for st in g.get("seasonTypes", []):
            if "Regular Season" not in st.get("displayName", ""):
                continue
            for cat in st.get("categories", []):
                for e in cat.get("events", []):
                    ev = events.get(e["eventId"], {})
                    b = box(labels, e.get("stats", []))
                    fp = sum(w * b.get(k, 0) for k, w in weights.items())
                    when = datetime.fromisoformat(ev.get("gameDate", "1970-01-01T00:00:00+00:00").replace("Z", "+00:00")).astimezone(ET).date()
                    rows.append([e["eventId"], when.isoformat(), int((ev.get("team") or {}).get("id", 0)), b["MIN"], fp])
        return aid, rows

    with ThreadPoolExecutor(6) as ex:
        results = dict(ex.map(one, list(ids)))
    logs = {str(a): {**ids[a], "games": sorted(rows, key=lambda r: r[1])} for a, rows in results.items() if rows}
    teams = {}
    for a in logs.values():
        for ev, _, team, _, _ in a["games"]:
            teams.setdefault(str(team), set()).add(ev)
    d.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(logs, separators=(",", ":")), encoding="utf-8")
    (d / "teams.json").write_text(json.dumps({k: sorted(v) for k, v in teams.items()}, separators=(",", ":")), encoding="utf-8")
    stamp.write_text(today, encoding="utf-8")
    print(f"nba logs {season}: {len(logs)} players, {sum(len(a['games']) for a in logs.values())} player-games")
    return logs


def injury_feed():
    """ESPN's NBA-wide injury report: athlete id -> {"status": "Out"/"Day-To-Day"/..., "return": YYYY-MM-DD or None}."""
    out = {}
    for team in (get(f"{SITE}/injuries") or {}).get("injuries", []):
        for i in team.get("injuries", []):
            a = i.get("athlete") or {}
            m = re.search(r"/id/(\d+)", "".join(l.get("href", "") for l in a.get("links", [])))
            if m:
                ret = (i.get("details") or {}).get("returnDate")
                out[int(m.group(1))] = {"status": i.get("status"), "return": ret[:10] if ret else None}
    return out


def load(season):
    p = OUT / str(season) / "logs.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


if __name__ == "__main__":
    for s in [int(a) for a in sys.argv[1:]] or [CURRENT - 1, CURRENT]:
        fetch_season(s, force="--force" in sys.argv)
