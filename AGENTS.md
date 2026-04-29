# AGENTS.md

Codex operating notes for this repository.

## Project Summary

`h_tracker` is a self-hosted health analytics app for Oura Ring data plus manual logs. It syncs Oura API data into local SQLite, adds custom logs for meals, substances, sex, journal notes, and workout sets, then serves a Flask dashboard with:

- `/` continuous timeline dashboard
- `/status` Solo Leveling-style health RPG page
- `/correlations` Pearson and normalized mutual information explorer

This repo is intentionally agent-operated. Most user-facing work should be done by reading the data, running targeted queries or helper functions, and reporting concrete findings.

## Repository Map

| Path | Purpose |
| --- | --- |
| `auth.py` | One-time Oura OAuth2 flow; writes `tokens.json`. |
| `oura_client.py` | Oura API v2 client with pagination and graceful unavailable-endpoint handling. |
| `db.py` | SQLite schema, connection helper, upserts, and manual logging helpers. |
| `sync.py` | Incremental/full Oura sync and sync status. |
| `check.py` | Text snapshot CLI for recent health data. |
| `nutrition.py` | USDA FoodData Central lookup, meal logging, nutrition scoring. |
| `time_utils.py` | Shared timestamp parsing, timezone-aware ISO normalization, and local day derivation. |
| `profile_targets.py` | Loads numeric target overrides from a fenced YAML block in `PROFILE.md`. |
| `leveling.py` | VIT/STR/END/NUT/DIS stat and XP engine. |
| `dashboard.py` | Flask app and JSON APIs for the dashboard. |
| `templates/` | Frontend HTML for dashboard, status, and correlations pages. |
| `scripts/backfill_micros.py` | One-off USDA micronutrient backfill script. |
| `scripts/dump_schema.py` | Prints SQLite table columns/types for schema-doc drift checks. |
| `static/base.css` | Shared theme primitives used by the dashboard templates. |
| `LEVELING.md` | Design spec for the RPG stat system. |
| `CLAUDE.md` | Original agent prompt with detailed schema and analytical examples. |
| `PROFILE.example.md` | Template for local user profile. |
| `tests/test_smoke.py` | Standard-library smoke tests for core agent-operated flows. |

## Local Data and Privacy

Treat the following as private local state:

- `.env`
- `tokens.json`
- `PROFILE.md`
- `health.db`, WAL/SHM files, and backups
- `sync.log`

These are gitignored. Do not print secrets or token contents. Read `PROFILE.md` only when it is relevant to personalization, health analysis, workout logging, or nutrition targets. It may contain sensitive demographic, medical, substance, and goal information.

## Setup and Commands

Use Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Common commands:

```bash
python3 auth.py              # one-time Oura auth, opens browser
python3 sync.py              # incremental sync
python3 sync.py --full       # full sync from RING_START_DATE
python3 sync.py --status     # sync state and row counts
python3 check.py 7           # recent text snapshot
python3 dashboard.py         # serves http://localhost:8000
python3 leveling.py          # print leveling snapshot JSON
```

For code changes, use the smallest meaningful verification:

- Run `python3 -m unittest discover -s tests`.
- Run `PYTHONPYCACHEPREFIX=/tmp/h_tracker_pycache python3 -m py_compile` on edited Python files.
- Run `python3 scripts/dump_schema.py` when schema docs may have drifted.
- Run feature-specific CLIs where applicable, such as `python3 sync.py --status`, `python3 check.py 7`, or `python3 leveling.py`.
- For dashboard changes, start `python3 dashboard.py` and check the changed endpoint/page.

Network-dependent commands include Oura sync/auth and USDA lookups. They require valid credentials and may fail in restricted environments.

## Database

The database is `health.db`, opened through `db.get_conn()`. `init_db()` is idempotent and sets WAL mode plus foreign keys.

Main tables:

- `sync_state`
- `daily_sleep`, `daily_readiness`, `daily_activity`, `daily_stress`, `daily_spo2`
- `daily_cardiovascular_age`, `daily_resilience`, `vo2_max`
- `sleep_periods`
- `heartrate`
- `workouts`, `sessions`, `sleep_time`
- `meals`, `substances`, `sex`, `journal`, `workout_sets`
- `leveling_daily_cache`

Important schema notes:

- Oura timestamps are stored as UTC strings. Manual logs should use timezone-aware ISO strings. Display and health-day grouping use `TIMEZONE` from `.env`, defaulting to `America/Toronto`.
- Use `time_utils.local_day()` for day grouping. Do not derive health days with `logged_at[:10]`; UTC timestamps near midnight can belong to the previous local day.
- `sleep_periods.type='long_sleep'` identifies main sleep. Short `type='sleep'` records are naps/rest detections.
- `sleep_phase_5_min` is a digit string: `1=Deep`, `2=Light`, `3=REM`, `4=Awake`.
- `heartrate` is high volume and should be downsampled for dashboard/API responses.
- Manual meals live in `meals`; Oura meal data is not available through the API.
- Substance dose tracking uses `amount_value`, `amount_unit`, and `potency_pct`; `amount_deprecated` is legacy.
- `workout_sets.weight_per_hand` is `0=single`, `1=each hand`.

### Detailed Schema Reference

`sync_state` tracks the latest successful sync by endpoint:

`endpoint TEXT PK | last_synced_at TEXT`

`daily_sleep` has one row per day:

`day PK | score | contrib_deep_sleep | contrib_efficiency | contrib_latency | contrib_rem_sleep | contrib_restfulness | contrib_timing | contrib_total_sleep | raw_json | synced_at`

`daily_readiness` has one row per day:

`day PK | score | temperature_deviation | temperature_trend_deviation | contrib_activity_balance | contrib_body_temperature | contrib_hrv_balance | contrib_previous_day_activity | contrib_previous_night | contrib_recovery_index | contrib_resting_heart_rate | contrib_sleep_balance | contrib_sleep_regularity | raw_json | synced_at`

`daily_activity` has one row per day:

`day PK | score | active_calories | total_calories | steps | average_met_minutes | high_activity_time | medium_activity_time | low_activity_time | sedentary_time | resting_time | target_calories | raw_json | synced_at`

`daily_stress` has one row per day:

`day PK | stress_high | recovery_high | day_summary | raw_json | synced_at`

This may be mostly zero/empty until Oura stress tracking is active.

`daily_spo2` has one row per day:

`day PK | spo2_average | breathing_disturbance_index | raw_json | synced_at`

Oura returns SpO2 as `spo2_percentage.average`; `db.upsert_daily_spo2()` flattens it.

`daily_cardiovascular_age` has one row per day when available:

`day PK | vascular_age | raw_json | synced_at`

`daily_resilience` is supported by the schema but may return no data without the right Oura scope:

`day PK | level | contrib_sleep_recovery | contrib_daytime_recovery | contrib_stress | raw_json | synced_at`

`vo2_max` stores Oura VO2 max estimates:

`id PK | day | timestamp | vo2_max | raw_json | synced_at`

`sleep_periods` stores detailed detected sleep events, including naps:

`id PK | day | type | period | bedtime_start | bedtime_end | time_in_bed | total_sleep_duration | efficiency | latency | deep_sleep_duration | light_sleep_duration | rem_sleep_duration | awake_time | restless_periods | average_hrv | average_heart_rate | lowest_heart_rate | average_breath | sleep_phase_30_sec | sleep_phase_5_min | hr_series_json | hrv_series_json | raw_json | synced_at`

Use `type='long_sleep'` for main sleep. `hr_series_json` and `hrv_series_json` store Oura's intra-night time series payloads as JSON.

`heartrate` stores high-resolution readings:

`timestamp PK | bpm | source`

Sources are typically `awake`, `rest`, `sleep`, `workout`, or `live`.

`workouts` stores Oura workout records:

`id PK | day | activity | intensity | source | start_datetime | end_datetime | duration | calories | distance | label | raw_json | synced_at`

Manual workouts can take hours to appear from the Oura API after being logged in the app.

`sessions` stores Oura app sessions:

`id PK | day | type | start_datetime | end_datetime | mood | raw_json | synced_at`

`sleep_time` stores bedtime recommendations:

`id PK | day | recommendation | status | optimal_bedtime_start_offset | optimal_bedtime_end_offset | raw_json | synced_at`

`meals` stores custom meal logs:

`id PK | logged_at | meal_type | description | calories | protein_g | carbs_g | fat_g | sat_fat_g | sugar_g | fiber_g | omega3_g | vitamin_d_mcg | b12_mcg | magnesium_mg | zinc_mg | iron_mg | potassium_mg | sodium_mg | vitamin_c_mg | vitamin_e_mg | vitamin_b6_mg | folate_mcg | notes | created_at`

`substances` stores custom substance logs:

`id PK | logged_at | substance | amount_deprecated | notes | created_at | amount_value | amount_unit | potency_pct`

`sex` stores custom sex or masturbation logs:

`id PK | logged_at | type | duration_min | notes | created_at`

Valid `type` values currently used by the helper are `sex` and `goon`.

`journal` stores free-text notes and detailed nutrition breakdowns:

`id PK | day | category | note | created_at`

Categories commonly include `sleep`, `training`, `nutrition`, `general`, and `mood`.

`workout_sets` stores strength-training set details:

`id PK | workout_day | exercise | set_number | reps | weight_lbs | weight_per_hand | notes | created_at | workout_id FK->workouts(id)`

`leveling_daily_cache` caches daily RPG stat computations:

`day PK | vit_score | str_score | end_score | nut_score | dis_score | daily_xp | decay | computed_at`

### Oura API Sync Notes

`sync.py` syncs from `RING_START_DATE = "2026-03-22"`. Incremental sync uses overlap windows because Oura data can arrive late:

- Date endpoints fetch from `last_synced - 1 day`.
- `sleep_periods` uses an extra lookback so overnight sleeps are not missed.
- `heartrate` uses datetime precision and a 48-hour overlap.
- All normal writes are upserts or insert-ignore, so syncs are intended to be safe to re-run.
- `daily_resilience` and `ring_configuration` may be unavailable depending on scopes.
- `vO2_max` endpoint casing is case-sensitive.

Endpoint availability as implemented:

| Endpoint | Status in code | Notes |
| --- | --- | --- |
| `daily_sleep`, `daily_readiness`, `daily_activity`, `daily_stress`, `daily_spo2` | Supported | Daily summary tables. |
| `sleep` | Supported | Detailed sleep periods and stage/HRV series. |
| `heartrate` | Supported | Datetime params, high-volume. |
| `workout`, `session`, `sleep_time` | Supported | Oura workout/session/bedtime recommendation data. |
| `daily_cardiovascular_age` | Supported | Vascular age. |
| `daily_resilience` | Supported, may be blocked | Depends on stress/resilience access. |
| `vO2_max` | Supported | Case-sensitive endpoint. |
| `ring_configuration` | Client method only | May require extra scope. |

## Health Analysis Workflow

When the user asks a health, training, nutrition, sleep, or coaching question:

1. Start from data, not memory. Usually run `python3 check.py 7`; use 14 or 30 days for trend questions.
2. If freshness matters, inspect `python3 sync.py --status`. Note stale data rather than silently assuming it is current.
3. Use SQL through `sqlite3` or `db.get_conn()` for targeted questions.
4. Connect interpretation to the user's actual metrics and, when needed, current evidence.
5. Be direct, specific, and non-moralizing. Discuss substances, sleep, food, and training in terms of observed effects and tradeoffs.

Common query patterns are documented in `CLAUDE.md`. Reuse those before inventing new SQL.

### Common SQL Patterns

Bedtime vs next-day readiness:

```sql
SELECT sp.day,
       TIME(sp.bedtime_start) AS bedtime,
       (JULIANDAY(sp.bedtime_start) - JULIANDAY(sp.day || ' 00:00:00')) * 24 AS bedtime_hour,
       sp.average_hrv,
       sp.lowest_heart_rate,
       r.score AS readiness,
       s.score AS sleep_score
FROM sleep_periods sp
JOIN daily_readiness r ON r.day = sp.day
JOIN daily_sleep s ON s.day = sp.day
WHERE sp.type = 'long_sleep'
ORDER BY sp.day;
```

Sleep stage percentages:

```sql
SELECT day,
       ROUND(deep_sleep_duration * 100.0 / total_sleep_duration, 1) AS deep_pct,
       ROUND(rem_sleep_duration * 100.0 / total_sleep_duration, 1) AS rem_pct,
       ROUND(light_sleep_duration * 100.0 / total_sleep_duration, 1) AS light_pct
FROM sleep_periods
WHERE type = 'long_sleep'
ORDER BY day;
```

Rolling 7-day HRV trend:

```sql
SELECT day,
       average_hrv,
       AVG(average_hrv) OVER (ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS hrv_7d_avg
FROM sleep_periods
WHERE type = 'long_sleep'
ORDER BY day;
```

Daily nutrition totals:

```sql
SELECT substr(logged_at,1,10) AS day,
       SUM(calories) AS calories,
       SUM(protein_g) AS protein_g,
       SUM(fiber_g) AS fiber_g,
       SUM(sodium_mg) AS sodium_mg,
       SUM(potassium_mg) AS potassium_mg
FROM meals
GROUP BY substr(logged_at,1,10)
ORDER BY day;
```

HR during workout windows:

```sql
SELECT w.day,
       w.activity,
       w.duration,
       AVG(h.bpm) AS avg_hr,
       MAX(h.bpm) AS peak_hr,
       MIN(h.bpm) AS min_hr
FROM workouts w
JOIN heartrate h ON h.timestamp BETWEEN w.start_datetime AND w.end_datetime
GROUP BY w.id
ORDER BY w.start_datetime;
```

Substance timing vs next sleep:

```sql
SELECT sub.substance,
       sub.logged_at,
       sub.amount_value,
       sub.amount_unit,
       sub.potency_pct,
       sp.day AS sleep_day,
       sp.average_hrv,
       sp.rem_sleep_duration,
       sp.total_sleep_duration,
       sp.efficiency,
       ds.score AS sleep_score
FROM substances sub
JOIN sleep_periods sp
  ON sp.day = DATE(sub.logged_at, '+1 day')
 AND sp.type = 'long_sleep'
LEFT JOIN daily_sleep ds ON ds.day = sp.day
ORDER BY sub.logged_at;
```

Progression for one exercise:

```sql
SELECT workout_day,
       set_number,
       reps,
       weight_lbs,
       CASE weight_per_hand WHEN 1 THEN 'each hand' ELSE 'single' END AS style
FROM workout_sets
WHERE exercise = 'double KB press'
ORDER BY workout_day DESC, set_number;
```

Weekly hard-set volume:

```sql
SELECT strftime('%Y-%W', workout_day) AS week,
       exercise,
       COUNT(*) AS hard_sets
FROM workout_sets
GROUP BY week, exercise
ORDER BY week DESC, exercise;
```

Intra-night HRV curve:

```python
import json
from db import get_conn

with get_conn() as conn:
    row = conn.execute(
        "SELECT hrv_series_json FROM sleep_periods WHERE day = ? AND type = 'long_sleep'",
        ("2026-04-25",),
    ).fetchone()
    series = json.loads(row["hrv_series_json"])
    for item in series.get("items", []):
        print(item)
```

### Derived Metrics Worth Computing

These are not all persisted but are useful for analysis and dashboard work:

| Metric | Derivation | Why it matters |
| --- | --- | --- |
| Bedtime consistency | Stddev of bedtime hour over 7/14/30 days | Circadian regularity. |
| Sleep efficiency trend | Rolling average of `sleep_periods.efficiency` | Detects worsening sleep quality. |
| Training load | Duration or calories over rolling 7 days, optionally intensity-weighted | Helps separate overload from undertraining. |
| Recovery ratio | HRV divided by resting HR | Combines autonomic signals. |
| Protein per kg | Daily protein divided by body weight from `PROFILE.md` | Better than a fixed protein target. |
| Fasting window | Time between prior day's last meal and current day's first meal | Evaluates eating-window consistency. |
| HR recovery | BPM drop after workout end | Cardiorespiratory fitness proxy. |
| Micronutrient gap score | Nutrients below target across 7-14 days | Distinguishes chronic gaps from one-offs. |

### Anomaly Flags

When presenting recent data, explicitly flag:

- HRV drop greater than 30% from recent baseline.
- Resting HR increase greater than 10% from recent baseline.
- Body temperature deviation above `+0.3 C`.
- SpO2 below 94%.
- Sleep efficiency below 75%.
- Total sleep below 6 hours.
- Bedtime after 1:00 AM local time.
- Zero meals logged on a day where nutrition completeness matters.
- Training on readiness below 65.
- Very high sedentary time, especially if repeated.

## Manual Logging Patterns

Prefer existing helpers over raw inserts.

Meals have two supported helper paths. Use the USDA path when the user gives foods and portions that should be looked up; use the raw DB path only when nutrient values are already known.

Actual USDA helper signature from `nutrition.py`:

```python
log_meal_with_nutrition(
    description: str,
    items: list,
    meal_type: str = "dinner",
    logged_at: str = None,
    notes: str = None,
)
```

`items` is consumed by `lookup_multi()` and must be a list of tuples:

- `(food_query: str, serving_grams: number)`
- `(food_query: str, serving_grams: number, fdc_id: int)` when pinning a USDA entry

The helper performs USDA lookup, maps totals into the `meals` nutrient columns, and writes a full JSON nutrient breakdown into `journal` with `category="nutrition"`.
The journal day is derived with `time_utils.local_day(logged_at)`, not by slicing the timestamp.

```python
from nutrition import log_meal_with_nutrition

log_meal_with_nutrition(
    "description of meal",
    [
        ("food search query", 100),
        ("another food search query", 50, 123456),
    ],
    meal_type="dinner",
    logged_at="YYYY-MM-DDTHH:MM:SS-04:00",
    notes="optional context",
)
```

Actual raw DB helper signature from `db.py`:

```python
log_meal(
    day: str,
    meal_type: str,
    description: str,
    calories: int = None,
    protein_g: float = None,
    carbs_g: float = None,
    fat_g: float = None,
    sat_fat_g: float = None,
    sugar_g: float = None,
    fiber_g: float = None,
    omega3_g: float = None,
    vitamin_d_mcg: float = None,
    b12_mcg: float = None,
    magnesium_mg: float = None,
    zinc_mg: float = None,
    iron_mg: float = None,
    potassium_mg: float = None,
    sodium_mg: float = None,
    vitamin_c_mg: float = None,
    vitamin_e_mg: float = None,
    vitamin_b6_mg: float = None,
    folate_mcg: float = None,
    notes: str = None,
    logged_at: str = None,
)
```

`log_meal()` stores `logged_at`; its `day` parameter is currently only used for the console message. Still pass the matching local date for clarity.

Workout sets:

```python
from db import get_conn, log_workout_session, upsert_workout

workout_id = "manual-kettlebell-2026-04-25-1800"
record = {
    "id": workout_id,
    "day": "2026-04-25",
    "activity": "kettlebell",
    "intensity": "medium",
    "source": "manual",
    "start_datetime": "2026-04-25T18:00:00-04:00",
    "end_datetime": "2026-04-25T18:25:00-04:00",
    "calories": None,
    "distance": None,
    "label": "KB Session",
}
with get_conn() as conn:
    upsert_workout(conn, record)

log_workout_session(workout_id, "2026-04-25", [
    ("double KB press", 1, 7, 25.0),
    ("double KB press", 2, 6, 25.0),
])
```

For user-reported workouts, create or reuse a manual parent row in `workouts` with `source="manual"` and the appropriate `activity` value, such as `kettlebell`, `rowing`, or `strength_training`. Then link related detail rows, such as `workout_sets`, via `workout_id`. Use a stable ID such as `manual-activity-YYYY-MM-DD-HHMM` when creating the parent manually. These sessions are not expected to be auto-detected by Oura, and strength sessions should not be left as unlinked `workout_sets` rows. Use canonical exercise names from `PROFILE.md` when available so progression queries do not split history across aliases.

Substances:

```python
from db import log_substance

log_substance(
    logged_at="2026-04-25T22:30:00-04:00",
    substance="weed",
    amount_value=0.25,
    amount_unit="g",
    potency_pct=30,
    notes="before bed",
)
```

Sex or masturbation:

```python
from db import log_sex

log_sex(
    logged_at="2026-04-25T23:15:00-04:00",
    type="sex",
    duration_min=25,
    notes=None,
)
```

Journal:

```python
from db import log_journal

log_journal("2026-04-25", "Felt unusually tired after dinner.", category="general")
```

When a user gives a relative time like "today" or "last night", resolve it using the local timezone configured by `TIMEZONE`, usually `America/Toronto`. Prefer storing timezone-aware ISO timestamps.

## Dashboard Development

Backend changes usually go in `dashboard.py`; frontend changes usually go in `templates/*.html`.

Follow existing patterns:

- Flask routes return JSON for chart data.
- ApexCharts renders client-side charts.
- Keep high-volume data downsampled before sending it to the browser.
- Preserve the dark cyberpunk visual language already present in the templates.
- Existing palette conventions include cyan/green/yellow/red/purple accents, dark backgrounds, and angular corners.

For new dashboard data, add a focused API route or extend the nearest existing route. Keep tooltips dense and useful: show value, units, target/context, and comparison where relevant.

## Leveling System

The RPG layer is implemented in `leveling.py` and documented in `LEVELING.md`.

Stats:

- `VIT`: sleep, readiness, HRV, resting HR
- `STR`: workout progression, set volume, post-workout recovery
- `END`: steps, active calories, resting HR trend, VO2 max
- `NUT`: nutrition score, protein distribution, training-day calorie adequacy
- `DIS`: bedtime consistency, training adherence, fasting window

`compute_snapshot()` is the main API used by `/api/leveling`. It caches older daily computations in `leveling_daily_cache`; recent days are recomputed.

XP rules:

- Each active stat contributes `stat_score / 5` XP per day.
- Max XP scales with the number of stats that have data.
- Daily decay is 50% of max possible active XP.
- Total XP can regress; levels are recalculated from total XP.
- Level curve: `XP_required(level) = 20 * level * level`.

Rank rules:

| Rank | Level requirement | Minimum active stat |
| --- | --- | --- |
| E | 1 | 0 |
| D | 6 | 30 |
| C | 11 | 45 |
| B | 21 | 55 |
| A | 31 | 65 |
| S | 41 | 75 |

When changing leveling logic, update `LEVELING.md` and verify with `python3 leveling.py`.

## Domain Model

### Sleep

Ask data-backed questions:

- Is sleep quality improving or degrading? Use 7-day rolling averages of sleep score, HRV, efficiency, and lowest HR.
- What predicts a good night? Compare bedtime, last meal, substances, activity, and training against sleep score and HRV.
- Is deep sleep adequate? Track deep sleep percent and duration from `sleep_periods`.
- Is REM suppressed? Compare REM percent/duration against alcohol, cannabis, late meals, and short sleep.
- Is the schedule stable? Compute bedtime and wake-time standard deviation.
- What does intra-night HRV do? Parse `hrv_series_json`; rising overnight is generally more favorable than flat/declining.

Default adult reference ranges, to be adjusted by `PROFILE.md` and personal baseline:

- HRV: personal baseline matters most; rough defaults are 50-70 ms solid, 70+ excellent, below 40 worth flagging.
- Lowest sleeping HR: 45-55 bpm excellent for many fit adults; persistent >65 is worth flagging.
- Deep sleep: roughly 15-20% of total sleep.
- REM: roughly 20-25% of total sleep.
- Total sleep: 7-9 hours.
- Efficiency: >85% good, <80% worth flagging.
- SpO2: 95-100% normal, <94% worth flagging.

### Nutrition

Use `nutrition.compute_nutrition_score()` for daily scoring. It combines macro adequacy, limit penalties, sodium:potassium ratio, and micronutrient coverage. Numeric targets come from `profile_targets.get_targets()`, which reads a fenced YAML block in `PROFILE.md` and falls back to defaults.

Default targets:

| Nutrient | Target or limit |
| --- | --- |
| Calories | 2300 kcal |
| Protein | 120 g |
| Fiber | 30 g |
| Saturated fat | <22 g |
| Sugar | <30 g |
| Sodium | judged partly through Na:K ratio |
| Omega-3 | 2 g |
| Magnesium | 420 mg |
| Potassium | 3400 mg |
| Vitamin D | 15 mcg |
| Iron | 8 mg |
| B12 | 2.4 mcg |
| Zinc | 11 mg |
| Vitamin C | 90 mg |
| Vitamin E | 15 mg |
| Vitamin B6 | 1.3 mg |
| Folate | 400 mcg |

`PROFILE.md` may override these with:

```yaml
targets:
  calories: 2300
  protein_g: 120
  training_sessions_per_week: 3
```

Keep machine-readable targets in that fenced block so `PROFILE.md` remains the single profile source of truth.

When analyzing nutrition:

- Prefer daily/weekly aggregates over single-meal judgments.
- Distinguish missing USDA micronutrient data from true low intake.
- Adjust protein and calories using body weight, goals, and training status from `PROFILE.md`.
- For recurring meals, consider pinning USDA `fdc_id` values if search picks poor matches.

### Activity and Training

Ask:

- Is training frequency consistent? Count workouts or distinct `workout_sets.workout_day` per week.
- Is progressive overload occurring? Compare max reps x weight by exercise over recent and prior windows.
- Is recovery adequate? Compare readiness and HRV after training days.
- What is the HR response during workouts? Join `workouts` to `heartrate` by workout window.
- Is Zone 2 cardio present? Look for sustained heart-rate periods, typically around 120-140 bpm depending on profile.
- Is sedentary time high? Use `daily_activity.sedentary_time`.

Readiness-based default training interpretation:

- 80+: full session is usually reasonable.
- 65-79: consider lower volume or intensity.
- Below 65: favor active recovery unless the user's context argues otherwise.

### Cross-Domain Analyses

High-value analysis usually connects tables:

- Substance timing -> REM, HRV, resting HR, sleep score.
- Last meal timing -> sleep efficiency, lowest HR, next-day readiness.
- Training load -> next-day readiness and HRV.
- Nutrition quality -> 3-5 day HRV/readiness trends.
- Bedtime consistency -> weekly readiness.
- Steps/activity -> same-night sleep quality.
- Sex/masturbation timing -> sleep latency, HRV, readiness, if enough data exists.

Be cautious with small samples. Report `n`, effect size, direction, and whether the relationship is stable or anecdotal.

## Coding Guidelines

- Keep changes small and consistent with the current single-file/module style.
- Use `rg` for searching.
- Prefer structured helpers and SQL parameters over ad hoc string manipulation.
- Do not commit secrets, local database files, profiles, logs, backups, or generated snapshots.
- Do not rewrite `CLAUDE.md` unless asked; this file is the Codex-facing guide.
- Be careful with schema changes: make migrations idempotent in `init_db()` and preserve existing local data.
- For Oura API changes, check endpoint casing and parameter style; `heartrate` uses datetime params, most others use date params, and `vO2_max` is case-sensitive.

## Research Methodology

Use current sources when the user asks a research question, asks for evidence, or when the answer depends on recent scientific or medical information.

Preferred evidence hierarchy:

- Meta-analyses and systematic reviews.
- Randomized controlled trials.
- Mechanistic or tightly controlled studies.
- Observational studies.
- Expert opinion only as context.

When using research:

- State the population studied and whether it maps to this user.
- Explain the mechanism where useful.
- Separate strong evidence from plausible speculation.
- Connect literature back to this user's actual data.
- Do not cite unverified claims.

## Analytics Roadmap

Existing:

- Pearson correlation matrix in `/api/correlations`.
- Normalized mutual information matrix in `/api/correlations`.
- Pearson p-value matrix in `/api/correlations` as `pvalues`.
- Pairwise sample sizes in `/api/correlations` as `counts`; the UI greys out cells with `n < 14`.
- Same-day and next-day lag option through `lag` query parameter.
- Interactive heatmap and scatter plot frontend.

Future directions described by the original project:

- At ~30+ days: change point detection, sleep regularity index, circadian rhythm analysis.
- At ~60+ days: VAR and Granger causality for lagged prediction.
- At ~90+ days: causal-impact style intervention analysis.
- At 6+ months: regularized multivariable causal discovery and richer recurrence/coupling analyses.

If implementing these, keep them Python-side first, expose narrow JSON routes, and only then add dashboard UI.

## Communication Style for User-Facing Analysis

- Lead with the numbers.
- Separate observation from interpretation.
- Give concrete next actions only when the data supports them.
- State uncertainty and data gaps plainly.
- Do not offer generic health advice when a query can answer the question.
- If medical, legal, or financial stakes are high, advise professional confirmation and support claims with current sources.
