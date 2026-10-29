"""Sportsbook player props -> expected fantasy points, in the league's scoring.

Lines: DraftKings player props as published on ESPN's public odds API (no login), keyed by ESPN
athlete id. Single-stat markets used: points, rebounds, assists, steals, blocks, 3-pointers.
Each market comes as two entries, over first then under; removing the bookmaker's margin gives
P(over the line), and from that the expected value:
  points                       normal around the line (sd grows with the line)
  rebounds, assists, steals,   Poisson: the rate whose P(X > line) matches the market
  blocks, 3-pointers
Turnovers and shooting (FGM/FGA/FTM/FTA) aren't priced: they come from Sleeper's box for the same
game, scaled by market points / Sleeper points (shooting volume follows scoring), or from the
league-wide ratio of those terms to points when Sleeper has no line.

Usage: python pipeline/market.py   (coverage + a hand-checkable example)
"""
import json
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nba_logs  # noqa: E402
from espn_client import ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
CACHE = ROOT / "data" / "tidy" / f"props_{CURRENT}.json"
TTL = 3600
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
PROPS = ("https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{e}/competitions/{e}"
         "/odds/100/propBets")
MARKET = {"Total Points": "PTS", "Total Rebounds": "REB", "Total Assists": "AST", "Total Steals": "STL",
          "Total Blocks": "BLK", "Total 3-Point Field Goals": "3PM"}
UNPRICED = ("TO", "FGM", "FGA", "FTM", "FTA")
# league-wide (2*FGM - FGA + FTM - FTA - 2*TO) / PTS in this scoring, used only without a Sleeper box
SHOOT_PER_PT = 0.0

session = requests.Session()
session.headers["User-Agent"] = "league-analytics/1.0"


def _get(url, params=None):
    for i in range(3):
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


def _prob(american):
    a = float(american)
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def _poisson_sf(lam, k):
    """P(X > k) for integer k."""
    term = cdf = math.exp(-lam)
    for i in range(1, k + 1):
        term *= lam / i
        cdf += term
    return 1 - cdf


def expected(stat, line, p_over):
    """Market-implied expected value of a stat from its line and the no-margin P(over)."""
    p = min(max(p_over, 0.02), 0.98)
    if stat == "PTS":
        sd = max(3.0, 0.3 * line + 2.0)
        z = math.sqrt(2) * _erfinv(2 * p - 1)
        return max(0.0, line + sd * z)
    k = int(math.floor(line))  # over a .5 line = at least k + 1
    lo, hi = 1e-4, max(2 * line + 10, 5.0)
    for _ in range(60):  # bisection: P(X > k | lam) increases with lam
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _poisson_sf(mid, k) < p else (lo, mid)
    return (lo + hi) / 2


def _erfinv(y):
    # Winitzki's approximation, refined with two Newton steps (plenty for odds-derived inputs)
    a = 0.147
    ln = math.log(1 - y * y)
    x = math.copysign(math.sqrt(math.sqrt((2 / (math.pi * a) + ln / 2) ** 2 - ln / a) - (2 / (math.pi * a) + ln / 2)), y)
    for _ in range(2):
        x -= (math.erf(x) - y) / (2 / math.sqrt(math.pi) * math.exp(-x * x))
    return x


def fetch_props(dates, force=False):
    """ESPN athlete id -> {date: {stat: expected value}} for games on `dates` (ISO strings, ET)."""
    if not force and CACHE.exists():
        d = json.loads(CACHE.read_text(encoding="utf-8"))
        if time.time() - d.get("_fetched", 0) < TTL and set(dates) <= set(d.get("dates", [])):
            return {int(a): v for a, v in d["props"].items()}
    out = {}
    for day in dates:
        board = _get(SCOREBOARD, {"dates": day.replace("-", "")}) or {}
        for ev in board.get("events", []):
            if ev.get("status", {}).get("type", {}).get("state") != "pre":
                continue  # in progress / final: lines are gone or meaningless
            items, page = [], 1
            while True:
                r = _get(PROPS.format(e=ev["id"]), {"lang": "en", "region": "us", "limit": 1000, "page": page})
                if not r:
                    break
                items += r.get("items", [])
                if page >= r.get("pageCount", 1):
                    break
                page += 1
            sides = {}
            for i in items:
                stat = MARKET.get((i.get("type") or {}).get("name"))
                ref = (i.get("athlete") or {}).get("$ref", "")
                if not stat or "/athletes/" not in ref:
                    continue
                aid = int(ref.split("/athletes/")[1].split("?")[0])
                sides.setdefault((aid, stat), []).append(i)
            for (aid, stat), pair in sides.items():
                if len(pair) < 2:
                    continue
                line = pair[0]["current"]["target"]["value"]
                if pair[1]["current"]["target"]["value"] != line:
                    continue
                po, pu = _prob(pair[0]["odds"]["american"]["value"]), _prob(pair[1]["odds"]["american"]["value"])
                out.setdefault(aid, {}).setdefault(day, {})[stat] = round(expected(stat, float(line), po / (po + pu)), 2)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"_fetched": time.time(), "dates": list(dates), "props": out}, separators=(",", ":")),
                     encoding="utf-8")
    return out


def market_fp(stats, sleeper_box=None, weights=None):
    """Expected fantasy points for one game from market stat means (+ Sleeper for unpriced terms).
    Needs at least the points line; returns None otherwise."""
    if not stats or "PTS" not in stats:
        return None
    w = weights or nba_logs.scoring_weights()
    box = dict(sleeper_box or {})
    priced = {k: v for k, v in stats.items()}
    fp = sum(w.get(k, 0) * v for k, v in priced.items())
    sl_pts = box.get("PTS") or 0
    if sl_pts > 0:
        scale = priced["PTS"] / sl_pts
        fp += sum(w.get(k, 0) * box.get(k, 0) * scale for k in UNPRICED)
        # categories the book didn't price for this player: Sleeper's value as is
        fp += sum(w.get(k, 0) * box.get(k, 0) for k in ("REB", "AST", "STL", "BLK", "3PM") if k not in priced)
    else:
        fp += SHOOT_PER_PT * priced["PTS"]
    return round(fp, 2)


def shooting_ratio(sleeper_games, weights=None):
    """League-wide (unpriced terms in FP) / points from Sleeper boxes, for players Sleeper doesn't cover."""
    w = weights or nba_logs.scoring_weights()
    num = den = 0.0
    for games in sleeper_games.values():
        for g in games.values():
            b = g.get("box") or {}
            if b.get("PTS"):
                num += sum(w.get(k, 0) * b.get(k, 0) for k in UNPRICED)
                den += b["PTS"]
    return num / den if den else 0.0


if __name__ == "__main__":
    import pool as player_pool
    today = date.today()
    days = [(today + timedelta(d)).isoformat() for d in range(2)]
    props = fetch_props(days, force=True)
    n = sum(len(v) for v in props.values())
    print(f"props: {len(props)} players, {n} player-games with lines on {days}")
    pool = player_pool.fetch_pool()
    pro = json.loads((ROOT / "data" / "raw" / str(CURRENT) / "proschedule.json").read_text(encoding="utf-8"))
    abbrev = {t["id"]: t.get("abbrev", "") for t in pro["settings"]["proTeams"]}
    sleeper, _, _ = player_pool.sleeper_games(pool, abbrev)
    SHOOT_PER_PT = shooting_ratio(sleeper)
    pairs = []
    for aid, games in props.items():
        for day, st in games.items():
            sb = (sleeper.get(aid) or {}).get(day)
            m = market_fp(st, sb and sb.get("box"))
            if m is not None and sb:
                pairs.append((m, sb["fp"], aid, day, st))
    if pairs:
        import numpy as np
        x = np.array([p[0] for p in pairs]); y = np.array([p[1] for p in pairs])
        print(f"market vs Sleeper FP: corr {np.corrcoef(x, y)[0, 1]:.2f}, mean {x.mean():.1f} vs {y.mean():.1f} ({len(pairs)} games)")
        m, s, aid, day, st = max(pairs, key=lambda p: p[0])
        print(f"example: {pool.get(aid, {}).get('name')} on {day}: market means {st} -> {m} FP (Sleeper {s})")
        print(f"   Sleeper box: {(sleeper[aid][day]['box'])}")
