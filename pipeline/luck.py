"""Luck metrics for H2H points.

- All-play: each week, your score vs every other team's. Expected wins = share of teams you beat.
  Luck = actual wins - expected wins (regular season).
- Matchup luck card: your weekly rank, your opponent's rank, and whether you lost while
  out-scoring most of the league (bad beat) or won while under it (lucky win).
- Schedule swap: your record had you played each other team's schedule.
- Absences: average points of top-10 players whose NBA team played but who sat (injury/rest).
- Availability: games your roster could fill vs the cap (from coaching max_games).
"""
import pandas as pd


def weekly(t):
    """Matchup rows + all-play, ranks and opponent context."""
    m = t["matchups"][t["matchups"].opp_id.notna() & t["matchups"].pts.notna()].copy()
    m = m[m.result.notna() | (m.pts > 0)]
    out = []
    for (_, mp), g in m.groupby(["season", "mp"]):
        scores = g.set_index("team_id").pts
        n = len(scores)
        for r in g.itertuples():
            others = scores.drop(r.team_id)
            beat = (others < r.pts).sum() + 0.5 * (others == r.pts).sum()
            out.append({
                "team_id": r.team_id, "mp": mp,
                "allplay_w": beat / (n - 1) if n > 1 else None,
                "beat": float(beat), "n_others": n - 1,
                "rank": int((scores > r.pts).sum() + 1),
                "opp_rank": int((scores > r.opp_pts).sum() + 1),
                "week_avg": float(scores.mean()),
            })
    w = m.merge(pd.DataFrame(out), on=["team_id", "mp"])
    w["margin"] = w.pts - w.opp_pts
    half = (w.n_others + 1) / 2
    w["bad_beat"] = (w.result == "L") & (w["rank"] <= half)
    w["lucky_win"] = (w.result == "W") & (w["rank"] > half)
    return w.drop(columns=["espn_by_sp"])


def season_luck(w):
    """Regular-season, completed weeks only."""
    reg = w[~w.playoff & w.result.notna()].assign(
        is_w=lambda x: x.result == "W", is_l=lambda x: x.result == "L", is_t=lambda x: x.result == "T",
        pa_diff=lambda x: x.opp_pts - x.week_avg)
    g = reg.groupby("team_id")
    s = pd.DataFrame({
        "w": g.is_w.sum(), "l": g.is_l.sum(), "t": g.is_t.sum(),
        "pf": g.pts.sum(), "pa": g.opp_pts.sum(),
        "exp_w": g.allplay_w.sum(),
        "allplay_w": g.beat.sum(), "allplay_g": g.n_others.sum(),
        "bad_beats": g.bad_beat.sum(), "lucky_wins": g.lucky_win.sum(),
        "weeks": g.size(),
    })
    s["luck"] = s.w + 0.5 * s.t - s.exp_w
    s["allplay_pct"] = s.allplay_w / s.allplay_g
    # points against relative to what an average opponent scored those weeks
    s["pa_vs_avg"] = g.pa_diff.sum()
    return s.reset_index()


def schedule_swap(w):
    """matrix[a][b] = (wins, losses) for team a playing team b's regular-season schedule."""
    reg = w[~w.playoff & w.result.notna()]
    score = {(r.team_id, r.mp): r.pts for r in reg.itertuples()}
    opp = {(r.team_id, r.mp): r.opp_id for r in reg.itertuples()}
    teams = sorted(reg.team_id.unique())
    mps = sorted(reg.mp.unique())
    out = {}
    for a in teams:
        out[int(a)] = {}
        for b in teams:
            wins = losses = 0
            for mp in mps:
                if (a, mp) not in score or (b, mp) not in opp:
                    continue
                o = opp[(b, mp)]
                o = b if o == a else o  # b's opponent was a: a plays b instead
                if (o, mp) not in score:
                    continue
                if score[(a, mp)] > score[(o, mp)]:
                    wins += 1
                elif score[(a, mp)] < score[(o, mp)]:
                    losses += 1
            out[int(a)][int(b)] = [wins, losses]
    return out


def absences(t):
    """Per team-week: points lost to top-10 players sitting games their NBA team played."""
    d = t["daily"]
    games = t["pro_games"]
    avg = d[d.gp > 0].groupby("player_id").pts.mean()
    d = d.assign(avg=d.player_id.map(avg).fillna(0.0))
    d = d[d.apply(lambda r: r.pro in games.get(r.sp, ()), axis=1)]
    rows = []
    for (mp, tid), g in d.groupby(["mp", "team_id"]):
        top = set(g.drop_duplicates("player_id").nlargest(10, "avg").player_id)
        miss = g[(g.gp == 0) & g.player_id.isin(top)]
        rows.append({"mp": mp, "team_id": tid, "missed_games": len(miss),
                     "missed_pts": float(miss.avg.sum()),
                     "missed_top": miss.groupby("name").avg.agg(["size", "sum"]).nlargest(3, "sum")
                                       .reset_index().rename(columns={"size": "g", "sum": "pts"})
                                       .to_dict("records")})
    return pd.DataFrame(rows)
