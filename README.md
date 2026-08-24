# oura_tracker

A self-hosted health analytics platform for the [Oura Ring](https://ouraring.com/) and Withings scale data, built to be operated by an AI agent.

It pulls everything Oura's API exposes — sleep, readiness, activity, HR, HRV, SpO2, workouts, sessions, vO2 max, vascular age — plus Withings Body Comp measurements into a local SQLite database, augments it with custom logging (meals with USDA-backed or item-level nutrition, workouts with set/rep tracking, substances, travel, journal entries), and serves it through a Flask dashboard with timeline, RPG status, correlation, and advanced time-series views.

The whole thing is designed to be driven by a coding agent. `CLAUDE.md` is tuned for Claude Code; `AGENTS.md` is the Codex-facing equivalent. The agent logs your meals, runs your queries, surfaces patterns. If you have any questions or want to make any changes just ask the agent!

## What's in the box

- **Full Oura API sync** with incremental updates and idempotent upserts
- **Withings Body Comp sync and charting** for weight, fat, muscle, water, bone mass, and pulse wave velocity where available
- **SQLite database** with flat queryable columns + raw JSON for full fidelity
- **Structured nutrition logging** — meal totals plus optional per-item rows, with USDA-backed lookup when appropriate
- **Custom nutrition scoring** — sigmoid/gaussian curves over macros + micros (AHEI-2010 inspired)
- **Solo Leveling RPG layer** — VIT/STR/END/NUT/DIS stats, XP, levels, ranks (see `LEVELING.md`)
- **Correlation engine** — Pearson + normalized mutual information across ~30 daily features, with same-day and next-day lag
- **Advanced analytics** — stable baseline shifts, Sleep Regularity Index, 24-hour heart-rate cosinor, corrected VAR/Granger tests, and impulse responses
- **Agent-first design** — `CLAUDE.md` and `AGENTS.md` are operating prompts; every script is structured for programmatic use

## Setup

Prerequisites: Python 3.11+, an Oura developer app, a free USDA API key. Withings scale sync additionally needs a Withings developer app and an HTTPS OAuth callback URL.

```bash
# 1. Clone
git clone <your-fork-url> oura_tracker
cd oura_tracker

# 2. Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your Oura client ID/secret and USDA key

# 4. Authenticate with Oura (one-time, opens a browser)
python3 auth.py

# 5. Optional: authenticate Withings (one-time, opens a browser)
# Requires WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, and WITHINGS_REDIRECT_URI
python3 withings_auth.py

# 6. Initial sync (pulls all historical data)
python3 sync_all.py --full

# 7. Create your profile so the agent can personalize analysis
cp PROFILE.example.md PROFILE.md
# Fill in PROFILE.md with your details — gitignored, stays local

# 8. Run the dashboard
python3 dashboard.py
# → http://localhost:8000
```

## Daily use

Subsequent syncs are incremental:

```bash
python3 sync_all.py        # Oura + Withings deltas
python3 sync_all.py --status
python3 sync.py            # Oura only, for debugging
python3 withings_sync.py   # Withings only, for debugging
python3 check.py 7         # text snapshot of last 7 days
```

Logging meals, workouts, and substances is done through the agent — point it at this repo and it reads `CLAUDE.md`/`AGENTS.md` to learn the database schema, then uses the helpers in `nutrition.py` and `db.py` to log on your behalf.

## Architecture

```
  Oura API  ──►  oura_client.py  ──►  sync.py ───────────┐
                                                         │
  USDA API  ──►  nutrition.py  ──────────────────────────┤
                                                         │
  Withings  ──►  withings_client.py ─► withings_sync.py ─┤
                                                         │
  Agent     ──►  db.py logging helpers  ─────────────────┤
                  meals, substances, workouts            │
                                                         │
                                                         │
                                                         ▼
                                                     health.db  (SQLite)
                                                         │
                                                         ▼
                                                    dashboard.py  (Flask, uses leveling + nutrition scoring)
                                                         │
                                  ┌──────────────┬──────────────┬──────────────┐
                                  ▼              ▼              ▼              ▼
                                  /           /status     /correlations   /analytics
                              (timeline)     (RPG stats)  (Pearson + MI)  (time series)
```

See `CLAUDE.md` or `AGENTS.md` for the full database schema, sync strategy, and analytical patterns.

## Repository layout

| Path | Purpose |
|------|---------|
| `auth.py` | One-time Oura OAuth2 flow |
| `oura_client.py` | Oura API v2 client (all endpoints, auto-paginating) |
| `db.py` | Schema + upsert helpers |
| `sync_all.py` | Combined Oura + Withings sync runner |
| `sync.py` | Incremental + full Oura sync |
| `withings_auth.py` | One-time Withings OAuth2 flow |
| `withings_client.py` | Withings Public API client |
| `withings_sync.py` | Incremental + full Withings scale sync |
| `check.py` | Text snapshot CLI |
| `nutrition.py` | USDA lookup + scoring engine |
| `advanced_analytics.py` | Change points, SRI, cosinor, VAR/Granger, and impulse responses |
| `leveling.py` | RPG stat/XP/level engine |
| `dashboard.py` | Flask app |
| `templates/` | Dashboard HTML, including the dedicated advanced analytics page |
| `scripts/` | One-shot maintenance scripts |
| `CLAUDE.md` | Claude Code operating prompt |
| `AGENTS.md` | Codex operating prompt |
| `LEVELING.md` | RPG system design spec |
| `PROFILE.example.md` | User profile template |

## License

Released into the public domain — see [UNLICENSE](UNLICENSE). Do whatever you want with it.
