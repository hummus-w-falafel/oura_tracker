# h_tracker

A self-hosted health analytics platform for the [Oura Ring](https://ouraring.com/), built to be operated by an AI agent.

It pulls everything Oura's API exposes — sleep, readiness, activity, HR, HRV, SpO2, workouts, sessions, vO2 max, vascular age — into a local SQLite database, augments it with custom logging (meals with USDA-backed nutrition lookup, workouts with set/rep tracking, substances, journal entries), and serves it through a Flask dashboard with three views: a Solo Leveling–style RPG status page, a continuous timeline dashboard, and a correlation explorer.

The whole thing is designed to be driven by a coding agent. `CLAUDE.md` is tuned for Claude Code; `AGENTS.md` is the Codex-facing equivalent. The agent logs your meals, runs your queries, surfaces patterns. You don't write SQL — you have a conversation.

## What's in the box

- **Full Oura API sync** with incremental updates and idempotent upserts
- **SQLite database** with flat queryable columns + raw JSON for full fidelity
- **USDA-backed nutrition logging** — 21 nutrients per meal, no manual entry
- **Custom nutrition scoring** — sigmoid/gaussian curves over macros + micros (AHEI-2010 inspired)
- **Solo Leveling RPG layer** — VIT/STR/END/NUT/DIS stats, XP, levels, ranks (see `LEVELING.md`)
- **Correlation engine** — Pearson + normalized mutual information across ~30 daily features, with same-day and next-day lag
- **Agent-first design** — `CLAUDE.md` and `AGENTS.md` are operating prompts; every script is structured for programmatic use

## Setup

Prerequisites: Python 3.11+, an Oura developer app, a free USDA API key.

```bash
# 1. Clone
git clone <your-fork-url> h_tracker
cd h_tracker

# 2. Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your Oura client ID/secret and USDA key

# 4. Authenticate with Oura (one-time, opens a browser)
python3 auth.py

# 5. Initial sync (pulls all historical data)
python3 sync.py --full

# 6. Create your profile so the agent can personalize analysis
cp PROFILE.example.md PROFILE.md
# Fill in PROFILE.md with your details — gitignored, stays local

# 7. Run the dashboard
python3 dashboard.py
# → http://localhost:8000
```

## Daily use

Subsequent syncs are incremental:

```bash
python3 sync.py            # daily delta
python3 sync.py --status   # row counts per table
python3 check.py 7         # text snapshot of last 7 days
```

Logging meals, workouts, and substances is done through the agent — point it at this repo and it reads `CLAUDE.md`/`AGENTS.md` to learn the database schema, then uses the helpers in `nutrition.py` and `db.py` to log on your behalf.

## Architecture

```
  Oura API  ──►  oura_client.py  ──►  sync.py ────┐
                                                  │
  USDA API  ──►  nutrition.py  ───────────────────┤
                                                  │
  Agent     ──►  db.log_meal / log_substance  ────┤
                   log_workout_session            │
                                                  │
                                                  ▼
                                               health.db  (SQLite)
                                                  │
                                                  ▼
                                             dashboard.py  (Flask, uses leveling + nutrition scoring)
                                                  │
                                 ┌────────────────┼────────────────┐
                                 ▼                ▼                ▼
                                 /             /status       /correlations
                             (timeline)       (RPG stats)    (Pearson + MI)
```

See `CLAUDE.md` or `AGENTS.md` for the full database schema, sync strategy, and analytical patterns.

## Repository layout

| Path | Purpose |
|------|---------|
| `auth.py` | One-time Oura OAuth2 flow |
| `oura_client.py` | Oura API v2 client (all endpoints, auto-paginating) |
| `db.py` | Schema + upsert helpers |
| `sync.py` | Incremental + full sync |
| `check.py` | Text snapshot CLI |
| `nutrition.py` | USDA lookup + scoring engine |
| `leveling.py` | RPG stat/XP/level engine |
| `dashboard.py` | Flask app |
| `templates/` | Dashboard HTML (3 pages) |
| `scripts/` | One-shot maintenance scripts |
| `CLAUDE.md` | Claude Code operating prompt |
| `AGENTS.md` | Codex operating prompt |
| `LEVELING.md` | RPG system design spec |
| `PROFILE.example.md` | User profile template |

## License

Released into the public domain — see [UNLICENSE](UNLICENSE). Do whatever you want with it.
