"""Weekly league podcast: an episode pack for NotebookLM, built from the site's own data.

After a week closes this writes site/podcast/drafts/{season}-w{mp:02}/:
  brief.json            every fact the episode may use (all numbers come from site/data)
  source.md             one source document for NotebookLM: run of show + talking points
  prompt.txt            text for NotebookLM's "Customize" box
A draft is written once per week and then left alone (edit source.md freely before recording).

Audio: generate it in NotebookLM, then upload the file to site/podcast/episodes/ (any name;
mp3 / m4a / wav). The next build names it after the newest draft without audio, converts it to
mp3 when ffmpeg is available, and lists it on #/podcast and in the RSS feed (site/podcast/feed.xml).

Optional: with ANTHROPIC_API_KEY set, Claude rewrites the run of show in the show's voice
(config/podcast_style.md). Without it the template version is used.

Usage: python pipeline/podcast.py [--force] [--mp N]
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data"
POD = ROOT / "site" / "podcast"
DRAFTS, EPISODES = POD / "drafts", POD / "episodes"
STYLE = ROOT / "config" / "podcast_style.md"
SITE_URL = os.environ.get("SITE_URL", "https://midzayai.github.io/league-analytics/")
AUDIO = (".mp3", ".m4a", ".wav")
MODEL = "claude-opus-5-5"


def load(name):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def r0(x):
    return int(round(x or 0))


def pct(x):
    return f"{round(100 * (x or 0))}%"


# ---------------------------------------------------------------- the brief (facts only)

def completed_mp(season):
    done = {w["mp"] for w in season["weeks"] if w["result"]}
    open_ = {w["mp"] for w in season["weeks"] if not w["result"]}
    return max((m for m in done if m not in open_), default=None)


def brief(mp=None):
    meta, hist = load("meta.json"), load("history.json")
    s = meta["current_season"]
    season = load(f"season_{s}.json")
    mp = mp or completed_mp(season)
    if mp is None:
        return None
    name = {t["team_id"]: t["name"] for t in season["teams"]}
    owner = {t["team_id"]: t["owner_id"] for t in season["teams"]}
    cap = next((c for c in season["caps"] if c["mp"] == mp), {})
    rows = [w for w in season["weeks"] if w["mp"] == mp]
    by_team = {w["team_id"]: w for w in rows}
    h2h = hist.get("h2h", {})

    def series(a, b):
        r = h2h.get(owner[a], {}).get(owner[b])
        return {"w": r["w"], "l": r["l"]} if r else None

    games = []
    for w in sorted(rows, key=lambda w: -w["pts"]):
        if w["result"] != "W" and not (w["result"] == "T" and w["team_id"] < w["opp_id"]):
            continue
        lo = by_team[w["opp_id"]]
        games.append({
            "winner": name[w["team_id"]], "loser": name[lo["team_id"]], "tie": w["result"] == "T",
            "score": [r0(w["pts"]), r0(lo["pts"])], "margin": r0(w["margin"]),
            "expected": [r0(w.get("e4")), r0(lo.get("e4"))],
            "upset": bool(w.get("flipped") or lo.get("flipped")),
            "series_before": series(w["team_id"], lo["team_id"]),
            "winner_star": _star(w), "loser_star": _star(lo), "loser_bust": _bust(lo),
            "loser_bench_lost": r0(lo.get("bench_lost")), "loser_lineup_cost_game": bool(lo.get("coaching_loss")),
        })

    teams = []
    for w in rows:
        teams.append({
            "team": name[w["team_id"]], "pts": r0(w["pts"]), "result": w["result"], "rank": w["rank"],
            "allplay": f"{r0(w['beat'])}-{r0(w['n_others'] - w['beat'])}",
            "bad_beat": bool(w.get("bad_beat")), "lucky_win": bool(w.get("lucky_win")),
            "bench_lost": r0(w.get("bench_lost")), "efficiency": round(100 * (w.get("efficiency") or 0)),
            "lineup_cost_game": bool(w.get("coaching_loss")), "over_cap": bool(w.get("over_cap")),
            "missed_starts": [{"name": m["name"], "pts": r0(m["pts"])} for m in (w.get("missed_starts") or [])[:3]],
            "expected": r0(w.get("e4")), "vs_expected": r0((w["pts"] or 0) - (w.get("e4") or 0)),
        })
    teams.sort(key=lambda t: -t["pts"])

    players = [{**p, "team": name[w["team_id"]]} for w in rows for p in (w.get("hot") or [])]
    busts = [{**p, "team": name[w["team_id"]]} for w in rows for p in (w.get("cold") or [])]
    pl = lambda p: {"name": p["name"], "team": p["team"], "pts": r0(p["pts"]), "games": p["games"],
                    "vs_usual": r0(p["diff"] * p["games"])}  # noqa: E731
    standings = sorted(season["teams"], key=lambda t: (-t["w"], -t["pf"]))

    out = {
        "season": s, "season_label": f"{s - 1}-{str(s)[2:]}", "mp": mp, "league": meta["league_name"],
        "dates": [cap.get("start"), cap.get("end")], "games_cap": cap.get("cap"), "playoff": bool(cap.get("playoff")),
        "games": games, "teams": teams,
        "high": teams[0]["team"] if teams else None, "low": teams[-1]["team"] if teams else None,
        "breakouts": [pl(p) for p in sorted(players, key=lambda p: -p["diff"] * p["games"])[:4]],
        "busts": [pl(p) for p in sorted(busts, key=lambda p: p["diff"] * p["games"])[:4]],
        "standings": [{"team": t["name"], "w": t["w"], "l": t["l"], "pf": r0(t["pf"]),
                       "luck": round(t.get("luck") or 0, 1)} for t in standings],
        "records": _records(hist, s, mp),
        "odds": _odds(s, mp, name),
        "next_week": _next_week(mp, name, owner, h2h),
        "injuries": _injuries(name),
    }
    return out


def _star(w):
    h = sorted(w.get("hot") or [], key=lambda p: -p["pts"])
    return {"name": h[0]["name"], "pts": r0(h[0]["pts"]), "games": h[0]["games"]} if h else None


def _bust(w):
    c = sorted(w.get("cold") or [], key=lambda p: p["diff"] * p["games"])
    return {"name": c[0]["name"], "pts": r0(c[0]["pts"]), "games": c[0]["games"],
            "vs_usual": r0(c[0]["diff"] * c[0]["games"])} if c else None


def _records(hist, s, mp):
    names = hist["owners"]
    out = []
    for kind, lst in hist.get("records", {}).items():
        for i, r in enumerate(lst):
            if r.get("season") == s and r.get("mp") == mp:
                out.append({"record": kind.replace("_", " "), "place": i + 1, "team": names.get(r["owner_id"]),
                            "value": r0(r.get("pts") if "pts" in r else r.get("bench_lost"))})
    return out


def _odds(s, mp, name):
    snap = ROOT / "data" / "history" / f"odds_{s}.json"
    now = load("odds.json")
    if not now:
        return []
    hist = json.loads(snap.read_text(encoding="utf-8")) if snap.exists() else {}
    before = hist.get(str(mp), {})
    out = []
    for t in now["teams"]:
        b = before.get(str(t["team_id"]), {})
        out.append({"team": name.get(t["team_id"], t.get("name")), "playoff": pct(t["playoff"]), "title": pct(t["title"]),
                    "playoff_change": r0(100 * (t["playoff"] - b["playoff"])) if b else None,
                    "title_change": r0(100 * (t["title"] - b["title"])) if b else None,
                    "proj_record": f"{r0(t['proj_w'])} wins", "clinched": t.get("clinched_playoffs"),
                    "eliminated": t.get("eliminated")})
    return sorted(out, key=lambda o: -float(o["playoff"][:-1]))


def _next_week(mp, name, owner, h2h):
    live, odds = load("live.json"), load("odds.json")
    if not live or live.get("mp") != mp + 1:
        return []
    stakes = {t["team_id"]: t.get("stakes") or {} for t in (odds or {}).get("teams", [])}
    out = []
    for m in live["matchups"]:
        a, b = m["home"]["team_id"], m["away"]["team_id"]
        sa = stakes.get(a, {})
        r = h2h.get(owner[a], {}).get(owner[b])
        out.append({"teams": [name[a], name[b]], "win_prob": [pct(sa.get("p_win")), pct(1 - sa.get("p_win", 0.5))] if sa else None,
                    "proj": [r0(m["home"]["proj"]), r0(m["away"]["proj"])],
                    "playoff_swing": [r0(100 * (sa.get("playoff_w", 0) - sa.get("playoff_l", 0))),
                                      r0(100 * (stakes.get(b, {}).get("playoff_w", 0) - stakes.get(b, {}).get("playoff_l", 0)))],
                    "series": {"w": r["w"], "l": r["l"]} if r else None})
    out.sort(key=lambda g: -sum(g["playoff_swing"]))
    return out


def _injuries(name):
    inj = load("injuries.json")
    if not inj:
        return {"rostered": [], "pickups": []}
    ros = [{"name": p["name"], "team": name.get(p["owner"]), "status": p["status"], "back": p.get("return"),
            "fp": p["fp"], "who_benefits": [h["name"] for h in p["helps"][:2]]}
           for p in inj["players"] if p.get("owner") is not None][:6]
    pk = [{"name": p["name"], "because": p["because"], "bump": p["next"]} for p in inj.get("pickups", [])[:3]]
    return {"rostered": ros, "pickups": pk}


# ---------------------------------------------------------------- source doc + prompt


def source_md(b):
    L = [f"# {b['league']} — Week {b['mp']} recap ({b['season_label']} season)", "",
         f"Week {b['mp']} ran {b['dates'][0]} to {b['dates'][1]}; each team could use {b['games_cap']} player-games. "
         "Every number below is checked against the league's data. Use only these numbers.", ""]
    g = b["games"]
    if g:
        closest, blowout = min(g, key=lambda x: x["margin"]), max(g, key=lambda x: x["margin"])
        L += ["## 1. Cold open: the headline", ""]
        ups = [x for x in g if x["upset"]]
        if ups:
            u = ups[0]
            L.append(f"- Upset of the week: {u['winner']} beat {u['loser']} {u['score'][0]}–{u['score'][1]} "
                     f"even though the model expected {u['loser']} to win ({u['expected'][1]} vs {u['expected'][0]} expected).")
        L.append(f"- Closest game: {closest['winner']} over {closest['loser']} by {closest['margin']}.")
        L.append(f"- Biggest blowout: {blowout['winner']} over {blowout['loser']} by {blowout['margin']}.")
        L.append(f"- High score: {b['teams'][0]['team']} with {b['teams'][0]['pts']}. Low score: {b['teams'][-1]['team']} with {b['teams'][-1]['pts']}.")
        for r in b["records"]:
            L.append(f"- League record book: {r['team']} — #{r['place']} all-time {r['record']} ({r['value']}).")
        L += ["", "## 2. Scoreboard: every matchup", ""]
        for x in g:
            line = f"### {x['winner']} {x['score'][0]}, {x['loser']} {x['score'][1]}"
            L += [line, f"- Margin {x['margin']}. Expected scores were {x['winner']} {x['expected'][0]}, {x['loser']} {x['expected'][1]}."]
            if x["winner_star"]:
                s = x["winner_star"]
                L.append(f"- {x['winner']}'s best performer: {s['name']}, {s['pts']} points in {s['games']} games.")
            if x["loser_bust"] and x["loser_bust"]["vs_usual"] < -15:
                s = x["loser_bust"]
                L.append(f"- {x['loser']} got let down by {s['name']}: {s['pts']} points in {s['games']} games, {abs(s['vs_usual'])} below his usual.")
            if x["loser_lineup_cost_game"]:
                L.append(f"- {x['loser']} would have WON with their best possible lineup (left {x['loser_bench_lost']} points on the bench).")
            if x["series_before"]:
                sr = x["series_before"]
                L.append(f"- All-time series, including this game: {x['winner']} leads {x['loser']} {sr['w']}-{sr['l']}." if sr['w'] >= sr['l']
                         else f"- All-time series, including this game: {x['winner']} trails {x['loser']} {sr['w']}-{sr['l']}.")
            L.append("")
    L += ["## 3. Luck lab", "",
          "All-play record = how this week's score would have done against every other team."]
    for t in b["teams"]:
        tags = [s for s, on in (("bad beat: 2nd-highest-or-better score and still lost", t["bad_beat"]),
                                ("lucky win: below-average score and still won", t["lucky_win"])) if on]
        L.append(f"- {t['team']}: {t['pts']} ({t['result']}), all-play {t['allplay']}" + (f" — {'; '.join(tags)}" if tags else ""))
    luck = sorted(b["standings"], key=lambda t: t["luck"])
    if luck:
        L += [f"- Season luck so far: unluckiest {luck[0]['team']} ({luck[0]['luck']:+} wins vs expected), "
              f"luckiest {luck[-1]['team']} ({luck[-1]['luck']:+})."]
    L += ["", "## 4. Coaching corner: points left on the bench", ""]
    for t in sorted(b["teams"], key=lambda t: -t["bench_lost"])[:4]:
        miss = ", ".join(f"{m['name']} ({m['pts']})" for m in t["missed_starts"])
        L.append(f"- {t['team']}: {t['bench_lost']} points left on the bench ({t['efficiency']}% lineup efficiency)"
                 + (f". Benched: {miss}" if miss else "") + (". This cost them the game." if t["lineup_cost_game"] else "."))
    over = [t["team"] for t in b["teams"] if t["over_cap"]]
    if over:
        L.append(f"- Went over the games cap (breaking the gentleman's agreement): {', '.join(over)}.")
    L += ["", "## 5. Breakouts and busts", ""]
    for p in b["breakouts"]:
        L.append(f"- Breakout: {p['name']} ({p['team']}) — {p['pts']} in {p['games']} games, {p['vs_usual']} more than usual.")
    for p in b["busts"]:
        L.append(f"- Bust: {p['name']} ({p['team']}) — {p['pts']} in {p['games']} games, {abs(p['vs_usual'])} fewer than usual.")
    L += ["", "## 6. Standings and playoff odds", ""]
    for i, t in enumerate(b["standings"], 1):
        L.append(f"{i}. {t['team']} {t['w']}-{t['l']} ({t['pf']} points for)")
    if b["odds"]:
        L.append("")
        for o in b["odds"]:
            ch = (f" ({'up' if o['playoff_change'] > 0 else 'down'} {abs(o['playoff_change'])} percentage points since last week)"
                  if o["playoff_change"] else "")
            flag = " — CLINCHED" if o["clinched"] else (" — ELIMINATED" if o["eliminated"] else "")
            L.append(f"- {o['team']}: {o['playoff']} playoffs{ch}, {o['title']} title{flag}")
    inj = b["injuries"]
    if inj["rostered"] or inj["pickups"]:
        L += ["", "## 7. Injury report and waiver wire", ""]
        for p in inj["rostered"]:
            back = f", expected back {p['back']}" if p["back"] else ""
            ben = f" Who benefits: {', '.join(p['who_benefits'])}." if p["who_benefits"] else ""
            L.append(f"- {p['team']}'s {p['name']} ({p['fp']} fantasy points a game) is {p['status']}{back}.{ben}")
        for p in inj["pickups"]:
            L.append(f"- Pickup idea: {p['name']} (free agent), about +{p['bump']} points a game with {p['because']} out.")
    if b["next_week"]:
        L += ["", f"## 8. Week {b['mp'] + 1} preview", ""]
        for i, m in enumerate(b["next_week"]):
            head = "Game of the week: " if i == 0 else ""
            wp = f" Win chances {m['win_prob'][0]} / {m['win_prob'][1]}." if m["win_prob"] else ""
            ser = f" All-time series {m['series']['w']}-{m['series']['l']}." if m["series"] else ""
            L.append(f"- {head}{m['teams'][0]} vs {m['teams'][1]}: projected {m['proj'][0]}–{m['proj'][1]}.{wp}{ser}"
                     f" Win vs loss swings playoff odds by {m['playoff_swing'][0]} / {m['playoff_swing'][1]} percentage points.")
    L += ["", "## 9. Close: predictions and one-liners", "",
          "- Each host picks a lock of the week and a team on upset alert.", ""]
    if STYLE.exists():
        L += ["---", "", "## Show style notes", "", STYLE.read_text(encoding="utf-8").strip(), ""]
    return "\n".join(L)


def prompt_txt(b):
    return (f"Two hosts recap Week {b['mp']} of our 8-team fantasy basketball league ({b['league']}). "
            "Follow the run of show in order: headline, every matchup, luck, coaching blunders, breakouts and busts, "
            "playoff odds, injuries/pickups, next week's preview, predictions. Use ONLY numbers from the source; "
            "never invent stats. Refer to managers by team name. Fun, trash-talky, sports-radio energy; "
            "roast bad lineup calls. About 12-15 minutes.")


def claude_rewrite(src):
    """Rewrite the run of show in the show's voice; facts stay as given. Returns None without a key."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    style = STYLE.read_text(encoding="utf-8") if STYLE.exists() else ""
    resp = anthropic.Anthropic().beta.messages.create(
        model=MODEL, max_tokens=16000, output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=("You punch up the run of show for a two-host fantasy basketball podcast. Keep the same sections and "
                "every fact and number exactly as given; add no new numbers. Add angles, jokes and callbacks in the "
                "show's style so the hosts have something to riff on. Output markdown only.\n\n" + style),
        messages=[{"role": "user", "content": src}],
    )
    if resp.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resp.content if b.type == "text") or None


def numbers_ok(text, b):
    """Numbers in a rewritten doc that never appear in the brief (catches invented stats)."""
    allowed = set(re.findall(r"\d+", json.dumps(b)))
    return sorted({n for n in re.findall(r"\d+", text) if n not in allowed and len(n) > 1})


def write_draft(b, force=False):
    d = DRAFTS / f"{b['season']}-w{b['mp']:02d}"
    if (d / "source.md").exists() and not force:
        return d, False
    d.mkdir(parents=True, exist_ok=True)
    src = source_md(b)
    polished = None
    try:
        polished = claude_rewrite(src)
    except Exception as e:  # never break the data refresh over the podcast
        print(f"podcast: Claude rewrite skipped ({e})")
    bad = numbers_ok(polished, b) if polished else []
    (d / "brief.json").write_text(json.dumps(b, indent=1), encoding="utf-8")
    (d / "source.md").write_text(polished if polished and not bad else src, encoding="utf-8")
    (d / "prompt.txt").write_text(prompt_txt(b), encoding="utf-8")
    if bad:
        print(f"podcast: Claude version used numbers not in the brief ({bad[:8]}); kept the template version")
    return d, True


# ---------------------------------------------------------------- episodes + feed

def _ffmpeg():
    return shutil.which("ffmpeg")


def _duration(path):
    probe = shutil.which("ffprobe")
    if not probe:
        return None
    try:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return int(float(out))
    except (ValueError, subprocess.SubprocessError):
        return None


def adopt_uploads():
    """Name new uploads after the newest draft without audio; convert to mp3 when possible."""
    EPISODES.mkdir(parents=True, exist_ok=True)
    named = re.compile(r"^\d{4}-w\d{2}\.mp3$")
    drafts = sorted(p.name for p in DRAFTS.glob("*-w*") if p.is_dir()) if DRAFTS.exists() else []
    for f in sorted(EPISODES.iterdir(), key=lambda p: p.stat().st_mtime):
        if f.suffix.lower() not in AUDIO or named.match(f.name):
            continue
        m = re.search(r"(\d{4})-w(\d{1,2})", f.stem)
        have = {p.stem for p in EPISODES.glob("*.mp3") if named.match(p.name)}
        slug = f"{m.group(1)}-w{int(m.group(2)):02d}" if m else next((d for d in reversed(drafts) if d not in have), None)
        if not slug:
            print(f"podcast: no draft to attach {f.name} to; leaving it")
            continue
        dest = EPISODES / f"{slug}.mp3"
        if f.suffix.lower() == ".mp3":
            f.rename(dest)
        elif _ffmpeg():
            subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(f), "-codec:a", "libmp3lame", "-b:a", "128k",
                            str(dest)], check=True, timeout=600)
            f.unlink()
        else:
            print(f"podcast: {f.name} needs ffmpeg to convert to mp3; leaving it")
            continue
        print(f"podcast: published {dest.name}")


def episode_list():
    eps = []
    for f in sorted(EPISODES.glob("*.mp3"), reverse=True) if EPISODES.exists() else []:
        m = re.match(r"(\d{4})-w(\d{2})$", f.stem)
        if not m:
            continue
        season, mp = int(m.group(1)), int(m.group(2))
        bpath = DRAFTS / f.stem / "brief.json"
        b = json.loads(bpath.read_text(encoding="utf-8")) if bpath.exists() else {}
        meta_p = f.with_suffix(".json")
        info = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
        if not info:  # first time we see it: stamp publish time + duration once
            info = {"published": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                    "duration": _duration(f)}
            meta_p.write_text(json.dumps(info), encoding="utf-8")
        eps.append({"slug": f.stem, "season": season, "mp": mp, "file": f"podcast/episodes/{f.name}",
                    "bytes": f.stat().st_size, **info, "title": f"Week {mp} recap",
                    "notes": _notes(b)})
    return eps


def _notes(b):
    if not b:
        return ""
    bits = [f"{g['winner']} beat {g['loser']} {g['score'][0]}-{g['score'][1]}" for g in b.get("games", [])]
    return "; ".join(bits)


def write_feed(eps, league):
    items = []
    for e in eps:
        url = SITE_URL + e["file"]
        dur = f"<itunes:duration>{e['duration']}</itunes:duration>" if e.get("duration") else ""
        pub = format_datetime(datetime.fromisoformat(e["published"]))
        items.append(
            f"<item><title>{escape(e['title'])} ({e['season'] - 1}-{str(e['season'])[2:]})</title>"
            f"<description>{escape(e['notes'])}</description><pubDate>{pub}</pubDate>"
            f"<guid isPermaLink=\"false\">{e['slug']}</guid>"
            f"<enclosure url=\"{escape(url)}\" length=\"{e['bytes']}\" type=\"audio/mpeg\"/>{dur}"
            f"<itunes:episode>{e['mp']}</itunes:episode></item>")
    cover = f"<itunes:image href=\"{SITE_URL}podcast/cover.jpg\"/>" if (POD / "cover.jpg").exists() else ""
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
           "<rss version=\"2.0\" xmlns:itunes=\"http://www.itunes.com/dtds/podcast-1.0.dtd\">"
           f"<channel><title>{escape(league)} Podcast</title><link>{SITE_URL}#/podcast</link><language>en-us</language>"
           f"<description>Weekly recap of the {escape(league)} fantasy basketball league.</description>"
           f"<itunes:author>{escape(league)}</itunes:author><itunes:explicit>true</itunes:explicit>"
           f"<itunes:category text=\"Sports\"/>{cover}{''.join(items)}</channel></rss>\n")
    (POD / "feed.xml").write_text(xml, encoding="utf-8")


def main(force=False, mp=None):
    POD.mkdir(parents=True, exist_ok=True)
    b = brief(mp)
    drafts = []
    if b and b["games"]:
        d, new = write_draft(b, force)
        if new:
            print(f"podcast: new episode pack {d.relative_to(ROOT)}")
    adopt_uploads()
    eps = episode_list()
    with_audio = {e["slug"] for e in eps}
    for d in sorted(DRAFTS.glob("*-w*"), reverse=True) if DRAFTS.exists() else []:
        bj = json.loads((d / "brief.json").read_text(encoding="utf-8"))
        drafts.append({"slug": d.name, "season": bj["season"], "mp": bj["mp"], "has_audio": d.name in with_audio,
                       "source": f"podcast/drafts/{d.name}/source.md", "prompt": (d / "prompt.txt").read_text(encoding="utf-8"),
                       "sha": hashlib.sha1((d / "source.md").read_bytes()).hexdigest()[:8]})
    league = (load("meta.json") or {}).get("league_name", "League")
    write_feed(eps, league)
    (DATA / "podcast.json").write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "feed": SITE_URL + "podcast/feed.xml", "drafts": drafts, "episodes": eps,
    }, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(force="--force" in a, mp=int(a[a.index("--mp") + 1]) if "--mp" in a else None)
