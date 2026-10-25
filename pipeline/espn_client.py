"""Minimal client for ESPN's fantasy basketball v3 API (private leagues via cookies)."""
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba"


class ESPNClient:
    def __init__(self, league_id=None, espn_s2=None, swid=None):
        self.league_id = league_id or os.environ["LEAGUE_ID"]
        self.session = requests.Session()
        self.session.cookies.set("espn_s2", espn_s2 or os.environ.get("ESPN_S2", ""))
        self.session.cookies.set("SWID", swid or os.environ.get("SWID", ""))
        self.session.headers["User-Agent"] = "league-analytics/1.0"

    def _get(self, url, params=None, headers=None, retries=4):
        for attempt in range(retries):
            r = self.session.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403):
                raise PermissionError(
                    f"ESPN returned {r.status_code}: check ESPN_S2/SWID cookies in .env"
                )
            if r.status_code == 404:
                return None
            time.sleep(2 ** attempt)
        r.raise_for_status()

    def league(self, season, views, scoring_period=None, filter_header=None):
        """Fetch league data for a season. Falls back to leagueHistory for old seasons."""
        params = [("view", v) for v in views]
        if scoring_period is not None:
            params.append(("scoringPeriodId", scoring_period))
        headers = {"x-fantasy-filter": json.dumps(filter_header)} if filter_header else None
        url = f"{BASE}/seasons/{season}/segments/0/leagues/{self.league_id}"
        data = self._get(url, params, headers)
        if data is None:
            hist = self._get(
                f"{BASE}/leagueHistory/{self.league_id}",
                params + [("seasonId", season)],
                headers,
            )
            data = hist[0] if hist else None
        return data

    def pro_schedule(self, season):
        """NBA team schedules (games per scoring period)."""
        return self._get(f"{BASE}/seasons/{season}", [("view", "proTeamSchedules_wl")])

    def players(self, season, scoring_period=None, limit=600):
        """Player pool with projections/stats (kona_player_info)."""
        flt = {
            "players": {
                "limit": limit,
                "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            }
        }
        return self.league(season, ["kona_player_info"], scoring_period, flt)
