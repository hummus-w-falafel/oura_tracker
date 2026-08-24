from flask import Flask, jsonify, render_template, request
import json, os, re
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, date as Date

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from db import get_conn
from nutrition import compute_nutrition_score
from advanced_analytics import (
    VAR_MODELS,
    compute_sleep_regularity,
    cosinor_trend,
    detect_change_points,
    fit_cosinor,
    run_var_analysis,
)

app = Flask(__name__)
TZ  = ZoneInfo(os.environ.get("TIMEZONE", "America/Toronto"))
UTC = ZoneInfo("UTC")
DISPLAY_NAME = os.environ.get("DISPLAY_NAME", "User")
BIG_TIMEZONE_SHIFT_HOURS = 6
BODY_COMP_FIELDS = [
    "weight_kg", "fat_free_mass_kg", "fat_ratio_pct", "fat_mass_kg",
    "muscle_mass_kg", "muscle_pct", "hydration_kg", "water_pct",
    "bone_mass_kg", "pulse_wave_velocity_ms",
]
ANALYTICS_METRICS = {
    "hrv": {"label": "HRV", "unit": "ms", "higher_is_better": True},
    "lowest_hr": {"label": "Lowest HR", "unit": "bpm", "higher_is_better": False},
    "sleep_score": {"label": "Sleep Score", "unit": "", "higher_is_better": True},
    "readiness_score": {"label": "Readiness", "unit": "", "higher_is_better": True},
    "bedtime_hour": {"label": "Bedtime", "unit": "hour", "higher_is_better": None},
    "steps": {"label": "Steps", "unit": "steps", "higher_is_better": True},
    "active_cal": {"label": "Active Calories", "unit": "kcal", "higher_is_better": True},
    "weight_kg": {"label": "Weight", "unit": "kg", "higher_is_better": None},
    "fat_ratio_pct": {"label": "Body Fat", "unit": "%", "higher_is_better": False},
    "muscle_pct": {"label": "Muscle", "unit": "%", "higher_is_better": True},
}

def q(sql, params=()):
    with get_conn() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]

def day_utc(date_str):
    local = datetime.fromisoformat(date_str).replace(tzinfo=TZ)
    fmt   = "%Y-%m-%dT%H:%M:%S.000Z"
    return local.astimezone(UTC).strftime(fmt), (local + timedelta(days=1)).astimezone(UTC).strftime(fmt)

def parse_dt(s):
    s = re.sub(r'\.\d+', '', s).replace('Z', '+00:00')
    return datetime.fromisoformat(s)


def day_offset(day, offset):
    return (Date.fromisoformat(day) + timedelta(days=offset)).isoformat()


def latest_body_composition_by_day(since: str = None):
    where = " WHERE day > ?" if since else ""
    params = (since,) if since else ()
    rows = q(
        "SELECT day, measured_at, " + ", ".join(BODY_COMP_FIELDS) +
        " FROM withings_body_composition" +
        where +
        " ORDER BY day, measured_at",
        params,
    )
    latest = {}
    for row in rows:
        latest[row["day"]] = row
    return latest


def sleep_runs(since):
    """Parse all sleep nights since a date into stage runs."""
    STAGE = {"1": "Deep", "2": "Light", "3": "REM", "4": "Awake"}
    rows = q(
        "SELECT bedtime_start, sleep_phase_5_min AS phases"
        " FROM sleep_periods WHERE type='long_sleep' AND day > ? ORDER BY day",
        (since,)
    )
    runs = []
    for row in rows:
        if not row["phases"]: continue
        t0 = int(parse_dt(row["bedtime_start"]).timestamp() * 1000)
        i, ph = 0, row["phases"]
        while i < len(ph):
            j = i
            while j < len(ph) and ph[j] == ph[i]: j += 1
            runs.append({"stage": STAGE.get(ph[i], "Light"), "start": t0 + i*300_000, "end": t0 + j*300_000})
            i = j
    return runs

@app.route("/")
def index():
    return render_template("dashboard.html", name=DISPLAY_NAME)

# --- multi-day summary (cards + trends chart) ---
@app.route("/api/data/<int:days>")
def data(days):
    sleep = {r["day"]: r for r in q("SELECT day, score FROM daily_sleep ORDER BY day DESC LIMIT ?", (days,))}
    ready = {r["day"]: r for r in q("SELECT day, score FROM daily_readiness ORDER BY day DESC LIMIT ?", (days,))}
    sp    = {r["day"]: r for r in q(
        "SELECT day, average_hrv, lowest_heart_rate"
        " FROM sleep_periods WHERE type='long_sleep' ORDER BY day DESC LIMIT ?", (days,)
    )}
    all_days = sorted(set(list(sleep) + list(ready) + list(sp)))[-days:]
    return jsonify({"days": [{
        "day":   d,
        "sleep": sleep.get(d, {}).get("score"),
        "ready": ready.get(d, {}).get("score"),
        "hrv":   sp.get(d, {}).get("average_hrv"),
        "rhr":   sp.get(d, {}).get("lowest_heart_rate"),
    } for d in all_days]})

# --- continuous scrollable timeline ---
@app.route("/api/continuous/<int:days>")
def continuous(days):
    since     = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    since_utc, _ = day_utc(since)

    # Downsample HR: 1-min for 7D, 2-min for 14D, 5-min for 30D
    bucket = 60 if days <= 7 else (120 if days <= 14 else 300)
    hr = q(
        "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', (strftime('%s', substr(timestamp,1,19)) / :b) * :b, 'unixepoch') AS t,"
        " ROUND(AVG(bpm), 1) AS bpm"
        " FROM heartrate WHERE timestamp >= :s"
        " GROUP BY strftime('%s', substr(timestamp,1,19)) / :b"
        " ORDER BY t",
        {"b": bucket, "s": since_utc}
    )

    workouts = q(
        "SELECT activity, start_datetime AS start, end_datetime AS end, calories"
        " FROM workouts WHERE day > ? AND start_datetime IS NOT NULL ORDER BY start",
        (since,)
    )
    substances = q(
        "SELECT substance, amount_deprecated AS amount, logged_at"
        " FROM substances WHERE substr(logged_at,1,10) > ? ORDER BY logged_at",
        (since,)
    )

    meals = q(
        "SELECT meal_type, description, calories, protein_g, carbs_g, fat_g, sat_fat_g, sugar_g,"
        " fiber_g, omega3_g, vitamin_d_mcg, b12_mcg, magnesium_mg, zinc_mg, iron_mg,"
        " potassium_mg, sodium_mg, vitamin_c_mg, vitamin_e_mg, vitamin_b6_mg, folate_mcg, logged_at"
        " FROM meals WHERE substr(logged_at,1,10) > ? ORDER BY logged_at",
        (since,)
    )

    sex = q(
        "SELECT type, duration_min, notes, logged_at"
        " FROM sex WHERE substr(logged_at,1,10) > ? ORDER BY logged_at",
        (since,)
    )

    travel = q(
        "SELECT travel_type, origin, destination, hours, timezone_shift_hours, direction,"
        " start_datetime AS start, end_datetime AS end, notes"
        " FROM travel WHERE day > ? ORDER BY start_datetime",
        (since,)
    )

    # Midnight boundary markers
    d = Date.fromisoformat(since)
    today = datetime.now(TZ).date()
    boundaries = []
    while d <= today:
        midnight = datetime(d.year, d.month, d.day, tzinfo=TZ)
        boundaries.append({
            "t":     int(midnight.astimezone(UTC).timestamp() * 1000),
            "label": midnight.strftime("%-d %b"),
        })
        d += timedelta(days=1)

    today_str = datetime.now(TZ).strftime("%Y-%m-%d")

    sleep_scores = {r["day"]: r["score"] for r in q(
        "SELECT day, score FROM daily_sleep WHERE day > ? ORDER BY day",
        (since,)
    )}

    # Intra-night HRV series from sleep periods
    hrv_nights = q(
        "SELECT day, hrv_series_json, bedtime_start"
        " FROM sleep_periods WHERE type='long_sleep' AND hrv_series_json IS NOT NULL AND day > ?"
        " ORDER BY day",
        (since,)
    )
    hrv_series = []
    for night in hrv_nights:
        series = json.loads(night["hrv_series_json"])
        interval_ms = int(series.get("interval", 300) * 1000)
        t0 = int(parse_dt(series.get("timestamp", night["bedtime_start"])).timestamp() * 1000)
        for i, val in enumerate(series.get("items", [])):
            if val is not None:
                hrv_series.append({"t": t0 + i * interval_ms, "hrv": val})

    return jsonify({
        "hr":           hr,
        "hrv_series":   hrv_series,
        "runs":         sleep_runs(since),
        "workouts":     workouts,
        "meals":        meals,
        "substances":   substances,
        "sex":          sex,
        "travel":       travel,
        "boundaries":   boundaries,
        "sleep_scores": sleep_scores,
        "today":        today_str,
    })

# --- daily scores for the scores chart ---
@app.route("/api/scores/<int:days>")
def scores(days):
    since = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%d")

    sleep = {r["day"]: r["score"] for r in q(
        "SELECT day, score FROM daily_sleep WHERE day > ? ORDER BY day", (since,)
    )}
    readiness = {r["day"]: r["score"] for r in q(
        "SELECT day, score FROM daily_readiness WHERE day > ? ORDER BY day", (since,)
    )}
    activity = {r["day"]: r["score"] for r in q(
        "SELECT day, score FROM daily_activity WHERE day > ? ORDER BY day", (since,)
    )}
    sleep_dur = {r["day"]: round(r["dur"] / 3600, 2) for r in q(
        "SELECT day, total_sleep_duration AS dur FROM sleep_periods "
        "WHERE type='long_sleep' AND day > ? ORDER BY day", (since,)
    )}

    meal_days = q(
        "SELECT substr(logged_at,1,10) AS day, "
        "SUM(calories) AS calories, SUM(protein_g) AS protein_g, "
        "SUM(carbs_g) AS carbs_g, SUM(fat_g) AS fat_g, "
        "SUM(sat_fat_g) AS sat_fat_g, SUM(sugar_g) AS sugar_g, "
        "SUM(fiber_g) AS fiber_g, SUM(omega3_g) AS omega3_g, "
        "SUM(vitamin_d_mcg) AS vitamin_d_mcg, SUM(b12_mcg) AS b12_mcg, "
        "SUM(magnesium_mg) AS magnesium_mg, SUM(zinc_mg) AS zinc_mg, "
        "SUM(iron_mg) AS iron_mg, SUM(potassium_mg) AS potassium_mg, "
        "SUM(sodium_mg) AS sodium_mg, SUM(vitamin_c_mg) AS vitamin_c_mg, "
        "SUM(vitamin_e_mg) AS vitamin_e_mg, SUM(vitamin_b6_mg) AS vitamin_b6_mg, "
        "SUM(folate_mcg) AS folate_mcg "
        "FROM meals WHERE substr(logged_at,1,10) > ? "
        "GROUP BY substr(logged_at,1,10)", (since,)
    )
    nutrition = {}
    for row in meal_days:
        score = compute_nutrition_score(row)
        if score is not None:
            nutrition[row["day"]] = score

    all_days = sorted(set(
        list(sleep) + list(readiness) + list(activity) +
        list(sleep_dur) + list(nutrition)
    ))

    return jsonify({"scores": [{
        "day": d,
        "sleep": sleep.get(d),
        "readiness": readiness.get(d),
        "activity": activity.get(d),
        "hours_slept": sleep_dur.get(d),
        "nutrition": nutrition.get(d),
    } for d in all_days]})


@app.route("/api/body-composition/<int:days>")
def body_composition(days):
    since = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    by_day = latest_body_composition_by_day(since)

    def rounded(row, digits=2):
        return {
            "day": row["day"],
            "measured_at": row["measured_at"],
            **{
                field: round(row[field], digits) if row[field] is not None else None
                for field in BODY_COMP_FIELDS
            },
        }

    return jsonify({"body_composition": [rounded(by_day[d]) for d in sorted(by_day)]})


@app.route("/api/strength/<int:days>")
def strength(days):
    today = datetime.now(TZ).date()
    start_day = today - timedelta(days=max(days, 1) - 1)
    day_axis = [(start_day + timedelta(days=i)).isoformat() for i in range((today - start_day).days + 1)]
    first_week_start = start_day - timedelta(days=start_day.weekday())
    week_axis = []
    week_start = first_week_start
    while week_start <= today:
        week_axis.append({
            "week_start": week_start.isoformat(),
            "week": week_start.strftime("%Y-W%W"),
        })
        week_start += timedelta(days=7)

    rows = q(
        "SELECT workout_day, exercise, set_number, reps, weight_lbs, weight_per_hand, notes"
        " FROM workout_sets WHERE workout_day >= ?"
        " ORDER BY workout_day, exercise, set_number",
        (start_day.isoformat(),)
    )

    def set_load(r):
        weight = r["weight_lbs"] or 0
        implements = 2 if r["weight_per_hand"] else 1
        if weight <= 0:
            return r["reps"] or 0
        return round((r["reps"] or 0) * weight * implements, 1)

    daily = {}
    weekly = {}
    for r in rows:
        day = r["workout_day"]
        exercise = r["exercise"]
        workout_date = datetime.fromisoformat(day).date()
        week_start = (workout_date - timedelta(days=workout_date.weekday())).isoformat()
        week = workout_date.strftime("%Y-W%W")
        load = set_load(r)
        set_detail = {
            "set_number": r["set_number"],
            "day": day,
            "reps": r["reps"],
            "weight_lbs": r["weight_lbs"] or 0,
            "style": "each hand" if r["weight_per_hand"] else "single",
            "notes": r["notes"],
            "load": load,
        }
        day_bucket = daily.setdefault((day, exercise), [])
        day_bucket.append(set_detail)
        week_bucket = weekly.setdefault((week_start, week, exercise), [])
        week_bucket.append(set_detail)

    daily_sets = [
        {"day": day, "exercise": exercise, "sets": len(sets), "details": sorted(sets, key=lambda s: s["set_number"] or 0)}
        for (day, exercise), sets in sorted(daily.items())
    ]
    weekly_sets = [
        {"week_start": week_start, "week": week, "exercise": exercise, "sets": len(sets), "details": sorted(sets, key=lambda s: s["set_number"] or 0)}
        for (week_start, week, exercise), sets in sorted(weekly.items())
    ]

    return jsonify({
        "days": day_axis,
        "weeks": week_axis,
        "daily_sets": daily_sets,
        "weekly_sets": weekly_sets,
        "total_sets": len(rows),
    })


# --- bedtime vs recovery scatter ---
@app.route("/api/bedtime/<int:days>")
def bedtime(days):
    since = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = q(
        "SELECT sp.day, sp.bedtime_start, sp.average_hrv, sp.lowest_heart_rate,"
        " sp.total_sleep_duration, sp.efficiency,"
        " ROUND(sp.rem_sleep_duration * 100.0 / sp.total_sleep_duration, 1) AS rem_pct,"
        " ROUND(sp.deep_sleep_duration * 100.0 / sp.total_sleep_duration, 1) AS deep_pct,"
        " ds.score AS sleep_score, dr.score AS readiness_score"
        " FROM sleep_periods sp"
        " LEFT JOIN daily_sleep ds ON ds.day = sp.day"
        " LEFT JOIN daily_readiness dr ON dr.day = sp.day"
        " WHERE sp.type = 'long_sleep' AND sp.day > ?"
        " ORDER BY sp.day",
        (since,)
    )
    points = []
    for r in rows:
        if not r["bedtime_start"]:
            continue
        bt = parse_dt(r["bedtime_start"]).astimezone(TZ)
        # Bedtime as decimal hour (e.g. 23.5 = 11:30 PM, 1.5 = 1:30 AM next day)
        bed_hour = bt.hour + bt.minute / 60
        # Normalize: if before 6 PM, treat as next-day (add 24) for sorting
        if bed_hour < 18:
            bed_hour += 24
        points.append({
            "day": r["day"],
            "bed_hour": round(bed_hour, 2),
            "bed_time": bt.strftime("%-I:%M %p"),
            "hrv": r["average_hrv"],
            "lowest_hr": r["lowest_heart_rate"],
            "sleep_hrs": round(r["total_sleep_duration"] / 3600, 1) if r["total_sleep_duration"] else None,
            "efficiency": r["efficiency"],
            "rem_pct": r["rem_pct"],
            "deep_pct": r["deep_pct"],
            "sleep_score": r["sleep_score"],
            "readiness": r["readiness_score"],
        })
    return jsonify({"points": points})


@app.route("/status")
def status_page():
    return render_template("status.html", name=DISPLAY_NAME)


@app.route("/api/leveling")
def leveling_api():
    from leveling import compute_snapshot
    return jsonify(compute_snapshot())



@app.route("/correlations")
def correlations_page():
    return render_template("correlations.html", name=DISPLAY_NAME)


@app.route("/analytics")
def analytics_page():
    return render_template("analytics.html", name=DISPLAY_NAME)


def build_daily_feature_matrix():
    sleep = {r["day"]: r for r in q(
        "SELECT day, average_hrv, lowest_heart_rate, average_heart_rate,"
        " total_sleep_duration, efficiency, latency,"
        " ROUND(deep_sleep_duration * 100.0 / NULLIF(total_sleep_duration, 0), 1) AS deep_pct,"
        " ROUND(rem_sleep_duration * 100.0 / NULLIF(total_sleep_duration, 0), 1) AS rem_pct,"
        " bedtime_start"
        " FROM sleep_periods WHERE type = 'long_sleep' ORDER BY day"
    )}

    sleep_scores = {r["day"]: r["score"] for r in q("SELECT day, score FROM daily_sleep")}
    readiness = {r["day"]: r for r in q(
        "SELECT day, score, temperature_deviation FROM daily_readiness"
    )}
    activity = {r["day"]: r for r in q(
        "SELECT day, score, steps, active_calories, sedentary_time FROM daily_activity"
    )}
    spo2 = {r["day"]: r["spo2_average"] for r in q("SELECT day, spo2_average FROM daily_spo2")}
    cardio_age = {r["day"]: r["vascular_age"] for r in q(
        "SELECT day, vascular_age FROM daily_cardiovascular_age WHERE vascular_age IS NOT NULL"
    )}
    body_comp = latest_body_composition_by_day()

    meals_by_day = {}
    meal_timing = {}
    for r in q(
        "SELECT substr(logged_at,1,10) AS day, logged_at,"
        " SUM(calories) AS cal, SUM(protein_g) AS protein, SUM(carbs_g) AS carbs,"
        " SUM(fat_g) AS fat, SUM(sat_fat_g) AS sat_fat, SUM(sugar_g) AS sugar,"
        " SUM(fiber_g) AS fiber, SUM(sodium_mg) AS sodium,"
        " SUM(omega3_g) AS omega3, SUM(magnesium_mg) AS magnesium,"
        " SUM(potassium_mg) AS potassium, SUM(vitamin_d_mcg) AS vitamin_d,"
        " SUM(iron_mg) AS iron, SUM(b12_mcg) AS b12,"
        " SUM(vitamin_c_mg) AS vitamin_c, SUM(zinc_mg) AS zinc,"
        " SUM(vitamin_e_mg) AS vitamin_e, SUM(vitamin_b6_mg) AS vitamin_b6,"
        " SUM(folate_mcg) AS folate,"
        " COUNT(*) AS meal_count"
        " FROM meals GROUP BY substr(logged_at,1,10)"
    ):
        meals_by_day[r["day"]] = r
    # First and last meal times
    for r in q(
        "SELECT substr(logged_at,1,10) AS day,"
        " MIN(logged_at) AS first_meal, MAX(logged_at) AS last_meal"
        " FROM meals GROUP BY substr(logged_at,1,10)"
    ):
        try:
            first = parse_dt(r["first_meal"]).astimezone(TZ)
            last = parse_dt(r["last_meal"]).astimezone(TZ)
            meal_timing[r["day"]] = {
                "first_meal_hour": round(first.hour + first.minute / 60, 2),
                "last_meal_hour": round(last.hour + last.minute / 60, 2),
                "eating_window_hours": round((last - first).total_seconds() / 3600, 2),
            }
        except Exception:
            pass

    workouts_by_day = {r["day"]: r for r in q(
        "SELECT day, COUNT(*) AS workout_count,"
        " SUM(duration) AS workout_duration,"
        " SUM(calories) AS workout_calories"
        " FROM workouts GROUP BY day"
    )}
    sets_by_day = {r["workout_day"]: r for r in q(
        "SELECT workout_day, COUNT(*) AS strength_sets"
        " FROM workout_sets GROUP BY workout_day"
    )}

    subs_by_day = {}
    for r in q(
        "SELECT substr(logged_at,1,10) AS day, substance, COUNT(*) AS cnt,"
        " MAX(logged_at) AS last_use,"
        " SUM(amount_value) AS total_amount,"
        " SUM(amount_value * potency_pct / 100.0) AS active_dose"
        " FROM substances GROUP BY substr(logged_at,1,10), substance"
    ):
        if r["day"] not in subs_by_day:
            subs_by_day[r["day"]] = {}
        subs_by_day[r["day"]][r["substance"]] = {
            "count": r["cnt"], "last_use": r["last_use"],
            "total_amount": r["total_amount"], "active_dose": r["active_dose"],
        }

    travel_by_day = {r["day"]: r for r in q(
        "SELECT day, COUNT(*) AS travel_count,"
        " SUM(hours) AS travel_hours,"
        " SUM(CASE WHEN travel_type = 'flight' THEN hours ELSE 0 END) AS flight_hours,"
        " MAX(ABS(timezone_shift_hours)) AS timezone_shift_abs"
        " FROM travel GROUP BY day"
    )}

    all_days = sorted(set(
        set(sleep) | set(sleep_scores) | set(readiness) | set(activity) | set(travel_by_day) | set(body_comp)
    ))

    features = [
        "sleep_score", "readiness_score", "activity_score",
        "hrv", "lowest_hr", "avg_hr",
        "sleep_hours", "efficiency", "latency_min",
        "deep_pct", "rem_pct",
        "bedtime_hour", "temp_deviation",
        "steps", "active_cal", "sedentary_hrs",
        "spo2", "vascular_age",
        *BODY_COMP_FIELDS,
        "calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "sat_fat_g",
        "sodium_mg", "potassium_mg", "magnesium_mg",
        "meal_count", "first_meal_hour", "last_meal_hour", "eating_window_hours",
        "nutrition_score",
        "workout_count", "workout_minutes", "workout_calories", "strength_sets",
        "weed_count", "weed_g", "thc_mg",
        "nicotine_count", "nicotine_mg",
        "alcohol_count", "alcohol_ml", "pure_alcohol_ml",
        "travel_count", "travel_hours", "flight_hours", "timezone_shift_abs",
        "big_timezone_shift",
        "travel_yesterday", "travel_hours_yesterday", "flight_hours_yesterday",
        "timezone_shift_abs_yesterday", "big_timezone_shift_yesterday",
        "days_since_travel", "post_travel_1_3d",
    ]

    travel_days = sorted(day for day, values in travel_by_day.items() if (values.get("travel_count") or 0) > 0)

    rows = []
    for day in all_days:
        row = {}
        s = sleep.get(day, {})
        r_data = readiness.get(day, {})
        a = activity.get(day, {})
        m = meals_by_day.get(day, {})
        mt = meal_timing.get(day, {})
        w = workouts_by_day.get(day, {})
        ws = sets_by_day.get(day, {})
        sb = subs_by_day.get(day, {})
        tr = travel_by_day.get(day, {})
        bc = body_comp.get(day, {})

        row["sleep_score"] = sleep_scores.get(day)
        row["readiness_score"] = r_data.get("score") if isinstance(r_data, dict) else None
        row["activity_score"] = a.get("score") if isinstance(a, dict) else None
        row["hrv"] = s.get("average_hrv")
        row["lowest_hr"] = s.get("lowest_heart_rate")
        row["avg_hr"] = s.get("average_heart_rate")
        row["sleep_hours"] = round(s["total_sleep_duration"] / 3600, 2) if s.get("total_sleep_duration") else None
        row["efficiency"] = s.get("efficiency")
        row["latency_min"] = round(s["latency"] / 60, 1) if s.get("latency") else None
        row["deep_pct"] = s.get("deep_pct")
        row["rem_pct"] = s.get("rem_pct")

        if s.get("bedtime_start"):
            try:
                bt = parse_dt(s["bedtime_start"]).astimezone(TZ)
                bh = bt.hour + bt.minute / 60
                if bh < 18:
                    bh += 24
                row["bedtime_hour"] = round(bh, 2)
            except Exception:
                row["bedtime_hour"] = None
        else:
            row["bedtime_hour"] = None

        row["temp_deviation"] = r_data.get("temperature_deviation") if isinstance(r_data, dict) else None
        row["steps"] = a.get("steps") if isinstance(a, dict) else None
        row["active_cal"] = a.get("active_calories") if isinstance(a, dict) else None
        row["sedentary_hrs"] = round(a["sedentary_time"] / 3600, 1) if isinstance(a, dict) and a.get("sedentary_time") else None
        row["spo2"] = spo2.get(day)
        row["vascular_age"] = cardio_age.get(day)
        for field in BODY_COMP_FIELDS:
            row[field] = bc.get(field)
        row["calories"] = m.get("cal")
        row["protein_g"] = m.get("protein")
        row["carbs_g"] = m.get("carbs")
        row["fat_g"] = m.get("fat")
        row["fiber_g"] = m.get("fiber")
        row["sugar_g"] = m.get("sugar")
        row["sat_fat_g"] = m.get("sat_fat")
        row["sodium_mg"] = m.get("sodium")
        row["potassium_mg"] = m.get("potassium")
        row["magnesium_mg"] = m.get("magnesium")
        row["meal_count"] = m.get("meal_count")
        row["first_meal_hour"] = mt.get("first_meal_hour")
        row["last_meal_hour"] = mt.get("last_meal_hour")
        row["eating_window_hours"] = mt.get("eating_window_hours")
        if m.get("cal"):
            ns_input = {
                "calories": m.get("cal"), "protein_g": m.get("protein"),
                "fiber_g": m.get("fiber"), "sat_fat_g": m.get("sat_fat"),
                "sugar_g": m.get("sugar"), "sodium_mg": m.get("sodium"),
                "omega3_g": m.get("omega3"), "magnesium_mg": m.get("magnesium"),
                "potassium_mg": m.get("potassium"), "vitamin_d_mcg": m.get("vitamin_d"),
                "iron_mg": m.get("iron"), "b12_mcg": m.get("b12"),
                "vitamin_c_mg": m.get("vitamin_c"), "zinc_mg": m.get("zinc"),
                "vitamin_e_mg": m.get("vitamin_e"), "vitamin_b6_mg": m.get("vitamin_b6"),
                "folate_mcg": m.get("folate"),
            }
            row["nutrition_score"] = compute_nutrition_score(ns_input)
        else:
            row["nutrition_score"] = None
        row["workout_count"] = w.get("workout_count") or 0
        row["workout_minutes"] = round(w["workout_duration"] / 60, 1) if w.get("workout_duration") else 0
        row["workout_calories"] = w.get("workout_calories") or 0
        row["strength_sets"] = ws.get("strength_sets") or 0
        weed = sb.get("weed", {}) if sb else {}
        nic = sb.get("nicotine", {}) if sb else {}
        alc = sb.get("alcohol", {}) if sb else {}
        row["weed_count"] = weed.get("count", 0)
        row["weed_g"] = weed.get("total_amount") or 0
        row["thc_mg"] = round(weed["active_dose"] * 1000, 1) if weed.get("active_dose") else 0
        row["nicotine_count"] = nic.get("count", 0)
        row["nicotine_mg"] = nic.get("total_amount") or 0
        row["alcohol_count"] = alc.get("count", 0)
        row["alcohol_ml"] = alc.get("total_amount") or 0
        row["pure_alcohol_ml"] = round(alc["active_dose"], 1) if alc.get("active_dose") else 0
        row["travel_count"] = tr.get("travel_count") or 0
        row["travel_hours"] = tr.get("travel_hours") or 0
        row["flight_hours"] = tr.get("flight_hours") or 0
        row["timezone_shift_abs"] = tr.get("timezone_shift_abs") or 0
        row["big_timezone_shift"] = 1 if row["timezone_shift_abs"] >= BIG_TIMEZONE_SHIFT_HOURS else 0

        prev_tr = travel_by_day.get(day_offset(day, -1), {})
        row["travel_yesterday"] = 1 if (prev_tr.get("travel_count") or 0) > 0 else 0
        row["travel_hours_yesterday"] = prev_tr.get("travel_hours") or 0
        row["flight_hours_yesterday"] = prev_tr.get("flight_hours") or 0
        row["timezone_shift_abs_yesterday"] = prev_tr.get("timezone_shift_abs") or 0
        row["big_timezone_shift_yesterday"] = (
            1 if row["timezone_shift_abs_yesterday"] >= BIG_TIMEZONE_SHIFT_HOURS else 0
        )

        last_travel_day = None
        for travel_day in travel_days:
            if travel_day < day:
                last_travel_day = travel_day
            else:
                break
        if last_travel_day:
            days_since = (Date.fromisoformat(day) - Date.fromisoformat(last_travel_day)).days
            row["days_since_travel"] = days_since
            row["post_travel_1_3d"] = 1 if 1 <= days_since <= 3 else 0
        else:
            row["days_since_travel"] = None
            row["post_travel_1_3d"] = 0

        rows.append(row)

    return all_days, features, rows


@app.route("/api/correlations")
def correlations_api():
    import numpy as np
    from scipy import stats
    from scipy.stats import entropy as _entropy
    from sklearn.metrics import mutual_info_score

    lag = int(request.args.get("lag", 0))  # 0 = same day, 1 = next day
    all_days, features, rows = build_daily_feature_matrix()

    # Build day-indexed lookup for lagged correlations
    day_rows = {day: row for day, row in zip(all_days, rows)}

    # Compute pairwise Pearson and MI
    n_feat = len(features)
    pearson_matrix = [[None] * n_feat for _ in range(n_feat)]
    pvalue_matrix = [[None] * n_feat for _ in range(n_feat)]
    mi_matrix = [[None] * n_feat for _ in range(n_feat)]
    counts_matrix = [[0] * n_feat for _ in range(n_feat)]
    scatter_data = {}

    for i in range(n_feat):
        for j in range(0 if lag > 0 else i, n_feat):
            # Diagonal on same-day: always 1.0 by definition
            if lag == 0 and i == j:
                pearson_matrix[i][j] = 1.0
                pvalue_matrix[i][j] = 0.0
                mi_matrix[i][j] = 1.0
                counts_matrix[i][j] = sum(1 for d in all_days if day_rows[d][features[i]] is not None)
                continue
            # For lag=0: xi[day] vs xj[day] (symmetric, only compute upper triangle)
            # For lag>0: xi[day] vs xj[day+lag] (not symmetric, compute full matrix)
            pairs = []
            for k in range(len(all_days) - lag):
                day_a = all_days[k]
                day_b = all_days[k + lag]
                va = day_rows[day_a][features[i]]
                vb = day_rows[day_b][features[j]]
                if va is not None and vb is not None:
                    pairs.append((va, vb))
            n = len(pairs)
            counts_matrix[i][j] = n
            if lag == 0:
                counts_matrix[j][i] = n

            if n < 3:
                continue

            a_arr = np.array([p[0] for p in pairs], dtype=float)
            b_arr = np.array([p[1] for p in pairs], dtype=float)

            # Skip if constant
            if np.std(a_arr) == 0 or np.std(b_arr) == 0:
                continue

            # Pearson
            r, p_val = stats.pearsonr(a_arr, b_arr)
            pearson_matrix[i][j] = round(r, 3)
            pvalue_matrix[i][j] = round(p_val, 4)
            if lag == 0:
                pearson_matrix[j][i] = round(r, 3)
                pvalue_matrix[j][i] = round(p_val, 4)

            # MI — quantile-based bins, normalize by min(H(X), H(Y))
            n_bins = max(3, min(10, n // 3))
            a_binned = np.digitize(a_arr, np.quantile(a_arr, np.linspace(0, 1, n_bins + 1)[1:-1]))
            b_binned = np.digitize(b_arr, np.quantile(b_arr, np.linspace(0, 1, n_bins + 1)[1:-1]))
            mi = mutual_info_score(a_binned, b_binned)
            # Normalize by min entropy of the two variables
            ha = _entropy(np.bincount(a_binned))
            hb = _entropy(np.bincount(b_binned))
            min_h = min(ha, hb)
            nmi = round(mi / min_h, 3) if min_h > 0 else 0
            mi_matrix[i][j] = nmi
            if lag == 0:
                mi_matrix[j][i] = nmi

            # Store scatter data for click-through
            scatter_data[f"{i}-{j}"] = {
                "x": a_arr.tolist(), "y": b_arr.tolist(),
                "x_label": features[i], "y_label": features[j],
                "r": round(r, 3), "p": round(p_val, 4), "n": n, "nmi": nmi
            }

    return jsonify({
        "lag": lag,
        "features": features,
        "pearson": pearson_matrix,
        "pvalues": pvalue_matrix,
        "mi": mi_matrix,
        "counts": counts_matrix,
        "scatter": scatter_data,
        "total_days": len(all_days),
    })


@app.route("/api/granger")
def granger_api():
    return analytics_granger()


def _analytics_window(default=180, minimum=7, maximum=365):
    try:
        value = int(request.args.get("days", default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


@app.route("/api/analytics/change-points")
def analytics_change_points():
    metric = request.args.get("metric", "hrv")
    if metric not in ANALYTICS_METRICS:
        return jsonify({"error": "unknown metric", "available_metrics": ANALYTICS_METRICS}), 400
    days = _analytics_window()
    all_days, _, rows = build_daily_feature_matrix()
    cutoff = datetime.now(TZ).date() - timedelta(days=days - 1)
    selected = [
        (day, row.get(metric))
        for day, row in zip(all_days, rows)
        if Date.fromisoformat(day) >= cutoff
    ]
    meta = ANALYTICS_METRICS[metric]
    result = detect_change_points(
        [item[0] for item in selected],
        [item[1] for item in selected],
        higher_is_better=meta["higher_is_better"],
    )
    result.update(metric=metric, label=meta["label"], unit=meta["unit"], days=days)
    result["available_metrics"] = ANALYTICS_METRICS
    return jsonify(result)


@app.route("/api/analytics/sleep-regularity")
def analytics_sleep_regularity():
    days = _analytics_window(default=30, minimum=7, maximum=180)
    since = (datetime.now(TZ).date() - timedelta(days=days + 2)).isoformat()
    periods = q(
        "SELECT day, type, bedtime_start, sleep_phase_5_min"
        " FROM sleep_periods WHERE day >= ? AND sleep_phase_5_min IS NOT NULL"
        " ORDER BY bedtime_start",
        (since,),
    )
    result = compute_sleep_regularity(periods, tz_name=str(TZ), max_days=days)
    result["days"] = days
    return jsonify(result)


@app.route("/api/analytics/circadian")
def analytics_circadian():
    days = _analytics_window(default=90, minimum=14, maximum=180)
    since_local = (datetime.now(TZ) - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    since_utc = since_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    readings = q(
        "SELECT timestamp, bpm, source FROM heartrate"
        " WHERE timestamp >= ? AND source NOT IN ('workout', 'live')"
        " ORDER BY timestamp",
        (since_utc,),
    )
    result = fit_cosinor(readings, tz_name=str(TZ))
    result["window_days"] = days
    result["trend"] = cosinor_trend(readings, tz_name=str(TZ))
    return jsonify(result)


@app.route("/api/analytics/granger")
def analytics_granger():
    model_name = request.args.get("model", "all")
    if model_name != "all" and model_name not in VAR_MODELS:
        return jsonify({"error": "unknown model", "available_models": VAR_MODELS}), 400
    try:
        max_lag = max(1, min(int(request.args.get("max_lag", 3)), 3))
    except (TypeError, ValueError):
        max_lag = 3
    all_days, _, rows = build_daily_feature_matrix()
    result = run_var_analysis(all_days, rows, model_name=model_name, max_lag=max_lag)
    result["available_models"] = {
        key: {"label": value["label"], "variables": value["variables"]}
        for key, value in VAR_MODELS.items()
    }
    return jsonify(result)


@app.route("/api/analytics/impulse-response")
def analytics_impulse_response():
    model_name = request.args.get("model", "recovery")
    predictor = request.args.get("predictor")
    outcome = request.args.get("outcome")
    if model_name not in VAR_MODELS or not predictor or not outcome:
        return jsonify({"error": "model, predictor, and outcome are required"}), 400
    all_days, _, rows = build_daily_feature_matrix()
    result = run_var_analysis(all_days, rows, model_name=model_name)
    match = next((item for item in result["results"]
                  if item["predictor"] == predictor and item["outcome"] == outcome), None)
    if not match:
        return jsonify({"error": "requested response is not available", "model": model_name}), 404
    return jsonify({
        "method": "non-orthogonalized standardized VAR impulse response",
        "model": model_name,
        "predictor": predictor,
        "outcome": outcome,
        "supported": match["supported"],
        "direction": match["direction"],
        "cumulative_response": match["cumulative_response"],
        "irf": match["irf"],
        "diagnostics": {
            "q": match["q"],
            "prediction_gain_pct": match["prediction_gain_pct"],
            "stable": match["stable"],
            "whiteness_p": match["whiteness_p"],
        },
    })


if __name__ == "__main__":
    print("→  http://localhost:8000")
    app.run(host="0.0.0.0", port=8000, debug=False)
