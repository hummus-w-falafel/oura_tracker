"""
Solo Leveling — Health RPG stat engine.
Computes VIT/STR/END/NUT/DIS stats from DB data, daily XP, total XP, and level.

The thresholds, targets, and weightings used to score each stat are defaults.
Adjust them to fit the user's profile.
"""

import math
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from db import get_conn
from nutrition import compute_nutrition_score
from profile_targets import get_targets


# ── Utilities ────────────────────────────────────────────────────────────────

def _days_back(d, n):
    """Return date string n days before d."""
    return (date.fromisoformat(d) - timedelta(days=n)).isoformat()


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _parse_bedtime_hour(bedtime_str):
    """Parse bedtime string to decimal hour, handling midnight crossover."""
    TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/Toronto"))
    try:
        t = datetime.fromisoformat(bedtime_str).astimezone(TZ)
        h = t.hour + t.minute / 60
        if h < 18:
            h += 24
        return h
    except Exception:
        return None


def _stddev(values):
    if len(values) < 2:
        return 0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


# ── Stat: VIT (Vitality) ────────────────────────────────────────────────────

def compute_vit(conn, today):
    """Sleep score 30%, readiness 30%, HRV vs baseline 25%, RHR vs baseline 15%."""
    d7 = _days_back(today, 7)
    d37 = _days_back(today, 37)

    # Sleep & readiness scores (today only, already 0-100)
    sleep_row = conn.execute(
        "SELECT score FROM daily_sleep WHERE day = ? AND score IS NOT NULL", (today,)
    ).fetchone()

    readiness_row = conn.execute(
        "SELECT score FROM daily_readiness WHERE day = ? AND score IS NOT NULL", (today,)
    ).fetchone()

    # HRV: 7-day avg vs 30-day baseline
    hrv_7d = [r[0] for r in conn.execute(
        "SELECT average_hrv FROM sleep_periods WHERE type='long_sleep' AND day > ? AND day <= ? AND average_hrv IS NOT NULL",
        (d7, today)
    ).fetchall()]

    hrv_baseline = [r[0] for r in conn.execute(
        "SELECT average_hrv FROM sleep_periods WHERE type='long_sleep' AND day > ? AND day <= ? AND average_hrv IS NOT NULL",
        (d37, d7)
    ).fetchall()]

    # Resting HR: 7-day avg vs 30-day baseline (lower = better, so invert)
    rhr_7d = [r[0] for r in conn.execute(
        "SELECT lowest_heart_rate FROM sleep_periods WHERE type='long_sleep' AND day > ? AND day <= ? AND lowest_heart_rate IS NOT NULL",
        (d7, today)
    ).fetchall()]

    rhr_baseline = [r[0] for r in conn.execute(
        "SELECT lowest_heart_rate FROM sleep_periods WHERE type='long_sleep' AND day > ? AND day <= ? AND lowest_heart_rate IS NOT NULL",
        (d37, d7)
    ).fetchall()]

    components = {}
    raw = {}

    # Sleep score component (today)
    if sleep_row:
        components["sleep"] = sleep_row[0]
        raw["sleep"] = f"{sleep_row[0]}/100"
    else:
        components["sleep"] = None

    # Readiness component (today)
    if readiness_row:
        components["readiness"] = readiness_row[0]
        raw["readiness"] = f"{readiness_row[0]}/100"
    else:
        components["readiness"] = None

    # HRV vs baseline component
    if hrv_7d:
        avg_7d = sum(hrv_7d) / len(hrv_7d)
        if hrv_baseline and len(hrv_baseline) >= 3:
            bl_avg = sum(hrv_baseline) / len(hrv_baseline)
            bl_std = _stddev(hrv_baseline) or 1
            z = (avg_7d - bl_avg) / bl_std
            components["hrv"] = _clamp(50 + z * 25)
            raw["hrv"] = f"7d: {avg_7d:.0f}ms, baseline: {bl_avg:.0f}ms, z={z:+.1f}"
        else:
            # No baseline yet — score raw HRV: 40=20, 55=50, 70=80, 85+=100
            components["hrv"] = _clamp((avg_7d - 25) * 100 / 60)
            raw["hrv"] = f"7d avg: {avg_7d:.0f}ms (no baseline yet)"
    else:
        components["hrv"] = None

    # RHR vs baseline component (inverted — lower is better)
    if rhr_7d:
        avg_7d = sum(rhr_7d) / len(rhr_7d)
        if rhr_baseline and len(rhr_baseline) >= 3:
            bl_avg = sum(rhr_baseline) / len(rhr_baseline)
            bl_std = _stddev(rhr_baseline) or 1
            z = (avg_7d - bl_avg) / bl_std
            components["rhr"] = _clamp(50 - z * 25)  # inverted
            raw["rhr"] = f"7d: {avg_7d:.0f}bpm, baseline: {bl_avg:.0f}bpm, z={z:+.1f}"
        else:
            # No baseline — score raw: 45=100, 55=50, 65=0
            components["rhr"] = _clamp((65 - avg_7d) * 100 / 20)
            raw["rhr"] = f"7d avg: {avg_7d:.0f}bpm (no baseline yet)"
    else:
        components["rhr"] = None

    # Weighted average (redistribute weights if components missing)
    weights = {"sleep": 0.30, "readiness": 0.30, "hrv": 0.25, "rhr": 0.15}
    result = _weighted_score(components, weights)
    result["raw"] = raw
    return result


# ── Stat: STR (Strength) ────────────────────────────────────────────────────

def compute_str(conn, today):
    """Progressive overload 35%, training volume 35%, post-workout recovery 30%."""
    d7 = _days_back(today, 7)
    d21 = _days_back(today, 21)
    d37 = _days_back(today, 37)

    # Check if any workout data exists
    recent_sets = conn.execute(
        "SELECT * FROM workout_sets WHERE workout_day > ? AND workout_day <= ?",
        (d21, today)
    ).fetchall()

    if not recent_sets:
        return {"score": None, "components": {}, "raw": {}}

    components = {}
    raw = {}

    # Volume (35%): hard sets per week — 10-20 sets/wk is optimal range
    sets_7d = conn.execute(
        "SELECT workout_day, exercise, reps, weight_lbs, weight_per_hand FROM workout_sets WHERE workout_day > ? AND workout_day <= ?",
        (d7, today)
    ).fetchall()

    if sets_7d:
        total_sets = len(sets_7d)
        session_days = len(set(r["workout_day"] for r in sets_7d))
        # Scale: 0=0, 10=50, 20+=100
        components["volume"] = _clamp(total_sets * 100 / 20)
        raw["volume"] = f"{total_sets} sets in {session_days} sessions (target: 10-20/wk)"
    else:
        components["volume"] = 0
        raw["volume"] = "No sets in last 7d"

    # Progressive overload (35%): compare recent 2 weeks vs prior 2 weeks
    d14 = _days_back(today, 14)
    recent = conn.execute(
        "SELECT exercise, MAX(reps * weight_lbs * (1 + weight_per_hand)) as max_vol "
        "FROM workout_sets WHERE workout_day > ? AND workout_day <= ? GROUP BY exercise",
        (d14, today)
    ).fetchall()

    older = conn.execute(
        "SELECT exercise, MAX(reps * weight_lbs * (1 + weight_per_hand)) as max_vol "
        "FROM workout_sets WHERE workout_day > ? AND workout_day <= ? GROUP BY exercise",
        (d37, d14)
    ).fetchall()

    if recent and older:
        older_map = {r["exercise"]: r["max_vol"] for r in older}
        ratios = []
        for r in recent:
            if r["exercise"] in older_map and older_map[r["exercise"]] > 0:
                ratios.append(r["max_vol"] / older_map[r["exercise"]])
        if ratios:
            avg_ratio = sum(ratios) / len(ratios)
            # ratio 1.0 = maintaining = 50, 1.1 = 55, 0.9 = 45
            components["overload"] = _clamp(avg_ratio * 50)
            raw["overload"] = f"ratio: {avg_ratio:.2f}x vs prior 2wk"
        else:
            components["overload"] = 50  # no comparison possible, neutral
            raw["overload"] = "No overlapping exercises to compare"
    else:
        components["overload"] = 50  # not enough history, neutral
        raw["overload"] = "Default 50 — not enough history"

    # Post-workout recovery (30%): next-day readiness/HRV after training
    workout_days = list(set(r["workout_day"] for r in sets_7d)) if sets_7d else []
    recovery_scores = []
    for wd in workout_days:
        next_day = (date.fromisoformat(wd) + timedelta(days=1)).isoformat()
        r = conn.execute(
            "SELECT score FROM daily_readiness WHERE day = ? AND score IS NOT NULL",
            (next_day,)
        ).fetchone()
        if r:
            recovery_scores.append(r[0])

    if recovery_scores:
        components["recovery"] = sum(recovery_scores) / len(recovery_scores)
        raw["recovery"] = f"Next-day readiness: {', '.join(str(int(s)) for s in recovery_scores)}"
    else:
        components["recovery"] = None

    weights = {"overload": 0.35, "volume": 0.35, "recovery": 0.30}
    result = _weighted_score(components, weights)
    result["raw"] = raw
    return result


# ── Stat: END (Endurance) ───────────────────────────────────────────────────

def compute_end(conn, today):
    """Steps 30%, active cal 20%, resting HR trend 25%, VO2 max 25%."""
    targets = get_targets()
    d7 = _days_back(today, 7)
    d90 = _days_back(today, 90)

    components = {}
    raw = {}

    # Steps (30%): 0=0, 4000=50, 8000+=100
    steps = [r[0] for r in conn.execute(
        "SELECT steps FROM daily_activity WHERE day > ? AND day <= ? AND steps IS NOT NULL",
        (d7, today)
    ).fetchall()]
    if steps:
        avg_steps = sum(steps) / len(steps)
        step_target = targets["steps"]
        components["steps"] = _clamp(avg_steps * 100 / step_target)
        raw["steps"] = f"7d avg: {avg_steps:.0f} steps (target: {step_target:.0f})"
    else:
        components["steps"] = None

    # Active calories (20%): 0=0, 200=50, 400+=100
    cal = [r[0] for r in conn.execute(
        "SELECT active_calories FROM daily_activity WHERE day > ? AND day <= ? AND active_calories IS NOT NULL",
        (d7, today)
    ).fetchall()]
    if cal:
        avg_cal = sum(cal) / len(cal)
        active_cal_target = targets["active_calories"]
        components["active_cal"] = _clamp(avg_cal * 100 / active_cal_target)
        raw["active_cal"] = f"7d avg: {avg_cal:.0f} cal (target: {active_cal_target:.0f})"
    else:
        components["active_cal"] = None

    # Resting HR long-term trend (25%): 7d avg vs 90d avg, lower = better
    rhr_7d = [r[0] for r in conn.execute(
        "SELECT lowest_heart_rate FROM sleep_periods WHERE type='long_sleep' AND day > ? AND day <= ? AND lowest_heart_rate IS NOT NULL",
        (d7, today)
    ).fetchall()]
    rhr_90d = [r[0] for r in conn.execute(
        "SELECT lowest_heart_rate FROM sleep_periods WHERE type='long_sleep' AND day > ? AND day <= ? AND lowest_heart_rate IS NOT NULL",
        (d90, d7)
    ).fetchall()]

    if rhr_7d:
        avg_7d = sum(rhr_7d) / len(rhr_7d)
        if rhr_90d and len(rhr_90d) >= 5:
            bl_avg = sum(rhr_90d) / len(rhr_90d)
            bl_std = _stddev(rhr_90d) or 1
            z = (avg_7d - bl_avg) / bl_std
            components["rhr_trend"] = _clamp(50 - z * 25)
            raw["rhr_trend"] = f"7d: {avg_7d:.0f}bpm, 90d baseline: {bl_avg:.0f}bpm"
        else:
            components["rhr_trend"] = _clamp((65 - avg_7d) * 100 / 20)
            raw["rhr_trend"] = f"7d avg: {avg_7d:.0f}bpm (no 90d baseline yet)"
    else:
        components["rhr_trend"] = None

    # VO2 max (25%): most recent value on or before this day
    vo2 = conn.execute(
        "SELECT vo2_max FROM vo2_max WHERE day <= ? ORDER BY day DESC LIMIT 1",
        (today,)
    ).fetchone()
    if vo2 and vo2[0]:
        # Male 25-30: 30=poor(20), 40=average(50), 50=good(80), 55+=100
        components["vo2"] = _clamp((vo2[0] - 25) * 100 / 30)
        raw["vo2"] = f"VO2 max: {vo2[0]:.1f} ml/kg/min"
    else:
        components["vo2"] = None
        raw["vo2"] = "No VO2 data"

    weights = {"steps": 0.30, "active_cal": 0.20, "rhr_trend": 0.25, "vo2": 0.25}
    result = _weighted_score(components, weights)
    result["raw"] = raw
    return result


# ── Stat: NUT (Nutrition) ───────────────────────────────────────────────────

def compute_nut(conn, today):
    """
    Nutrition score + protein distribution. On training days, adds calorie adequacy.
    - Non-training days: nutrition 70%, protein_dist 30%
    - Training days:     nutrition 50%, protein_dist 30%, calorie_adequacy 20%
    """
    import math
    targets = get_targets()
    components = {}
    raw = {}
    protein_target = targets["protein_g"]
    protein_trigger = targets["protein_trigger_g"]
    calorie_target = targets["calories"]

    # Check if today is a training day (kettlebell or rowing only — not Oura auto-detected walks)
    is_training_day = conn.execute(
        "SELECT COUNT(*) FROM workouts WHERE day = ? AND activity IN ('kettlebell', 'rowing')",
        (today,)
    ).fetchone()[0] > 0

    # Nutrition score
    row = conn.execute(
        "SELECT SUM(calories) as calories, SUM(protein_g) as protein_g,"
        " SUM(fiber_g) as fiber_g, SUM(sat_fat_g) as sat_fat_g,"
        " SUM(sugar_g) as sugar_g, SUM(sodium_mg) as sodium_mg,"
        " SUM(omega3_g) as omega3_g, SUM(magnesium_mg) as magnesium_mg,"
        " SUM(potassium_mg) as potassium_mg, SUM(vitamin_d_mcg) as vitamin_d_mcg,"
        " SUM(iron_mg) as iron_mg, SUM(b12_mcg) as b12_mcg,"
        " SUM(vitamin_c_mg) as vitamin_c_mg, SUM(zinc_mg) as zinc_mg,"
        " SUM(vitamin_e_mg) as vitamin_e_mg, SUM(vitamin_b6_mg) as vitamin_b6_mg,"
        " SUM(folate_mcg) as folate_mcg"
        " FROM meals WHERE substr(logged_at,1,10) = ?", (today,)
    ).fetchone()
    total_calories = row["calories"] if row else None
    if row and row["calories"]:
        s = compute_nutrition_score(dict(row))
        if s is not None:
            components["nutrition"] = s
            raw["nutrition"] = f"{s:.0f}/100"
        else:
            components["nutrition"] = None
    else:
        components["nutrition"] = None

    # Protein distribution
    meals = conn.execute(
        "SELECT protein_g FROM meals WHERE substr(logged_at,1,10) = ? AND protein_g IS NOT NULL",
        (today,)
    ).fetchall()
    if meals:
        proteins = [m[0] for m in meals]
        hits = sum(1 for p in proteins if p >= protein_trigger)
        total = sum(proteins)
        adequate = total >= protein_target
        if hits >= 2:
            score = 100 if adequate else 80
        elif hits == 1:
            score = 60 if adequate else 40
        else:
            score = min(total / protein_target * 40, 40)
        components["protein_dist"] = score
        raw["protein_dist"] = f"{hits} meals with {protein_trigger:.0f}g+, {total:.0f}g total (target: 2+ meals, {protein_target:.0f}g)"
    else:
        components["protein_dist"] = None

    # Calorie adequacy — training days only, no penalty above 2300
    if is_training_day:
        if total_calories:
            if total_calories >= calorie_target:
                cal_score = 100.0
            else:
                # Steep penalty for undereating on training days
                cal_score = 100.0 * math.exp(-0.5 * ((total_calories - calorie_target) / 300) ** 2)
            components["calorie_adequacy"] = cal_score
            raw["calorie_adequacy"] = f"{total_calories:.0f} kcal / {calorie_target:.0f} target (training day)"
        else:
            components["calorie_adequacy"] = 0
            raw["calorie_adequacy"] = "no meals logged (training day)"

    # Always include calorie_adequacy in components so the dashboard can show it
    # On non-training days it's null (shown as —)
    if not is_training_day:
        components["calorie_adequacy"] = None

    if is_training_day:
        weights = {"nutrition": 0.50, "protein_dist": 0.30, "calorie_adequacy": 0.20}
    else:
        weights = {"nutrition": 0.70, "protein_dist": 0.30}

    result = _weighted_score(components, weights)
    result["raw"] = raw
    result["is_training_day"] = is_training_day
    return result


# ── Stat: DIS (Discipline) ──────────────────────────────────────────────────

def compute_dis(conn, today):
    """Bedtime consistency 40%, training adherence 30%, fasting window 30%."""
    targets = get_targets()
    d7 = _days_back(today, 7)
    components = {}
    raw = {}

    # Bedtime consistency (40%): stddev of bedtime hour over 7 days
    bedtimes = conn.execute(
        "SELECT bedtime_start FROM sleep_periods WHERE type='long_sleep' AND day > ? AND day <= ? AND bedtime_start IS NOT NULL",
        (d7, today)
    ).fetchall()

    bt_hours = [_parse_bedtime_hour(r[0]) for r in bedtimes]
    bt_hours = [h for h in bt_hours if h is not None]

    if len(bt_hours) >= 2:
        std = _stddev(bt_hours)
        avg_h = sum(bt_hours) / len(bt_hours)
        hr = int(avg_h % 24)
        mn = int((avg_h % 1) * 60)
        # stddev < 0.5h = 100, 1h = 83, 2h = 33, 3h+ = 0
        components["bedtime"] = _clamp(100 - std * 33)
        raw["bedtime"] = f"avg: {hr}:{mn:02d}, stddev: {std:.1f}h ({len(bt_hours)} nights)"
    elif len(bt_hours) == 1:
        components["bedtime"] = 50  # can't measure consistency with one point
        raw["bedtime"] = "Only 1 night — can't measure consistency"
    else:
        components["bedtime"] = None

    # Training adherence (30%): sessions in last 7 days vs target of 3
    sessions = conn.execute(
        "SELECT COUNT(DISTINCT workout_day) FROM workout_sets WHERE workout_day > ? AND workout_day <= ?",
        (d7, today)
    ).fetchone()[0]
    training_target = targets["training_sessions_per_week"]
    components["training"] = _clamp(sessions * 100 / training_target)
    raw["training"] = f"{sessions}/{training_target:g} sessions in last 7d"

    # Fasting window adherence (30%): avg fasting window over 7 days
    fast_scores = []
    fast_windows = []
    for day_offset in range(7):
        d = (date.fromisoformat(today) - timedelta(days=day_offset)).isoformat()
        r = conn.execute(
            "SELECT MIN(logged_at) as first_meal, MAX(logged_at) as last_meal"
            " FROM meals WHERE substr(logged_at,1,10) = ?", (d,)
        ).fetchone()
        if r and r["first_meal"] and r["last_meal"] and r["first_meal"] != r["last_meal"]:
            try:
                first = datetime.fromisoformat(r["first_meal"])
                last = datetime.fromisoformat(r["last_meal"])
                eating_hrs = (last - first).total_seconds() / 3600
                fasting_hrs = 24 - eating_hrs
                fasting_target = targets["fasting_hours"]
                # Default target: 16h+ = 100, 14h = 66, 12h = 33, 10h = 0
                fast_scores.append(_clamp((fasting_hrs - 10) * 100 / max(fasting_target - 10, 1)))
                fast_windows.append(fasting_hrs)
            except Exception:
                pass

    if fast_scores:
        components["fasting"] = sum(fast_scores) / len(fast_scores)
        avg_fast = sum(fast_windows) / len(fast_windows)
        raw["fasting"] = f"avg: {avg_fast:.1f}h fasting (target: {targets['fasting_hours']:g}h, {len(fast_windows)} days)"
    else:
        components["fasting"] = None

    weights = {"bedtime": 0.40, "training": 0.30, "fasting": 0.30}
    result = _weighted_score(components, weights)
    result["raw"] = raw
    return result


# ── Weighted score helper ───────────────────────────────────────────────────

def _weighted_score(components, weights):
    """Compute weighted average, redistributing weights for None components."""
    active = {k: v for k, v in components.items() if v is not None}
    if not active:
        return {"score": None, "components": components}

    total_weight = sum(weights[k] for k in active)
    score = sum(active[k] * weights[k] / total_weight for k in active)
    return {"score": round(_clamp(score), 1), "components": {k: round(v, 1) if v is not None else None for k, v in components.items()}}


# ── XP & Level Engine ───────────────────────────────────────────────────────

def compute_daily(conn, day):
    """Compute all stats and XP for a single day."""
    vit = compute_vit(conn, day)
    str_ = compute_str(conn, day)
    end = compute_end(conn, day)
    nut = compute_nut(conn, day)
    dis = compute_dis(conn, day)

    stats = {"vit": vit, "str": str_, "end": end, "nut": nut, "dis": dis}

    # XP: each active stat contributes score/5 (max 20 per stat)
    active_stats = {k: v for k, v in stats.items() if v["score"] is not None}
    daily_xp = sum(v["score"] / 5 for v in active_stats.values())
    max_xp = len(active_stats) * 20
    decay = max_xp * 0.5  # 50% of max

    return {
        "stats": stats,
        "daily_xp": round(daily_xp, 1),
        "decay": round(decay, 1),
        "net_xp": round(daily_xp - decay, 1),
    }


def xp_for_level(level):
    """XP required to reach a given level."""
    return 20 * level * level


def level_from_xp(total_xp):
    """Determine level from total XP."""
    level = int(math.sqrt(max(0, total_xp) / 20))
    # Make sure we don't overshoot
    while xp_for_level(level + 1) <= total_xp:
        level += 1
    return max(1, level)


def rank_from_level_and_stats(level, stat_scores):
    """Determine rank from level and minimum stat requirements."""
    ranks = [
        ("S", 41, 75),
        ("A", 31, 65),
        ("B", 21, 55),
        ("C", 11, 45),
        ("D", 6, 30),
        ("E", 1, 0),
    ]
    active_scores = [v for v in stat_scores.values() if v is not None]
    min_stat = min(active_scores) if active_scores else 0

    for rank, min_level, min_req in ranks:
        if level >= min_level and min_stat >= min_req:
            return rank
    return "E"


def compute_snapshot():
    """Main entry point: compute full leveling state."""
    today = date.today().isoformat()

    with get_conn() as conn:
        # Find the earliest day with any data
        earliest = conn.execute(
            "SELECT MIN(day) FROM daily_sleep WHERE score IS NOT NULL"
        ).fetchone()[0]

        if not earliest:
            return {"level": 1, "total_xp": 0, "rank": "E", "today": None}

        # Compute day by day from earliest to today
        total_xp = 0
        current = date.fromisoformat(earliest)
        end = date.fromisoformat(today)
        history = []

        while current <= end:
            day_str = current.isoformat()

            # Check cache for older days (not last 7)
            if (end - current).days > 7:
                cached = conn.execute(
                    "SELECT * FROM leveling_daily_cache WHERE day = ?", (day_str,)
                ).fetchone()
                if cached and cached["daily_xp"] is not None:
                    total_xp = max(0, total_xp + cached["daily_xp"] - cached["decay"])
                    current += timedelta(days=1)
                    continue

            # Compute fresh
            result = compute_daily(conn, day_str)
            total_xp = max(0, total_xp + result["net_xp"])

            # Cache it
            conn.execute(
                "INSERT OR REPLACE INTO leveling_daily_cache "
                "(day, vit_score, str_score, end_score, nut_score, dis_score, daily_xp, decay, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                (day_str,
                 result["stats"]["vit"]["score"],
                 result["stats"]["str"]["score"],
                 result["stats"]["end"]["score"],
                 result["stats"]["nut"]["score"],
                 result["stats"]["dis"]["score"],
                 result["daily_xp"],
                 result["decay"])
            )

            if (end - current).days <= 7:
                history.append({"day": day_str, **result})

            current += timedelta(days=1)

        # Today's detailed stats
        today_result = history[-1] if history else compute_daily(conn, today)

        level = level_from_xp(total_xp)
        xp_current = xp_for_level(level)
        xp_next = xp_for_level(level + 1)

        stat_scores = {k: v["score"] for k, v in today_result["stats"].items()}
        rank = rank_from_level_and_stats(level, stat_scores)

        return {
            "level": level,
            "total_xp": round(total_xp, 1),
            "xp_current_level": xp_current,
            "xp_next_level": xp_next,
            "xp_progress": round(total_xp - xp_current, 1),
            "xp_needed": xp_next - xp_current,
            "rank": rank,
            "today": today_result,
            "history": history,
        }


if __name__ == "__main__":
    import json
    result = compute_snapshot()
    print(json.dumps(result, indent=2))
