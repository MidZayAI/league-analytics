"""Live forecast for the current matchup week -> site/data/live.json (+ win-probability snapshots).

Usage: python pipeline/live.py     (build.py calls this when the current season is in progress)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expected  # noqa: E402
import gameproj  # noqa: E402
import lineups  # noqa: E402
import injuries  # noqa: E402
import nba_logs  # noqa: E402
import pool as player_pool  # noqa: E402
import season_sim  # noqa: E402
import waivers  # noqa: E402
import projections as pj  # noqa: E402
from espn_client import ESPNClient, ROOT  # noqa: E402
from normalize import RAW, cached_seasons, load_season  # noqa: E402
from simulate import SIMS, simulate_team, win_prob  # noqa: E402

CURRENT = int(os.environ.get("SEASON", 2027))
SNAP_DIR = ROOT / "data" / "history"
OUT = ROOT / "site" / "data" / "live.json"
SNAP_MIN_GAP = 9 * 60  # seconds between stored win-probability snapshots (refresh runs every 10 min)


def split(stats, season, source, split_type, sp=0):
    for s in stats or []:
        if (s.get("seasonId"), s.get("statSourceId"), s.get("statSplitTypeId"), s.get("scoringPeriodId")) == (season, source, split_type, sp):
            return s.get("appliedTotal") or 0.0, (s.get("stats") or {}).get("42") or 0.0
    return 0.0, 0.0


def per_game(total_gp):
    total, gp = total_gp
    return (total / gp if gp else np.nan), gp


def game_starts(proschedule):
    """(pro team id, sp) -> game start (epoch seconds)."""
    out = {}
    for t in proschedule["settings"]["proTeams"]:
        for sp, games in (t.get("proGamesByScoringPeriod") or {}).items():
            for g in games:
                out[(t["id"], int(sp))] = g["date"] / 1000
    return out


def nba_game_status(day):
    """pro team id -> {state: pre|in|post, frac: share of the game played, label} from ESPN's public
    scoreboard (no login needed). Empty if the scoreboard can't be reached."""
    import requests
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
        events = requests.get(url, params={"dates": day.strftime("%Y%m%d")}, timeout=20).json().get("events", [])
    except Exception:
        return {}
    out = {}
    for e in events:
        c = e["competitions"][0]
        st = c["status"]
        state, period = st["type"]["state"], st.get("period") or 0
        frac = 0.0
        if state == "in":
            try:
                mins, secs = (st.get("displayClock") or "0:00").split(":") if ":" in str(st.get("displayClock")) else (0, st.get("displayClock") or 0)
                left = float(mins) + float(secs) / 60
            except ValueError:
                left = 0.0
            # regulation = 4 x 12 min; overtime counts as nearly done
            frac = 0.97 if period > 4 else min(0.98, ((period - 1) * 12 + (12 - left)) / 48)
        elif state == "post":
            frac = 1.0
        for comp in c["competitors"]:
            out[int(comp["team"]["id"])] = {"state": state, "frac": frac, "label": st["type"].get("shortDetail", "")}
    return out


class InjuryAvailability:
    """Chance to play + teammate usage bump for any player's future game (live).
    Uses the league's injury tags, ESPN's NBA injury feed (status + expected return date), each
    player's current miss streak and the historical return curve (injuries.py)."""

    def __init__(self, roster, dates, sp_now, starts, pro_games, now, pool=None):
        nba_logs.fetch_season(CURRENT - 1)
        nba_logs.fetch_season(CURRENT)
        self.dates, self.curve = dates, pj.PARAMS.get("return_curve")
        today = dates[sp_now].isoformat()
        cache = ROOT / "data" / "tidy" / f"injstate_v4_{CURRENT}_{today}.pkl"  # bump v when InjuryState changes
        if cache.exists():
            self.state = pd.read_pickle(cache)
        else:
            self.state = injuries.InjuryState(CURRENT, today, pool=pool)
            cache.parent.mkdir(parents=True, exist_ok=True)
            pd.to_pickle(self.state, cache)
        try:
            self.feed = nba_logs.injury_feed()
        except Exception:
            self.feed = {}
        self.tags = {e["playerId"]: e["playerPoolEntry"]["player"].get("injuryStatus")
                     for tm in roster["teams"] for e in tm["roster"]["entries"]}
        # h = which upcoming game this is for the team (games already under way don't count)
        self.game_no, count = {}, {}
        for sp in sorted(x for x in pro_games if x >= sp_now):
            for pro in pro_games[sp]:
                if sp == sp_now and starts.get((pro, sp), 0) <= now:
                    continue
                count[pro] = count.get(pro, 0) + 1
                self.game_no[(pro, sp)] = count[pro]

    def _status(self, aid, tag=None):
        f = self.feed.get(aid)
        if f:
            return f["status"], f["return"]
        tag = tag or self.tags.get(aid)
        return (None if tag in (None, "ACTIVE", "NORMAL") else tag), None

    def p_play(self, aid, base, tag, pro, sp):
        st, ret = self._status(aid, tag)
        return injuries.play_p(self.curve, base, st, self.state.streak.get(aid, 0), self.game_no.get((pro, sp), 1),
                               self.dates[sp].isoformat(), ret)

    def boost(self, aid, pro, sp):
        """(extra expected FP, 'Name out') for this game from key teammates likely to sit."""
        h, day = self.game_no.get((pro, sp), 1), self.dates[sp].isoformat()

        def sit(k):
            st, ret = self._status(k)
            return 1 - injuries.play_p(self.curve, 1 - self.state.sit_base(k), st, self.state.streak.get(k, 0), h, day, ret)
        total, best = self.state.boost(aid, sit)
        why = f"{self.state.name.get(best[0], 'teammate')} out" if best else None
        return total, why


def play_chance(status, days_ahead, base):
    if status in pj.STATUS_PLAY:
        if status in ("OUT", "INJURY_RESERVE"):
            return pj.OUT_RETURN * base if days_ahead >= 3 else 0.0
        if status == "DAY_TO_DAY":
            return pj.STATUS_PLAY[status] if days_ahead <= 1 else base
        return pj.STATUS_PLAY[status]
    return base


ODDS_OUT = ROOT / "site" / "data" / "odds.json"
INJ_OUT = ROOT / "site" / "data" / "injuries.json"
MIN_FP_SHOWN = 25.0      # injured players shown: 25+ FP/game, or on a league roster
MIN_BUMP_SHOWN = 0.75    # beneficiaries shown: model boost of 0.75+ FP/game while he sits


def write_injuries(avail, roster, abbrev, sp_now):
    """Injury report for the site: every relevant injured NBA player and who benefits while he sits."""
    st = avail.state
    owner = {e["playerId"]: tm["id"] for tm in roster["teams"] for e in tm["roster"]["entries"]}
    pro_of = {e["playerId"]: e["playerPoolEntry"]["player"].get("proTeamId") for tm in roster["teams"] for e in tm["roster"]["entries"]}
    is_out = lambda a: (avail.feed.get(a) or {}).get("status") == "Out" or avail.tags.get(a) in ("OUT", "INJURY_RESERVE")
    helps = {}
    for t_aid, lst in st.bumps.items():
        for k_aid, b in lst:
            helps.setdefault(k_aid, []).append((t_aid, b))
    injured = {aid: f for aid, f in avail.feed.items()}
    for aid, tag in avail.tags.items():  # league tags not (yet) in the NBA report
        if tag in ("OUT", "INJURY_RESERVE", "DAY_TO_DAY") and aid not in injured:
            injured[aid] = {"status": "Out" if tag != "DAY_TO_DAY" else "Day-To-Day", "return": None}

    def next_game(pro):
        sps = sorted(sp for (q, sp), h in avail.game_no.items() if q == pro)
        return sps[0] if sps else None

    players = []
    for aid, f in injured.items():
        fp, pro = st.fp.get(aid, 0.0), st.team.get(aid) or pro_of.get(aid)
        if fp < MIN_FP_SHOWN and aid not in owner:
            continue
        sp = next_game(pro) if pro else None
        p_next = avail.p_play(aid, 1 - st.sit_base(aid), None, pro, sp) if sp else None
        sit_more = max(0.0, (1 - p_next) - st.sit_base(aid)) if p_next is not None else 1.0
        bens = []
        for t_aid, b in sorted(helps.get(aid, []), key=lambda x: -x[1]):
            if b < MIN_BUMP_SHOWN or is_out(t_aid):  # an injured teammate can't absorb minutes
                continue
            ev = st.evidence.get((aid, t_aid), {})
            row = {"id": t_aid, "name": st.name.get(t_aid), "pos": st.pos.get(t_aid), "owner": owner.get(t_aid),
                   "bump": round(b, 1), "fp": round(st.fp.get(t_aid, 0.0), 1), "n": ev.get("n"),
                   "min_in": ev.get("min_in"), "min_out": ev.get("min_out"), "season": ev.get("season"),
                   "role": bool(ev.get("role")), "next": round(b * sit_more, 1)}
            bens.append(row)
        players.append({"id": aid, "name": st.name.get(aid), "team": abbrev.get(pro, ""), "pos": st.pos.get(aid),
                        "status": f["status"], "return": f.get("return"), "streak": st.streak.get(aid, 0),
                        "fp": round(fp, 1), "owner": owner.get(aid), "p_next": p_next, "helps": bens[:5]})
    players.sort(key=lambda r: (r["owner"] is None, -r["fp"]))
    INJ_OUT.write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "players": players,
    }, default=float, separators=(",", ":")), encoding="utf-8")
    print(f"injuries: {len(players)} injured players shown, "
          f"{sum(1 for p in players for h in p['helps'] if h['role'])} role-based beneficiaries")


def write_odds(t, mp, sp_now, team_out, cur_draws, names, rng, avail):
    """Season simulation from now: playoff / bye / title odds, stakes, rooting guide, clinch flags."""
    reg, rounds, n_po = season_sim.league_format(CURRENT, t)
    caps = t["caps"].set_index("mp")
    m = t["matchups"]
    g = m[m.mp.isin(reg) & m.opp_id.notna() & (m.team_id < m.opp_id)]
    done = [(int(r.team_id), int(r.opp_id), float(r.pts), float(r.opp_pts)) for r in g.itertuples() if r.mp < mp and r.result]
    future = [(int(r.mp), int(r.team_id), int(r.opp_id)) for r in g.itertuples() if r.mp >= mp]
    teams = sorted(team_out)
    players = {tid: [{"pid": pid, "pro": pro, "mu": mu, "base": base, "inj": inj} for pro, mu, base, inj, pid in team_out[tid]["model_players"]]
               for tid in teams}
    later = sorted({f[0] for f in future if f[0] > mp}) + [r for r in rounds if r in caps.index]
    exp = season_sim.expected_scores(players, later, caps, t["pro_games"], sp_now, rng,
                                     lambda pl, sp, h: (avail.p_play(pl["pid"], pl["base"], pl["inj"], pl["pro"], sp),
                                                        pl["mu"] + avail.boost(pl["pid"], pl["pro"], sp)[0]))
    params = {**season_sim.DEFAULTS, **{k: pj.PARAMS[k] for k in ("week_cv", "odds_drift", "odds_shrink") if k in pj.PARAMS}}
    out = season_sim.simulate(teams, done, future, exp, [r for r in rounds if r in caps.index], n_po, params, SIMS, rng,
                              current={"mp": mp, "draws": cur_draws} if cur_draws else None)
    res = season_sim.summarize(teams, out, n_po, None)

    wins_now = {tid: 0 for tid in teams}
    losses_now = {tid: 0 for tid in teams}
    for a, b, pa, pb in done:
        if pa != pb:
            wins_now[a if pa > pb else b] += 1
            losses_now[b if pa > pb else a] += 1
    left = {tid: sum(1 for _, a, b in future if tid in (a, b)) for tid in teams}
    flags = season_sim.clinch_flags(teams, wins_now, left, n_po)
    for r in res:
        r.update(flags[r["team_id"]], w=wins_now[r["team_id"]], l=losses_now[r["team_id"]], name=names.get(r["team_id"]))

    hist_path = SNAP_DIR / f"odds_{CURRENT}.json"
    hist = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else {}
    hist[str(mp)] = {str(r["team_id"]): {k: round(r[k], 4) for k in ("playoff", "bye", "title")} for r in res}
    hist_path.write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")

    ODDS_OUT.write_text(json.dumps({
        "season": CURRENT, "mp": mp, "sp_now": sp_now, "n_playoff": n_po, "sims": SIMS, "params": params,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "teams": res,
    }, default=float, separators=(",", ":")), encoding="utf-8")
    print("odds: " + ", ".join(f"{r['name']} {r['playoff']:.0%}/{r['title']:.0%}" for r in sorted(res, key=lambda r: -r["title"])))


def histogram(values, bins=40):
    counts, edges = np.histogram(values, bins=bins)
    return {"edges": [round(float(e), 1) for e in edges], "counts": counts.tolist()}


def main():
    now = time.time()
    c = ESPNClient()
    league = c.league(CURRENT, ["mMatchupScore", "mLiveScoring", "mStatus", "mTeam"])
    status = league["status"]
    t = load_season(CURRENT)
    caps = t["caps"].set_index("mp")
    mp = status.get("currentMatchupPeriod")
    sp_now = status.get("latestScoringPeriod")
    if not mp or mp not in caps.index or t["complete"]:
        OUT.write_text(json.dumps({"active": False}), encoding="utf-8")
        print("live: no matchup in progress")
        return

    cap_row = caps.loc[mp]
    week = list(range(int(cap_row.sp_start), int(cap_row.sp_end) + 1))
    dates = dict(zip(t["calendar"].sp, t["calendar"].date))
    pro_raw = json.loads((RAW / str(CURRENT) / "proschedule.json").read_text(encoding="utf-8"))
    starts = game_starts(pro_raw)
    abbrev = {p["id"]: p.get("abbrev", "").upper() for p in pro_raw["settings"]["proTeams"]}

    # history-based play rates + cv (current season so far, previous seasons for the prior)
    model = pj.HistoryModel({s: load_season(s) for s in cached_seasons() if s >= CURRENT - 1})
    raw_state = model.raw_state(CURRENT, sp_now)
    P = pj.PARAMS
    exp_ctx = expected.SeasonContext(CURRENT, t, model)
    done_days = [sp for sp in week if sp < sp_now]
    games_now = nba_game_status(dates[sp_now])  # today's NBA games: not started / in progress / final

    roster = c.league(CURRENT, ["mRoster"], scoring_period=sp_now)
    try:
        pool = player_pool.fetch_pool()
    except Exception as e:  # the pool only sharpens baselines; never block the live forecast on it
        print(f"pool unavailable: {e}")
        pool = None
    sleeper = None
    if pool:
        sleeper = player_pool.sleeper_games(pool, abbrev)
        for aid, games in sleeper[0].items():  # Sleeper's average upcoming projection = the starting estimate
            fps = [g["fp"] for g in games.values() if g.get("min")]
            if fps and aid in pool:
                pool[aid]["sl"] = float(np.mean(fps))
    avail = InjuryAvailability(roster, dates, sp_now, starts, t["pro_games"], now, pool)
    proj = None
    if pool:
        weeks_ahead = [sp for sp in range(sp_now, int(caps.loc[mp].sp_end) + 1)]
        if mp + 1 in caps.index:
            weeks_ahead += list(range(int(caps.loc[mp + 1].sp_start), int(caps.loc[mp + 1].sp_end) + 1))
        gameproj.grade(dates[sp_now].isoformat())
        proj = gameproj.GameProjector(pool, abbrev, avail, dates, [sp for sp in weeks_ahead if sp in t["pro_games"]],
                                      model.repl, sleeper)
    daily = t["daily"]
    names = dict(zip(t["teams"].team_id, t["teams"].name))
    rng = np.random.default_rng()

    team_out = {}
    for team in roster["teams"]:
        tid = team["id"]
        rows, grid, in_progress, model_players = [], [], [], []
        for e in team["roster"]["entries"]:
            pl = e["playerPoolEntry"]["player"]
            pid, pro = pl["id"], pl.get("proTeamId")
            st = pl["stats"]
            season_avg, n = per_game(split(st, CURRENT, 0, 0))
            recent_avg, n15 = per_game(split(st, CURRENT, 0, 2))
            proj_pg, proj_gp = per_game(split(st, CURRENT, 1, 0))
            prev_avg, prev_gp = per_game(split(st, CURRENT - 1, 0, 0))
            prior = proj_pg if proj_gp else (prev_avg if prev_gp >= 10 else model.repl)
            mu = float(pj.blend_mean(prior, season_avg, n, recent_avg, n15, P["k"], P["r"]))
            if proj and pid in pool:
                mu = proj.mu(pid)  # Sleeper-based starting estimate instead of ESPN's preseason one
            sd = float(pj.spread(mu, model.cv))
            hist = raw_state.loc[pid] if pid in raw_state.index else None
            base = float(pj.play_prob(hist.played if hist is not None else 0,
                                      hist.opps if hist is not None else 0, P["p_base"], P["p_k"]))
            inj = pl.get("injuryStatus") or "ACTIVE"

            days = []
            for sp in week:
                has_game = (pro, sp) in starts
                g_now = games_now.get(pro) if sp == sp_now and has_game else None
                if g_now and g_now["state"] == "in":
                    # game in progress: points so far count now; project the rest of his game too
                    pts, gp = split(st, CURRENT, 0, 5, sp)
                    active = e.get("lineupSlotId") not in (12, 13)
                    rem = mu * (1 - g_now["frac"]) if active and gp else 0.0
                    if rem > 0:
                        in_progress.append((rem, sd * max(0.2, (1 - g_now["frac"])) ** 0.5))
                    days.append({"sp": sp, "state": "live", "pts": pts, "counted": bool(active and gp),
                                 "label": g_now["label"], "frac": g_now["frac"], "rem": rem})
                    continue
                started = starts.get((pro, sp), 0) <= now and not (g_now and g_now["state"] == "pre")
                if sp < sp_now or (sp == sp_now and started):
                    if sp == sp_now:
                        pts, gp = split(st, CURRENT, 0, 5, sp)
                        counted = e.get("lineupSlotId") not in (12, 13)
                    else:
                        d = daily[(daily.team_id == tid) & (daily.sp == sp) & (daily.player_id == pid)]
                        pts, gp = (float(d.pts.iloc[0]), float(d.gp.iloc[0])) if len(d) else (0.0, 0.0)
                        counted = bool(d.counted.iloc[0]) if len(d) else False
                    days.append({"sp": sp, "state": "played" if gp else ("missed" if has_game else "none"),
                                 "pts": pts if gp else None, "counted": bool(counted and gp)})
                elif has_game:
                    if proj and pid in pool:  # this game's blend: our model + Sleeper + betting lines + boost
                        g = proj.game(pid, pro, sp, base, inj, record=True)
                        p, b, why, g_mu = g["p"], g["boost"], g["why"], g["fp"]
                    else:
                        p = avail.p_play(pid, base, inj, pro, sp)
                        b, why = avail.boost(pid, pro, sp)
                        g_mu = mu + b
                    rows.append({"sp": sp, "mu": g_mu, "sd": float(pj.spread(g_mu, model.cv)), "p": p, "player_id": pid})
                    day = {"sp": sp, "state": "game", "p": p, "fp": round(g_mu, 1)}
                    if b >= 0.5:
                        day.update(boost=round(b, 1), why=why)
                    days.append(day)
                else:
                    days.append({"sp": sp, "state": "none"})
            model_players.append((pro, mu, base, inj, pid))
            grid.append({"id": pid, "name": pl.get("fullName"), "pro": abbrev.get(pro, ""), "inj": inj,
                         "slot": e.get("lineupSlotId"), "mu": mu, "days": days})

        cands = pd.DataFrame(rows, columns=["sp", "mu", "sd", "p", "player_id"])
        team_out[tid] = {"cands": cands, "grid": grid, "in_progress": in_progress, "model_players": model_players}

    snaps_path = SNAP_DIR / f"winprob_{CURRENT}.json"
    snaps = json.loads(snaps_path.read_text(encoding="utf-8")) if snaps_path.exists() else []
    matchups, snap, cur_draws = [], {}, {}
    for m in league["schedule"]:
        if m.get("matchupPeriodId") != mp or not m.get("away"):
            continue
        sides = []
        for key in ("home", "away"):
            s = m[key]
            tid = s["teamId"]
            live = s.get("cumulativeScoreLive") or s.get("cumulativeScore") or {}
            gp_used = ((live.get("statBySlot") or {}).get("15") or {}).get("value") or 0
            cur = s.get("totalPointsLive", s.get("totalPoints")) or 0.0
            rem_cap = int(cap_row.cap - gp_used)
            sims, start_p, games = simulate_team(team_out[tid]["cands"], rem_cap, SIMS, rng, P.get("team_sd", 0.0))
            # rest of games already under way: already counted against the cap, so just add their points
            for rem, rsd in team_out[tid]["in_progress"]:
                sims = sims + rng.gamma((rem / rsd) ** 2, rsd ** 2 / rem, size=SIMS)
            cands = team_out[tid]["cands"].assign(start=start_p)
            for g in team_out[tid]["grid"]:
                for d in g["days"]:
                    if d["state"] == "game":
                        hit = cands[(cands.player_id == g["id"]) & (cands.sp == d["sp"])]
                        d["start"] = float(hit.start.iloc[0]) if len(hit) else 0.0
            final = cur + sims
            # finished days + today's games that are already final (in-progress games stay out)
            final_today = {pro for pro, g in games_now.items() if g["state"] == "post"}
            res = expected.week_breakdown(exp_ctx, tid, int(mp), week, int(cap_row.cap), rng,
                                          done_days + [sp_now], (sp_now, final_today))
            so_far = None
            if res and (done_days or final_today):
                so_far, players = res
                so_far["hot"], so_far["cold"] = expected.top_players(players)
                so_far["days_done"] = len(done_days)
                so_far["today_final_games"] = sum(1 for g in games_now.values() if g["state"] == "post") // 2
            sides.append({"team_id": tid, "name": names.get(tid), "pts": cur, "gp_used": gp_used,
                          "cap": int(cap_row.cap), "rem_cap": rem_cap,
                          "avail_games": float(cands.p.sum()), "exp_games": float(games.mean()),
                          "proj": float(final.mean()), "p10": float(np.percentile(final, 10)),
                          "p90": float(np.percentile(final, 90)), "_final": final, "expected_so_far": so_far,
                          "players": sorted(team_out[tid]["grid"], key=lambda g: -g["mu"])})
        a, b = sides
        fa, fb = a.pop("_final"), b.pop("_final")
        cur_draws[a["team_id"]], cur_draws[b["team_id"]] = fa, fb
        pa = win_prob(a["pts"], fa - a["pts"], b["pts"], fb - b["pts"])
        a["win_prob"], b["win_prob"] = pa, 1 - pa
        matchups.append({"matchup_id": m["id"], "home": a, "away": b,
                         "proj_margin": a["proj"] - b["proj"], "margin_hist": histogram(fa - fb)})
        snap[str(m["id"])] = {str(a["team_id"]): round(pa, 4), str(b["team_id"]): round(1 - pa, 4)}

    # keep one point per SNAP_MIN_GAP window; a rerun inside the window replaces the latest point
    point = {"ts": now, "sp": sp_now, "mp": int(mp), "probs": snap}
    anchor = snaps[-1].get("anchor", snaps[-1]["ts"]) if snaps else None
    if snaps and now - anchor < SNAP_MIN_GAP and snaps[-1]["sp"] == sp_now:
        snaps[-1] = point | {"anchor": anchor}
    else:
        snaps.append(point)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snaps_path.write_text(json.dumps(snaps, separators=(",", ":")), encoding="utf-8")

    out = {
        "active": True, "season": CURRENT, "mp": int(mp), "sp_now": sp_now, "cap": int(cap_row.cap),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "days": [{"sp": sp, "date": dates[sp].isoformat(), "game_day": sp in t["pro_games"]} for sp in week],
        "matchups": matchups, "params": P, "sims": SIMS,
        "snapshots": [s for s in snaps if s["mp"] == mp],
    }
    OUT.write_text(json.dumps(out, default=float, separators=(",", ":")), encoding="utf-8")
    write_odds(t, int(mp), sp_now, team_out, cur_draws, names, rng, avail)
    write_injuries(avail, roster, abbrev, sp_now)
    if proj:
        fa = []
        try:
            fa = waivers.build(pool, abbrev, proj, t, int(mp), sp_now, starts, now, roster)
        except Exception as e:  # never fail the live forecast over the waiver list
            print(f"waivers skipped: {e}")
        try:
            rem_caps = {s["team_id"]: s["rem_cap"] for m in matchups for s in (m["home"], m["away"])}
            lineups.build(proj, t, int(mp), sp_now, starts, now, roster, rem_caps, pool, abbrev, fa, week, team_out)
        except Exception as e:
            print(f"lineups skipped: {e}")
        proj.save_log()
    print(f"live: week {mp}, day {sp_now}: " + ", ".join(
        f"{x['home']['name']} {x['home']['win_prob']:.0%} vs {x['away']['name']}" for x in matchups))


if __name__ == "__main__":
    main()
