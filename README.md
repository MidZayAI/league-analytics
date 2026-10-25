# League Analytics

Advanced analytics for an ESPN H2H Points fantasy basketball league with a weekly games cap:
- live matchup win probabilities
- luck (all-play, schedule swap, bad beats, upsets)
- a Coaching Rating (actual vs best possible lineup under the cap)
- games-cap analytics
- all-time franchise history

A Python pipeline pulls ESPN data and writes JSON to `site/data/`. `site/` is a static page (Tailwind + Alpine.js + ECharts, no build step). GitHub Actions refreshes the data on a schedule and publishes `site/` to GitHub Pages.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env              # fill in LEAGUE_ID, SEASON, ESPN_S2, SWID
python pipeline/build.py --fetch  # pull ESPN data + rebuild everything
python -m http.server 8000 -d site
```

Then open http://localhost:8000.

## Hosting (GitHub Pages + auto-refresh)

1. Push this folder to a **public** GitHub repo. League data (team names, scores) is visible to anyone with the URL; your ESPN cookies are not.
2. Go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.
3. Go to **Settings → Secrets and variables → Actions** and add these repository secrets:
   - `LEAGUE_ID`
   - `ESPN_S2`
   - `SWID` (including the `{ }`)
4. Go to **Actions → Refresh league data → Run workflow** for the first run.
5. After that it runs:
   - every 30 min from about 7pm to 2:30am ET
   - four times during the day

The site is at `https://<user>.github.io/<repo>/`.

When the ESPN cookies expire (roughly yearly), copy fresh values into the secrets.

**New season:** add a repository *variable* `SEASON` (the year the NBA season ends, e.g. `2028`) and update `.env`. The previous season becomes history automatically.

## Pipeline

| Step | File |
|---|---|
| ESPN client (cookie auth) | `pipeline/espn_client.py` |
| Fetch + cache raw data | `pipeline/fetch.py` |
| Tidy tables + weekly cap: `round(38/7 × NBA game days)` | `pipeline/normalize.py` |
| Coaching Rating (integer program per team-week) | `pipeline/coaching.py` |
| Luck metrics | `pipeline/luck.py` |
| Franchise history, H2H, record book | `pipeline/franchises.py` |
| Player projections | `pipeline/projections.py` |
| Cap-aware Monte Carlo | `pipeline/simulate.py` |
| Live forecast + win-prob snapshots | `pipeline/live.py` |
| Predictor backtest / tuning (run occasionally, ~3 min) | `pipeline/backtest.py` |
| Orchestrates everything → `site/data/*.json` | `pipeline/build.py` |

Owner display names live in `config/owners.json`. Edit them to real names or nicknames.
