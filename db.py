"""
Database schema and upsert helpers.
SQLite with flat columns for all queryable fields + raw_json for full fidelity.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from time_utils import ensure_tz, event_local_day, local_day, local_tz, now_local

LOCAL_TZ = local_tz()


def get_db_path() -> Path:
    return Path(os.getenv("HEALTH_DB_PATH", Path(__file__).parent / "health.db"))


DB_PATH = get_db_path()


@contextmanager
def get_conn():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        -- ── Sync state ──────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS sync_state (
            endpoint      TEXT PRIMARY KEY,
            last_synced_at TEXT  -- ISO 8601, UTC
        );

        -- ── Daily summaries ─────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS daily_sleep (
            day             TEXT PRIMARY KEY,
            score           INTEGER,
            contrib_deep_sleep    INTEGER,
            contrib_efficiency    INTEGER,
            contrib_latency       INTEGER,
            contrib_rem_sleep     INTEGER,
            contrib_restfulness   INTEGER,
            contrib_timing        INTEGER,
            contrib_total_sleep   INTEGER,
            raw_json        TEXT,
            synced_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_readiness (
            day                         TEXT PRIMARY KEY,
            score                       INTEGER,
            temperature_deviation       REAL,
            temperature_trend_deviation REAL,
            contrib_activity_balance    INTEGER,
            contrib_body_temperature    INTEGER,
            contrib_hrv_balance         INTEGER,
            contrib_previous_day_activity INTEGER,
            contrib_previous_night      INTEGER,
            contrib_recovery_index      INTEGER,
            contrib_resting_heart_rate  INTEGER,
            contrib_sleep_balance       INTEGER,
            contrib_sleep_regularity    INTEGER,
            raw_json                    TEXT,
            synced_at                   TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_activity (
            day                 TEXT PRIMARY KEY,
            score               INTEGER,
            active_calories     INTEGER,
            total_calories      INTEGER,
            steps               INTEGER,
            average_met_minutes REAL,
            high_activity_time  INTEGER,
            medium_activity_time INTEGER,
            low_activity_time   INTEGER,
            sedentary_time      INTEGER,
            resting_time        INTEGER,
            target_calories     INTEGER,
            raw_json            TEXT,
            synced_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_stress (
            day             TEXT PRIMARY KEY,
            stress_high     INTEGER,
            recovery_high   INTEGER,
            day_summary     TEXT,
            raw_json        TEXT,
            synced_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_spo2 (
            day                         TEXT PRIMARY KEY,
            spo2_average                REAL,
            breathing_disturbance_index INTEGER,
            raw_json                    TEXT,
            synced_at                   TEXT
        );

        -- ── Cardiovascular age ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS daily_cardiovascular_age (
            day             TEXT PRIMARY KEY,
            vascular_age    INTEGER,
            raw_json        TEXT,
            synced_at       TEXT
        );

        -- ── Resilience ────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS daily_resilience (
            day                     TEXT PRIMARY KEY,
            level                   TEXT,
            contrib_sleep_recovery  INTEGER,
            contrib_daytime_recovery INTEGER,
            contrib_stress          INTEGER,
            raw_json                TEXT,
            synced_at               TEXT
        );

        -- ── VO2 max ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS vo2_max (
            id              TEXT PRIMARY KEY,
            day             TEXT,
            timestamp       TEXT,
            vo2_max         REAL,
            raw_json        TEXT,
            synced_at       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vo2_max_day ON vo2_max(day);

        -- ── Detailed sleep periods ──────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS sleep_periods (
            id                    TEXT PRIMARY KEY,
            day                   TEXT,
            type                  TEXT,
            period                INTEGER,
            bedtime_start         TEXT,
            bedtime_end           TEXT,
            time_in_bed           INTEGER,
            total_sleep_duration  INTEGER,
            efficiency            INTEGER,
            latency               INTEGER,
            deep_sleep_duration   INTEGER,
            light_sleep_duration  INTEGER,
            rem_sleep_duration    INTEGER,
            awake_time            INTEGER,
            restless_periods      INTEGER,
            average_hrv           INTEGER,
            average_heart_rate    REAL,
            lowest_heart_rate     INTEGER,
            average_breath        REAL,
            sleep_phase_30_sec    TEXT,
            sleep_phase_5_min     TEXT,
            hr_series_json        TEXT,
            hrv_series_json       TEXT,
            raw_json              TEXT,
            synced_at             TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sleep_periods_day ON sleep_periods(day);
        CREATE INDEX IF NOT EXISTS idx_sleep_periods_type ON sleep_periods(type);

        -- ── Heart rate time series ──────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS heartrate (
            timestamp   TEXT PRIMARY KEY,
            bpm         INTEGER,
            source      TEXT   -- awake | rest | sleep | workout | live
        );
        CREATE INDEX IF NOT EXISTS idx_heartrate_source ON heartrate(source);
        CREATE INDEX IF NOT EXISTS idx_heartrate_timestamp ON heartrate(timestamp);

        -- ── Workouts ────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS workouts (
            id              TEXT PRIMARY KEY,
            day             TEXT,
            activity        TEXT,
            intensity       TEXT,
            source          TEXT,
            start_datetime  TEXT,
            end_datetime    TEXT,
            duration        INTEGER,
            calories        REAL,
            distance        REAL,
            label           TEXT,
            raw_json        TEXT,
            synced_at       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_workouts_day ON workouts(day);

        -- ── Sessions ────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS sessions (
            id              TEXT PRIMARY KEY,
            day             TEXT,
            type            TEXT,
            start_datetime  TEXT,
            end_datetime    TEXT,
            mood            TEXT,
            raw_json        TEXT,
            synced_at       TEXT
        );

        -- ── Sleep time recommendations ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS sleep_time (
            id                              TEXT PRIMARY KEY,
            day                             TEXT,
            recommendation                  TEXT,
            status                          TEXT,
            optimal_bedtime_start_offset    INTEGER,
            optimal_bedtime_end_offset      INTEGER,
            raw_json                        TEXT,
            synced_at                       TEXT
        );

        -- ── Meals (custom — not from Oura) ──────────────────────────────────
        -- Logged manually: via conversation, CLI, or future integrations
        CREATE TABLE IF NOT EXISTS meals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at   TEXT NOT NULL,  -- ISO 8601 datetime (when the meal was eaten)
            meal_type   TEXT,           -- breakfast | lunch | dinner | snack | late_night
            description TEXT,           -- free text, e.g. "grilled salmon, rice, salad"
            calories    INTEGER,
            protein_g   REAL,
            carbs_g     REAL,
            fat_g       REAL,
            sat_fat_g   REAL,
            sugar_g     REAL,
            fiber_g     REAL,
            omega3_g    REAL,
            vitamin_d_mcg REAL,
            b12_mcg     REAL,
            magnesium_mg REAL,
            zinc_mg     REAL,
            iron_mg     REAL,
            potassium_mg REAL,
            sodium_mg   REAL,
            vitamin_c_mg REAL,
            vitamin_e_mg REAL,
            vitamin_b6_mg REAL,
            folate_mcg  REAL,
            notes       TEXT,           -- e.g. "post-workout", "felt heavy after"
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_meals_logged_at ON meals(logged_at);

        -- ── Meal items (optional detailed layer under meals) ───────────────
        -- When present, item rows are the detailed source used to roll up the
        -- parent meal totals. Older meals may only have meal-level totals.
        CREATE TABLE IF NOT EXISTS meal_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_id         INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
            sort_order      INTEGER,
            item_name       TEXT NOT NULL,
            quantity        REAL,
            unit            TEXT,
            serving_grams   REAL,
            brand           TEXT,
            restaurant      TEXT,
            fdc_id          INTEGER,
            calories        REAL,
            protein_g       REAL,
            carbs_g         REAL,
            fat_g           REAL,
            sat_fat_g       REAL,
            sugar_g         REAL,
            fiber_g         REAL,
            omega3_g        REAL,
            vitamin_d_mcg   REAL,
            b12_mcg         REAL,
            magnesium_mg    REAL,
            zinc_mg         REAL,
            iron_mg         REAL,
            potassium_mg    REAL,
            sodium_mg       REAL,
            vitamin_c_mg    REAL,
            vitamin_e_mg    REAL,
            vitamin_b6_mg   REAL,
            folate_mcg      REAL,
            source          TEXT,
            source_ref      TEXT,
            confidence      TEXT,
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_meal_items_meal_id ON meal_items(meal_id);

        -- ── Substance intake ─────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS substances (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at   TEXT NOT NULL,
            substance   TEXT NOT NULL,  -- weed | caffeine | nicotine | alcohol
            amount_deprecated TEXT,     -- legacy free-text column, use amount_value instead
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            amount_value REAL,          -- numeric amount (e.g. 0.25 for 0.25g weed, 300 for 300ml soju)
            amount_unit  TEXT,          -- unit (g, ml, mg, etc.)
            potency_pct  REAL           -- potency percentage (e.g. 30 for 30% THC, 12 for 12% ABV)
        );
        CREATE INDEX IF NOT EXISTS idx_substances_logged_at ON substances(logged_at);

        -- ── Sex / masturbation tracking ───────────────────────────────────
        CREATE TABLE IF NOT EXISTS sex (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at   TEXT NOT NULL,
            type        TEXT NOT NULL,  -- sex | goon
            duration_min INTEGER,
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sex_logged_at ON sex(logged_at);

        -- ── Travel tracking ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS travel (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            day                   TEXT NOT NULL,
            start_datetime        TEXT NOT NULL,
            end_datetime          TEXT,
            travel_type           TEXT NOT NULL,
            origin                TEXT,
            destination           TEXT,
            hours                 REAL,
            timezone_shift_hours  REAL,
            direction             TEXT,
            notes                 TEXT,
            created_at            TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_travel_day ON travel(day);
        CREATE INDEX IF NOT EXISTS idx_travel_start ON travel(start_datetime);

        -- ── Withings body composition sync ────────────────────────────────
        CREATE TABLE IF NOT EXISTS withings_sync_state (
            endpoint          TEXT PRIMARY KEY,
            last_synced_at    TEXT,
            last_update_unix  INTEGER
        );

        CREATE TABLE IF NOT EXISTS withings_measure_groups (
            grpid             INTEGER PRIMARY KEY,
            measured_at       TEXT NOT NULL,
            day               TEXT NOT NULL,
            created_at_api    TEXT,
            modified_at_api   TEXT,
            attrib            INTEGER,
            category          INTEGER,
            deviceid          TEXT,
            model             TEXT,
            raw_json          TEXT,
            synced_at         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_withings_measure_groups_day ON withings_measure_groups(day);
        CREATE INDEX IF NOT EXISTS idx_withings_measure_groups_measured_at ON withings_measure_groups(measured_at);

        CREATE TABLE IF NOT EXISTS withings_measure_items (
            grpid             INTEGER NOT NULL REFERENCES withings_measure_groups(grpid) ON DELETE CASCADE,
            measure_type      INTEGER NOT NULL,
            value_raw         INTEGER,
            unit              INTEGER,
            value             REAL,
            algo              INTEGER,
            fw                INTEGER,
            PRIMARY KEY (grpid, measure_type)
        );

        CREATE TABLE IF NOT EXISTS withings_body_composition (
            grpid                   INTEGER PRIMARY KEY REFERENCES withings_measure_groups(grpid) ON DELETE CASCADE,
            measured_at             TEXT NOT NULL,
            day                     TEXT NOT NULL,
            weight_kg               REAL,
            fat_free_mass_kg        REAL,
            fat_ratio_pct           REAL,
            fat_mass_kg             REAL,
            muscle_mass_kg          REAL,
            muscle_pct              REAL,
            hydration_kg            REAL,
            water_pct               REAL,
            bone_mass_kg            REAL,
            pulse_wave_velocity_ms  REAL,
            raw_json                TEXT,
            synced_at               TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_withings_body_composition_day ON withings_body_composition(day);
        CREATE INDEX IF NOT EXISTS idx_withings_body_composition_measured_at ON withings_body_composition(measured_at);

        -- ── Manual notes / journal entries ──────────────────────────────────
        CREATE TABLE IF NOT EXISTS journal (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            day         TEXT NOT NULL,
            category    TEXT,  -- sleep | training | nutrition | general | mood
            note        TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_journal_day ON journal(day);

        -- ── Workout detail logging ──────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS workout_sets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_day  TEXT NOT NULL,
            exercise     TEXT NOT NULL,
            set_number   INTEGER NOT NULL,
            reps         INTEGER,
            weight_lbs   REAL,
            weight_per_hand INTEGER DEFAULT 0,
            notes        TEXT,
            created_at   TEXT DEFAULT (datetime('now')),
            workout_id   TEXT REFERENCES workouts(id)
        );
        CREATE INDEX IF NOT EXISTS idx_workout_sets_day ON workout_sets(workout_day);

        -- ── Leveling system cache ───────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS leveling_daily_cache (
            day          TEXT PRIMARY KEY,
            vit_score    REAL,
            str_score    REAL,
            end_score    REAL,
            nut_score    REAL,
            dis_score    REAL,
            daily_xp     REAL,
            decay        REAL,
            computed_at  TEXT
        );
        """)
        # Idempotent column additions for schema evolution
        for col in ["sat_fat_g REAL", "sugar_g REAL", "fiber_g REAL", "omega3_g REAL",
                     "vitamin_d_mcg REAL", "b12_mcg REAL", "magnesium_mg REAL", "zinc_mg REAL",
                     "iron_mg REAL", "potassium_mg REAL", "sodium_mg REAL", "vitamin_c_mg REAL",
                     "vitamin_e_mg REAL", "vitamin_b6_mg REAL", "folate_mcg REAL"]:
            try:
                conn.execute(f"ALTER TABLE meals ADD COLUMN {col}")
            except Exception:
                pass
    print(f"Database initialised at {get_db_path()}")


# ── Upsert helpers ────────────────────────────────────────────────────────────

NOW = lambda: datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

MEAL_NUTRIENT_COLUMNS = [
    "calories", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "sugar_g",
    "fiber_g", "omega3_g", "vitamin_d_mcg", "b12_mcg", "magnesium_mg",
    "zinc_mg", "iron_mg", "potassium_mg", "sodium_mg", "vitamin_c_mg",
    "vitamin_e_mg", "vitamin_b6_mg", "folate_mcg",
]


def upsert_daily_sleep(conn, record: dict):
    c = record.get("contributors", {})
    conn.execute("""
        INSERT INTO daily_sleep VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(day) DO UPDATE SET
            score=excluded.score, contrib_deep_sleep=excluded.contrib_deep_sleep,
            contrib_efficiency=excluded.contrib_efficiency, contrib_latency=excluded.contrib_latency,
            contrib_rem_sleep=excluded.contrib_rem_sleep, contrib_restfulness=excluded.contrib_restfulness,
            contrib_timing=excluded.contrib_timing, contrib_total_sleep=excluded.contrib_total_sleep,
            raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["day"], record.get("score"),
        c.get("deep_sleep"), c.get("efficiency"), c.get("latency"),
        c.get("rem_sleep"), c.get("restfulness"), c.get("timing"), c.get("total_sleep"),
        json.dumps(record), NOW()
    ))


def upsert_daily_readiness(conn, record: dict):
    c = record.get("contributors", {})
    conn.execute("""
        INSERT INTO daily_readiness VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(day) DO UPDATE SET
            score=excluded.score, temperature_deviation=excluded.temperature_deviation,
            temperature_trend_deviation=excluded.temperature_trend_deviation,
            contrib_activity_balance=excluded.contrib_activity_balance,
            contrib_body_temperature=excluded.contrib_body_temperature,
            contrib_hrv_balance=excluded.contrib_hrv_balance,
            contrib_previous_day_activity=excluded.contrib_previous_day_activity,
            contrib_previous_night=excluded.contrib_previous_night,
            contrib_recovery_index=excluded.contrib_recovery_index,
            contrib_resting_heart_rate=excluded.contrib_resting_heart_rate,
            contrib_sleep_balance=excluded.contrib_sleep_balance,
            contrib_sleep_regularity=excluded.contrib_sleep_regularity,
            raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["day"], record.get("score"),
        record.get("temperature_deviation"), record.get("temperature_trend_deviation"),
        c.get("activity_balance"), c.get("body_temperature"), c.get("hrv_balance"),
        c.get("previous_day_activity"), c.get("previous_night"), c.get("recovery_index"),
        c.get("resting_heart_rate"), c.get("sleep_balance"), c.get("sleep_regularity"),
        json.dumps(record), NOW()
    ))


def upsert_daily_activity(conn, record: dict):
    conn.execute("""
        INSERT INTO daily_activity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(day) DO UPDATE SET
            score=excluded.score, active_calories=excluded.active_calories,
            total_calories=excluded.total_calories, steps=excluded.steps,
            average_met_minutes=excluded.average_met_minutes,
            high_activity_time=excluded.high_activity_time,
            medium_activity_time=excluded.medium_activity_time,
            low_activity_time=excluded.low_activity_time,
            sedentary_time=excluded.sedentary_time, resting_time=excluded.resting_time,
            target_calories=excluded.target_calories,
            raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["day"], record.get("score"),
        record.get("active_calories"), record.get("calories"),
        record.get("steps"), record.get("average_met_minutes"),
        record.get("high_activity_time"), record.get("medium_activity_time"),
        record.get("low_activity_time"), record.get("sedentary_time"),
        record.get("resting_time"), record.get("target_calories"),
        json.dumps(record), NOW()
    ))


def upsert_daily_stress(conn, record: dict):
    conn.execute("""
        INSERT INTO daily_stress VALUES (?,?,?,?,?,?)
        ON CONFLICT(day) DO UPDATE SET
            stress_high=excluded.stress_high, recovery_high=excluded.recovery_high,
            day_summary=excluded.day_summary, raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["day"], record.get("stress_high"), record.get("recovery_high"),
        record.get("day_summary"), json.dumps(record), NOW()
    ))


def upsert_daily_spo2(conn, record: dict):
    spo2 = record.get("spo2_percentage")
    avg = spo2.get("average") if isinstance(spo2, dict) else spo2
    conn.execute("""
        INSERT INTO daily_spo2 VALUES (?,?,?,?,?)
        ON CONFLICT(day) DO UPDATE SET
            spo2_average=excluded.spo2_average,
            breathing_disturbance_index=excluded.breathing_disturbance_index,
            raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["day"], avg, record.get("breathing_disturbance_index"),
        json.dumps(record), NOW()
    ))


def upsert_daily_cardiovascular_age(conn, record: dict):
    conn.execute("""
        INSERT INTO daily_cardiovascular_age VALUES (?,?,?,?)
        ON CONFLICT(day) DO UPDATE SET
            vascular_age=excluded.vascular_age,
            raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["day"], record.get("vascular_age"),
        json.dumps(record), NOW()
    ))


def upsert_daily_resilience(conn, record: dict):
    c = record.get("contributors", {})
    conn.execute("""
        INSERT INTO daily_resilience VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(day) DO UPDATE SET
            level=excluded.level,
            contrib_sleep_recovery=excluded.contrib_sleep_recovery,
            contrib_daytime_recovery=excluded.contrib_daytime_recovery,
            contrib_stress=excluded.contrib_stress,
            raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["day"], record.get("level"),
        c.get("sleep_recovery"), c.get("daytime_recovery"), c.get("stress"),
        json.dumps(record), NOW()
    ))


def upsert_vo2_max(conn, record: dict):
    conn.execute("""
        INSERT INTO vo2_max VALUES (?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            day=excluded.day, timestamp=excluded.timestamp,
            vo2_max=excluded.vo2_max,
            raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["id"], record.get("day"), record.get("timestamp"),
        record.get("vo2_max"),
        json.dumps(record), NOW()
    ))


def upsert_sleep_period(conn, record: dict):
    hr = record.get("heart_rate")
    hrv = record.get("hrv")
    conn.execute("""
        INSERT INTO sleep_periods VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            day=excluded.day, type=excluded.type, period=excluded.period,
            bedtime_start=excluded.bedtime_start, bedtime_end=excluded.bedtime_end,
            time_in_bed=excluded.time_in_bed, total_sleep_duration=excluded.total_sleep_duration,
            efficiency=excluded.efficiency, latency=excluded.latency,
            deep_sleep_duration=excluded.deep_sleep_duration,
            light_sleep_duration=excluded.light_sleep_duration,
            rem_sleep_duration=excluded.rem_sleep_duration,
            awake_time=excluded.awake_time, restless_periods=excluded.restless_periods,
            average_hrv=excluded.average_hrv, average_heart_rate=excluded.average_heart_rate,
            lowest_heart_rate=excluded.lowest_heart_rate, average_breath=excluded.average_breath,
            sleep_phase_30_sec=excluded.sleep_phase_30_sec,
            sleep_phase_5_min=excluded.sleep_phase_5_min,
            hr_series_json=excluded.hr_series_json, hrv_series_json=excluded.hrv_series_json,
            raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["id"], record["day"], record.get("type"), record.get("period"),
        record.get("bedtime_start"), record.get("bedtime_end"),
        record.get("time_in_bed"), record.get("total_sleep_duration"),
        record.get("efficiency"), record.get("latency"),
        record.get("deep_sleep_duration"), record.get("light_sleep_duration"),
        record.get("rem_sleep_duration"), record.get("awake_time"),
        record.get("restless_periods"), record.get("average_hrv"),
        record.get("average_heart_rate"), record.get("lowest_heart_rate"),
        record.get("average_breath"),
        record.get("sleep_phase_30_sec"), record.get("sleep_phase_5_min"),
        json.dumps(hr) if hr else None,
        json.dumps(hrv) if hrv else None,
        json.dumps(record), NOW()
    ))


def upsert_heartrate_batch(conn, records: list):
    """Batch insert for heart rate — uses INSERT OR IGNORE (timestamp is immutable)."""
    conn.executemany(
        "INSERT OR IGNORE INTO heartrate (timestamp, bpm, source) VALUES (?,?,?)",
        [(r["timestamp"], r.get("bpm"), r.get("source")) for r in records]
    )


def upsert_workout(conn, record: dict):
    dur = None
    if record.get("start_datetime") and record.get("end_datetime"):
        try:
            s = datetime.fromisoformat(record["start_datetime"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(record["end_datetime"].replace("Z", "+00:00"))
            dur = int((e - s).total_seconds())
        except Exception:
            pass
    conn.execute("""
        INSERT INTO workouts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            day=excluded.day, activity=excluded.activity, intensity=excluded.intensity,
            source=excluded.source, start_datetime=excluded.start_datetime,
            end_datetime=excluded.end_datetime, duration=excluded.duration,
            calories=excluded.calories, distance=excluded.distance,
            label=excluded.label, raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["id"], record["day"], record.get("activity"), record.get("intensity"),
        record.get("source"), record.get("start_datetime"), record.get("end_datetime"),
        dur, record.get("calories"), record.get("distance"), record.get("label"),
        json.dumps(record), NOW()
    ))


def upsert_session(conn, record: dict):
    conn.execute("""
        INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            day=excluded.day, type=excluded.type,
            start_datetime=excluded.start_datetime, end_datetime=excluded.end_datetime,
            mood=excluded.mood, raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["id"], record["day"], record.get("type"),
        record.get("start_datetime"), record.get("end_datetime"),
        record.get("mood"), json.dumps(record), NOW()
    ))


def upsert_sleep_time(conn, record: dict):
    ob = record.get("optimal_bedtime") or {}
    conn.execute("""
        INSERT INTO sleep_time VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            day=excluded.day, recommendation=excluded.recommendation, status=excluded.status,
            optimal_bedtime_start_offset=excluded.optimal_bedtime_start_offset,
            optimal_bedtime_end_offset=excluded.optimal_bedtime_end_offset,
            raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        record["id"], record["day"], record.get("recommendation"), record.get("status"),
        ob.get("start_offset"), ob.get("end_offset"),
        json.dumps(record), NOW()
    ))


WITHINGS_MEASURE_TYPES = {
    1: "weight_kg",
    5: "fat_free_mass_kg",
    6: "fat_ratio_pct",
    8: "fat_mass_kg",
    76: "muscle_mass_kg",
    77: "hydration_kg",
    88: "bone_mass_kg",
    91: "pulse_wave_velocity_ms",
}


def withings_value(measure: dict):
    """Convert a Withings raw measure value using value * 10^unit."""
    raw = measure.get("value")
    unit = measure.get("unit")
    if raw is None or unit is None:
        return None
    return float(raw) * (10 ** int(unit))


def _unix_to_utc_iso(value):
    if value is None:
        return None
    return datetime.utcfromtimestamp(int(value)).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_withings_measure_group(conn, record: dict):
    """Store raw Withings group/items and flattened Body Comp metrics."""
    grpid = record["grpid"]
    measured_at = _unix_to_utc_iso(record.get("date"))
    day = local_day(measured_at)
    synced_at = NOW()
    conn.execute("""
        INSERT INTO withings_measure_groups
        (grpid, measured_at, day, created_at_api, modified_at_api, attrib, category,
         deviceid, model, raw_json, synced_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(grpid) DO UPDATE SET
            measured_at=excluded.measured_at, day=excluded.day,
            created_at_api=excluded.created_at_api, modified_at_api=excluded.modified_at_api,
            attrib=excluded.attrib, category=excluded.category, deviceid=excluded.deviceid,
            model=excluded.model, raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        grpid, measured_at, day,
        _unix_to_utc_iso(record.get("created")),
        _unix_to_utc_iso(record.get("modified")),
        record.get("attrib"), record.get("category"),
        record.get("deviceid"), record.get("model"),
        json.dumps(record), synced_at,
    ))

    values = {}
    for measure in record.get("measures", []):
        measure_type = measure.get("type")
        converted = withings_value(measure)
        conn.execute("""
            INSERT INTO withings_measure_items
            (grpid, measure_type, value_raw, unit, value, algo, fw)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(grpid, measure_type) DO UPDATE SET
                value_raw=excluded.value_raw, unit=excluded.unit, value=excluded.value,
                algo=excluded.algo, fw=excluded.fw
        """, (
            grpid, measure_type, measure.get("value"), measure.get("unit"), converted,
            measure.get("algo"), measure.get("fw"),
        ))
        if measure_type in WITHINGS_MEASURE_TYPES:
            values[WITHINGS_MEASURE_TYPES[measure_type]] = converted

    weight = values.get("weight_kg")
    muscle = values.get("muscle_mass_kg")
    hydration = values.get("hydration_kg")
    muscle_pct = round(muscle * 100 / weight, 2) if weight and muscle is not None else None
    water_pct = round(hydration * 100 / weight, 2) if weight and hydration is not None else None

    conn.execute("""
        INSERT INTO withings_body_composition
        (grpid, measured_at, day, weight_kg, fat_free_mass_kg, fat_ratio_pct,
         fat_mass_kg, muscle_mass_kg, muscle_pct, hydration_kg, water_pct,
         bone_mass_kg, pulse_wave_velocity_ms, raw_json, synced_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(grpid) DO UPDATE SET
            measured_at=excluded.measured_at, day=excluded.day,
            weight_kg=excluded.weight_kg, fat_free_mass_kg=excluded.fat_free_mass_kg,
            fat_ratio_pct=excluded.fat_ratio_pct, fat_mass_kg=excluded.fat_mass_kg,
            muscle_mass_kg=excluded.muscle_mass_kg, muscle_pct=excluded.muscle_pct,
            hydration_kg=excluded.hydration_kg, water_pct=excluded.water_pct,
            bone_mass_kg=excluded.bone_mass_kg,
            pulse_wave_velocity_ms=excluded.pulse_wave_velocity_ms,
            raw_json=excluded.raw_json, synced_at=excluded.synced_at
    """, (
        grpid, measured_at, day,
        values.get("weight_kg"), values.get("fat_free_mass_kg"), values.get("fat_ratio_pct"),
        values.get("fat_mass_kg"), values.get("muscle_mass_kg"), muscle_pct,
        values.get("hydration_kg"), water_pct, values.get("bone_mass_kg"),
        values.get("pulse_wave_velocity_ms"), json.dumps(record), synced_at,
    ))


def _ensure_tz(ts: str) -> str:
    """Ensure a timestamp string has a timezone offset. Assumes LOCAL_TZ if missing."""
    return ensure_tz(ts)


def _now_local() -> str:
    """Current time in local timezone with offset."""
    return now_local()


def insert_meal(conn, logged_at: str, meal_type: str, description: str,
                calories: int = None, protein_g: float = None,
                carbs_g: float = None, fat_g: float = None,
                sat_fat_g: float = None, sugar_g: float = None,
                fiber_g: float = None, omega3_g: float = None,
                vitamin_d_mcg: float = None, b12_mcg: float = None,
                magnesium_mg: float = None, zinc_mg: float = None,
                iron_mg: float = None, potassium_mg: float = None,
                sodium_mg: float = None, vitamin_c_mg: float = None,
                vitamin_e_mg: float = None, vitamin_b6_mg: float = None,
                folate_mcg: float = None, notes: str = None):
    """Insert a meal using an existing transaction and return meal_id."""
    cur = conn.execute(
        "INSERT INTO meals (logged_at, meal_type, description, calories, protein_g, carbs_g, fat_g, "
        "sat_fat_g, sugar_g, fiber_g, omega3_g, vitamin_d_mcg, b12_mcg, magnesium_mg, zinc_mg, "
        "iron_mg, potassium_mg, sodium_mg, vitamin_c_mg, vitamin_e_mg, vitamin_b6_mg, folate_mcg, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (logged_at, meal_type, description, calories, protein_g, carbs_g, fat_g,
         sat_fat_g, sugar_g, fiber_g, omega3_g, vitamin_d_mcg, b12_mcg, magnesium_mg, zinc_mg,
         iron_mg, potassium_mg, sodium_mg, vitamin_c_mg, vitamin_e_mg, vitamin_b6_mg, folate_mcg, notes)
    )
    return cur.lastrowid


def log_meal(day: str, meal_type: str, description: str,
             calories: int = None, protein_g: float = None,
             carbs_g: float = None, fat_g: float = None,
             sat_fat_g: float = None, sugar_g: float = None,
             fiber_g: float = None, omega3_g: float = None,
             vitamin_d_mcg: float = None, b12_mcg: float = None,
             magnesium_mg: float = None, zinc_mg: float = None,
             iron_mg: float = None, potassium_mg: float = None,
             sodium_mg: float = None, vitamin_c_mg: float = None,
             vitamin_e_mg: float = None, vitamin_b6_mg: float = None,
             folate_mcg: float = None,
             notes: str = None, logged_at: str = None):
    """Log a meal entry. logged_at defaults to now if not provided."""
    if not logged_at:
        logged_at = _now_local()
    else:
        logged_at = _ensure_tz(logged_at)
    day = day or local_day(logged_at)
    with get_conn() as conn:
        meal_id = insert_meal(
            conn, logged_at, meal_type, description,
            calories, protein_g, carbs_g, fat_g, sat_fat_g, sugar_g,
            fiber_g, omega3_g, vitamin_d_mcg, b12_mcg, magnesium_mg,
            zinc_mg, iron_mg, potassium_mg, sodium_mg, vitamin_c_mg,
            vitamin_e_mg, vitamin_b6_mg, folate_mcg, notes,
        )
    print(f"Logged: {meal_type} on {day} — {description}")
    return meal_id


def add_meal_item(conn, meal_id: int, item: dict, sort_order: int = None):
    """Insert one detailed meal item row under an existing meal."""
    columns = [
        "meal_id", "sort_order", "item_name", "quantity", "unit", "serving_grams",
        "brand", "restaurant", "fdc_id",
        *MEAL_NUTRIENT_COLUMNS,
        "source", "source_ref", "confidence", "notes",
    ]
    values = [
        meal_id,
        sort_order if sort_order is not None else item.get("sort_order"),
        item.get("item_name") or item.get("description") or item.get("name"),
        item.get("quantity"),
        item.get("unit"),
        item.get("serving_grams") if item.get("serving_grams") is not None else item.get("serving_g"),
        item.get("brand"),
        item.get("restaurant"),
        item.get("fdc_id"),
        *[item.get(col) for col in MEAL_NUTRIENT_COLUMNS],
        item.get("source"),
        item.get("source_ref"),
        item.get("confidence"),
        item.get("notes"),
    ]
    if not values[2]:
        raise ValueError("meal item requires item_name or description")
    placeholders = ",".join(["?"] * len(columns))
    cur = conn.execute(
        f"INSERT INTO meal_items ({','.join(columns)}) VALUES ({placeholders})",
        values,
    )
    return cur.lastrowid


def rollup_meal_items(conn, meal_id: int):
    """Recompute parent meal nutrient totals from item rows."""
    select_cols = ", ".join(
        f"SUM({col}) AS {col}" for col in MEAL_NUTRIENT_COLUMNS
    )
    totals = conn.execute(
        f"SELECT {select_cols} FROM meal_items WHERE meal_id = ?",
        (meal_id,),
    ).fetchone()
    if not totals:
        return
    updates = {col: totals[col] for col in MEAL_NUTRIENT_COLUMNS}
    if updates["calories"] is not None:
        updates["calories"] = round(updates["calories"])
    for col, value in list(updates.items()):
        if value is not None and col != "calories":
            updates[col] = round(value, 2)
    assignments = ", ".join(f"{col}=?" for col in MEAL_NUTRIENT_COLUMNS)
    conn.execute(
        f"UPDATE meals SET {assignments} WHERE id=?",
        [updates[col] for col in MEAL_NUTRIENT_COLUMNS] + [meal_id],
    )


def log_meal_with_items(day: str, meal_type: str, description: str,
                        items: list[dict], notes: str = None, logged_at: str = None):
    """Log a meal with structured item rows and rolled-up parent totals."""
    if not logged_at:
        logged_at = _now_local()
    else:
        logged_at = _ensure_tz(logged_at)
    day = day or local_day(logged_at)
    with get_conn() as conn:
        meal_id = insert_meal(conn, logged_at, meal_type, description, notes=notes)
        for idx, item in enumerate(items, start=1):
            add_meal_item(conn, meal_id, item, sort_order=idx)
        rollup_meal_items(conn, meal_id)
    print(f"Logged: {meal_type} on {day} — {description}")
    return meal_id


def log_substance(logged_at: str, substance: str, amount_value: float = None,
                   amount_unit: str = None, potency_pct: float = None, notes: str = None):
    """Log a substance intake event."""
    logged_at = _ensure_tz(logged_at)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO substances (logged_at, substance, amount_value, amount_unit, potency_pct, notes) "
            "VALUES (?,?,?,?,?,?)",
            (logged_at, substance, amount_value, amount_unit, potency_pct, notes)
        )
    print(f"Logged: {substance} at {logged_at}")


def log_sex(logged_at: str, type: str, duration_min: int = None, notes: str = None):
    """Log a sex or goon entry."""
    logged_at = _ensure_tz(logged_at)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sex (logged_at, type, duration_min, notes) VALUES (?,?,?,?)",
            (logged_at, type, duration_min, notes)
        )
    print(f"Logged: {type} at {logged_at}")


def log_travel(start_datetime: str, end_datetime: str = None,
               travel_type: str = "flight", origin: str = None, destination: str = None,
               hours: float = None, timezone_shift_hours: float = None,
               direction: str = None, notes: str = None):
    """Log a travel event such as a flight, drive, train, or bus segment."""
    start_datetime = _ensure_tz(start_datetime)
    if end_datetime:
        end_datetime = _ensure_tz(end_datetime)
    day = event_local_day(start_datetime)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO travel "
            "(day, start_datetime, end_datetime, travel_type, origin, destination, hours, "
            "timezone_shift_hours, direction, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (day, start_datetime, end_datetime, travel_type, origin, destination, hours,
             timezone_shift_hours, direction, notes)
        )
        travel_id = cur.lastrowid
    label = f"{origin or ''} -> {destination or ''}".strip()
    print(f"Logged travel: {travel_type} on {day}" + (f" — {label}" if label else ""))
    return travel_id


def log_journal(day: str, note: str, category: str = "general"):
    """Log a journal/note entry."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO journal (day, category, note) VALUES (?,?,?)",
            (day, category, note)
        )


def log_workout_set(workout_id: str, workout_day: str, exercise: str,
                    set_number: int, reps: int, weight_lbs: float,
                    weight_per_hand: bool = True, notes: str = None):
    """Log a single set.

    `workout_id` must point to an existing row in the `workouts` table
    (Oura creates one when a kettlebell session is logged in the app).
    `weight_per_hand=True` means the weight is per-hand (e.g. 25lb each hand
    in a double KB press); False means single implement / single-hand.
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO workout_sets "
            "(workout_day, exercise, set_number, reps, weight_lbs, weight_per_hand, notes, created_at, workout_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (workout_day, exercise, set_number, reps, weight_lbs,
             1 if weight_per_hand else 0, notes, _now_local(), workout_id)
        )
    print(f"Logged set: {exercise} #{set_number}  {reps} reps @ {weight_lbs}lb"
          + (" each hand" if weight_per_hand else ""))


def log_workout_session(workout_id: str, workout_day: str,
                        sets: list[tuple], notes: str = None):
    """Log a full session at once.

    `sets` is a list of tuples describing each set, in order. Each tuple:
        (exercise, set_number, reps, weight_lbs)                         # per-hand defaults True
        (exercise, set_number, reps, weight_lbs, weight_per_hand)        # explicit bool
        (exercise, set_number, reps, weight_lbs, weight_per_hand, note)  # with a per-set note

    Example — 3 rounds of press + front squat + row at 25lb each hand:
        log_workout_session("<workout_id>", "2026-04-23", [
            ("double KB press",         1, 7, 25.0),
            ("double KB press",         2, 6, 25.0),
            ("double KB press",         3, 5, 25.0),
            ("double KB front squat",   1, 8, 25.0),
            ...
        ])
    """
    for s in sets:
        if len(s) == 4:
            exercise, set_number, reps, weight_lbs = s
            weight_per_hand, note = True, None
        elif len(s) == 5:
            exercise, set_number, reps, weight_lbs, weight_per_hand = s
            note = None
        elif len(s) == 6:
            exercise, set_number, reps, weight_lbs, weight_per_hand, note = s
        else:
            raise ValueError(f"Set tuple must have 4, 5, or 6 elements, got {len(s)}: {s}")
        log_workout_set(workout_id, workout_day, exercise,
                        set_number, reps, weight_lbs, weight_per_hand, note)
    if notes:
        log_journal(workout_day, notes, category="training")


def get_sync_state(endpoint: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_synced_at FROM sync_state WHERE endpoint=?", (endpoint,)
        ).fetchone()
        return row["last_synced_at"] if row else None


def set_sync_state(conn, endpoint: str, synced_at: str):
    conn.execute(
        "INSERT INTO sync_state VALUES (?,?) ON CONFLICT(endpoint) DO UPDATE SET last_synced_at=excluded.last_synced_at",
        (endpoint, synced_at)
    )


def get_withings_sync_state(endpoint: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_synced_at, last_update_unix FROM withings_sync_state WHERE endpoint=?",
            (endpoint,),
        ).fetchone()
        return dict(row) if row else None


def set_withings_sync_state(conn, endpoint: str, synced_at: str, last_update_unix: int):
    conn.execute(
        "INSERT INTO withings_sync_state (endpoint, last_synced_at, last_update_unix) VALUES (?,?,?) "
        "ON CONFLICT(endpoint) DO UPDATE SET "
        "last_synced_at=excluded.last_synced_at, last_update_unix=excluded.last_update_unix",
        (endpoint, synced_at, last_update_unix),
    )


if __name__ == "__main__":
    init_db()
