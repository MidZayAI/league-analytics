"""The whole NBA player pool, from two sources, in the league's scoring.

ESPN (kona_player_info, our league API): every player's ESPN season projection, season / last-15 /
last-season averages (ESPN applies the league's scoring), ownership across ESPN, outlook, injury
tag, current NBA team and league roster status.

Sleeper (public API, no login): per-game projections for each upcoming game, refreshed daily,
re-scored here with the league's scoring weights. Sleeper has no ESPN ids, so players are
matched on name + team.

Usage: python pipeline/pool.py   (prints match rates and a sanity check)
"""
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nba_logs  # noqa: E402
from espn_client import ESPNClient, ROOT  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "tidy"  # not committed; kept between CI runs by the actions cache
POOL_TTL = 2 * 3600       # seconds between ESPN pool pulls (the site refreshes every 10 min)
SLEEPER_TTL = 3 * 3600
SLEEPER = "https://api.sleeper.app"
POS = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C"}
# Sleeper box field -> our box field (nba_logs.scoring_weights keys)
SLEEPER_FIELD = {"pts": "PTS", "blk": "BLK", "stl": "STL", "ast": "AST", "reb": "REB", "to": "TO",
                 "fgm": "FGM", "fga": "FGA", "ftm": "FTM", "fta": "FTA", "tpm": "3PM"}
# team abbreviations differ between ESPN and Sleeper; map both to one spelling
TEAM_ALIAS = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX", "BRK": "BKN"}


def _cached(path, ttl):
    if path.exists():
        d = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - d.get("_fetched", 0) < ttl:
            return d
    return None


def _save(path, d):
    d["_fetched"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
    return d


def _split(stats, sid):
    s = next((x for x in stats or [] if x.get("id") == sid), None)
    if not s:
        return None, 0, None
    st = s.get("stats") or {}
    gp = st.get("42") or 0
    return (s.get("appliedAverage") if gp else None), gp, ((st.get("40") or 0) / gp if gp else None)


def fetch_pool(season=CURRENT, force=False):
    """id -> player summary (see module doc). Cached for POOL_TTL."""
    path = CACHE / f"pool_{season}.json"
    d = None if force else _cached(path, POOL_TTL)
    if d:
        return {int(k): v for k, v in d["players"].items()}
    raw = ESPNClient().players(season, limit=1500) or {}
    out = {}
    for e in raw.get("players", []):
        p = e["player"]
        st = p.get("stats")
        proj, proj_gp, proj_min = _split(st, f"10{season}")
        cur, n, cur_min = _split(st, f"00{season}")
        l15, n15, _ = _split(st, f"02{season}")
        prev, prev_gp, prev_min = _split(st, f"00{season - 1}")
        own = p.get("ownership") or {}
        out[p["id"]] = {
            "name": p.get("fullName"), "pro": p.get("proTeamId"), "pos": POS.get(p.get("defaultPositionId"), ""),
            "proj": proj, "proj_gp": proj_gp, "proj_min": proj_min,
            "avg": cur, "n": n, "min": cur_min, "l15": l15, "n15": n15,
            "prev": prev, "prev_gp": prev_gp, "prev_min": prev_min,
            "owned": own.get("percentOwned"), "owned_chg": own.get("percentChange"),
            "inj": p.get("injuryStatus"), "outlook": (p.get("seasonOutlook") or "").strip()[:600] or None,
            "on_team": e.get("onTeamId", 0), "status": e.get("status"),
        }
    _save(path, {"players": out})
    print(f"pool: {len(out)} players from ESPN")
    return out


def _norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def _team(abbr):
    a = (abbr or "").upper()
    return TEAM_ALIAS.get(a, a)


def sleeper_games(pool, espn_abbrev, weeks_ahead=2, force=False):
    """ESPN id -> {date: {"fp", "min"}} for upcoming games (Sleeper per-game projections in league scoring),
    plus a dict of Sleeper's injury status by ESPN id and the share of pool players Sleeper matched."""
    path = CACHE / f"sleeper_{CURRENT}.json"
    d = None if force else _cached(path, SLEEPER_TTL)
    if d is None:
        try:
            state = requests.get(f"{SLEEPER}/v1/state/nba", timeout=30).json()
            rows = []
            for w in range(int(state["week"]), int(state["week"]) + weeks_ahead):
                rows += requests.get(f"{SLEEPER}/projections/nba/{state['season']}/{w}",
                                     params={"season_type": "regular"}, timeout=60).json() or []
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f"sleeper: unavailable ({e})")
            return {}, {}, 0.0
        keep = []
        for r in rows:
            p, s = r.get("player") or {}, r.get("stats") or {}
            keep.append({"name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(), "team": r.get("team") or p.get("team"),
                         "date": r.get("date"), "inj": p.get("injury_status"),
                         "box": {SLEEPER_FIELD[k]: v for k, v in s.items() if k in SLEEPER_FIELD}, "sp": s.get("sp")})
        d = _save(path, {"rows": keep})
    weights = nba_logs.scoring_weights()
    by_key, by_last = {}, {}
    for aid, p in pool.items():
        t = _team(espn_abbrev.get(p["pro"], ""))
        by_key[(_norm(p["name"]), t)] = aid
        by_last.setdefault((_norm((p["name"] or "").split(" ")[-1]), t), []).append(aid)
    games, inj, seen, hit = {}, {}, set(), set()
    for r in d["rows"]:
        t = _team(r["team"])
        key = (_norm(r["name"]), t)
        seen.add(key)
        aid = by_key.get(key)
        if aid is None:
            cand = by_last.get((_norm(r["name"].split(" ")[-1]), t), [])
            aid = cand[0] if len(cand) == 1 else None
        if aid is None or not r["date"]:
            continue
        hit.add(key)
        if r["inj"]:
            inj[aid] = r["inj"]
        if r["box"]:
            games.setdefault(aid, {})[r["date"]] = {
                "fp": round(sum(w * r["box"].get(k, 0) for k, w in weights.items()), 2),
                "min": round((r["sp"] or 0) / 60, 1)}
    rate = len(hit) / max(len(seen), 1)
    return games, inj, rate


if __name__ == "__main__":
    pool = fetch_pool(force="--force" in sys.argv)
    pro = json.loads((RAW / str(CURRENT) / "proschedule.json").read_text(encoding="utf-8"))
    abbrev = {t["id"]: t.get("abbrev", "") for t in pro["settings"]["proTeams"]}
    games, inj, rate = sleeper_games(pool, abbrev, force="--force" in sys.argv)
    print(f"sleeper: {len(games)} players with game projections, name match {rate:.0%}")
    pairs = [(pool[a]["proj"], np.mean([g["fp"] for g in gs.values()])) for a, gs in games.items() if pool[a]["proj"]]
    if pairs:
        x, y = np.array(pairs).T
        print(f"ESPN season projection vs Sleeper per-game: corr {np.corrcoef(x, y)[0, 1]:.2f}, "
              f"mean {x.mean():.1f} vs {y.mean():.1f} FP/g")
    fas = sorted((p for p in pool.values() if p["on_team"] == 0 and p["proj"]), key=lambda p: -p["proj"])[:60]
    miss = [p["name"] for p in fas if next((a for a, q in pool.items() if q is p), None) not in games and p["inj"] in (None, "ACTIVE")]
    print(f"top-60 free agents without Sleeper games: {miss[:15]}")
    print(f"generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
