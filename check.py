"""
Health snapshot from local DB.
Run sync.py first to ensure data is fresh.

Usage:
  python3 check.py          # last 7 days
  python3 check.py 14       # last 14 days
  python3 check.py --sync   # sync then show 7-day snapshot
"""

import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from db import get_conn, init_db

load_dotenv()
LOCAL_TZ = ZoneInfo(os.getenv("TIMEZONE", "America/Toronto"))


def to_local(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(LOCAL_TZ)
    except Exception:
        return None


def fmt_time(iso_str):
    dt = to_local(iso_str)
    return dt.strftime("%I:%M %p") if dt else "N/A"


def fmt_dt(iso_str):
    dt = to_local(iso_str)
    return dt.strftime("%Y-%m-%d %I:%M %p") if dt else "N/A"


def hm(seconds):
    if seconds is None:
        return "N/A"
    h, m = divmod(int(seconds), 3600)
    mins = m // 60
    return f"{h}h {mins}m"


def score_label(s):
    if s is None: return "N/A"
    if s >= 85: tag = "excellent"
    elif s >= 70: tag = "good"
    elif s >= 60: tag = "fair"
    else: tag = "poor"
    return f"{s}/100 ({tag})"


def main():
    if "--sync" in sys.argv:
        import subprocess
        subprocess.run(["python3", "sync.py"], check=True)
        sys.argv.remove("--sync")

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    since = (date.today() - timedelta(days=days)).isoformat()
    today = date.today().isoformat()

    init_db()

    print(f"\n{'='*62}")
    print(f"  HEALTH SNAPSHOT — {since} → {today}  ({LOCAL_TZ})")
    print(f"{'='*62}")

    with get_conn() as conn:

        # ── Daily overview ────────────────────────────────────────────
        sleep_rows = {r["day"]: r for r in conn.execute(
            "SELECT * FROM daily_sleep WHERE day >= ? ORDER BY day", (since,)
        ).fetchall()}
        ready_rows = {r["day"]: r for r in conn.execute(
            "SELECT * FROM daily_readiness WHERE day >= ? ORDER BY day", (since,)
        ).fetchall()}
        act_rows = {r["day"]: r for r in conn.execute(
            "SELECT * FROM daily_activity WHERE day >= ? ORDER BY day", (since,)
        ).fetchall()}
        stress_rows = {r["day"]: r for r in conn.execute(
            "SELECT * FROM daily_stress WHERE day >= ? AND day_summary IS NOT NULL ORDER BY day", (since,)
        ).fetchall()}

        all_days = sorted(set(list(sleep_rows) + list(ready_rows) + list(act_rows)))

        print("\nDAILY OVERVIEW")
        print("-" * 62)
        for day in all_days:
            print(f"\n  {day}")
            if day in sleep_rows:
                s = sleep_rows[day]
                print(f"    Sleep:     {score_label(s['score'])}  |  "
                      f"deep {s['contrib_deep_sleep'] or '-'}  "
                      f"rem {s['contrib_rem_sleep'] or '-'}  "
                      f"total {s['contrib_total_sleep'] or '-'}")
            if day in ready_rows:
                r = ready_rows[day]
                temp = r["temperature_deviation"]
                temp_s = f"  temp {temp:+.2f}°C" if temp is not None else ""
                print(f"    Readiness: {score_label(r['score'])}  |  "
                      f"rhr {r['contrib_resting_heart_rate'] or '-'}  "
                      f"recovery {r['contrib_recovery_index'] or '-'}{temp_s}")
            if day in act_rows:
                a = act_rows[day]
                print(f"    Activity:  {score_label(a['score'])}  |  "
                      f"steps {a['steps'] or '-'}  "
                      f"active_cal {a['active_calories'] or '-'}")
            if day in stress_rows:
                st = stress_rows[day]
                print(f"    Stress:    [{st['day_summary']}]")

        # ── Detailed sleep (main sleeps only) ─────────────────────────
        sleep_periods = conn.execute(
            "SELECT * FROM sleep_periods WHERE day >= ? AND type='long_sleep' ORDER BY bedtime_start",
            (since,)
        ).fetchall()

        if sleep_periods:
            print(f"\n\nDETAILED SLEEP (main sleeps)")
            print("-" * 62)
            for s in sleep_periods:
                bed = fmt_time(s["bedtime_start"])
                wake = fmt_time(s["bedtime_end"])
                print(f"\n  {s['day']}  |  bed {bed} → wake {wake}")
                print(f"    In bed: {hm(s['time_in_bed'])}   "
                      f"Asleep: {hm(s['total_sleep_duration'])}   "
                      f"Efficiency: {s['efficiency']}%   "
                      f"Latency: {(s['latency'] or 0)//60}m")
                print(f"    Deep: {hm(s['deep_sleep_duration'])}   "
                      f"REM: {hm(s['rem_sleep_duration'])}   "
                      f"Light: {hm(s['light_sleep_duration'])}")
                print(f"    Avg HRV: {s['average_hrv'] or 'N/A'}ms   "
                      f"Lowest HR: {s['lowest_heart_rate'] or 'N/A'} bpm   "
                      f"Avg HR: {round(s['average_heart_rate'],1) if s['average_heart_rate'] else 'N/A'} bpm")
                print(f"    Restless periods: {s['restless_periods'] or 'N/A'}")

        # ── Naps / short sleep events ─────────────────────────────────
        naps = conn.execute(
            "SELECT * FROM sleep_periods WHERE day >= ? AND type != 'long_sleep' ORDER BY bedtime_start",
            (since,)
        ).fetchall()
        if naps:
            print(f"\n\nSHORT SLEEP EVENTS (naps/rest)")
            print("-" * 62)
            for n in naps:
                print(f"  {n['day']}  {fmt_time(n['bedtime_start'])} → {fmt_time(n['bedtime_end'])}  "
                      f"in_bed {hm(n['time_in_bed'])}  asleep {hm(n['total_sleep_duration'])}  "
                      f"eff {n['efficiency']}%")

        # ── Workouts ──────────────────────────────────────────────────
        workouts = conn.execute(
            "SELECT * FROM workouts WHERE day >= ? ORDER BY start_datetime", (since,)
        ).fetchall()
        if workouts:
            print(f"\n\nWORKOUTS")
            print("-" * 62)
            for w in workouts:
                dur = f"{round(w['duration']/60)}min" if w['duration'] else "N/A"
                cal = f"{round(w['calories'])} kcal" if w['calories'] else ""
                dist = f"{w['distance']/1000:.1f}km" if w['distance'] else ""
                print(f"  {fmt_dt(w['start_datetime'])}  "
                      f"{w['activity']}  {w['intensity']}  {dur}  {cal}  {dist}")

        # ── SpO2 ──────────────────────────────────────────────────────
        spo2 = conn.execute(
            "SELECT * FROM daily_spo2 WHERE day >= ? ORDER BY day", (since,)
        ).fetchall()
        if spo2:
            print(f"\n\nSPO2 (blood oxygen during sleep)")
            print("-" * 62)
            for s in spo2:
                bdi = s['breathing_disturbance_index']
                bdi_note = " ⚠ elevated" if bdi and bdi > 10 else ""
                print(f"  {s['day']}  {s['spo2_average']:.1f}%  "
                      f"breathing disturbances: {bdi}{bdi_note}")

        # ── Meals ─────────────────────────────────────────────────────
        meals = conn.execute(
            "SELECT * FROM meals WHERE logged_at >= ? ORDER BY logged_at", (since,)
        ).fetchall()
        if meals:
            print(f"\n\nMEALS LOGGED")
            print("-" * 62)
            for m in meals:
                time_s = fmt_dt(m['logged_at'])
                macros = []
                if m['calories']: macros.append(f"{m['calories']} kcal")
                if m['protein_g']: macros.append(f"P {m['protein_g']}g")
                if m['carbs_g']: macros.append(f"C {m['carbs_g']}g")
                if m['fat_g']: macros.append(f"F {m['fat_g']}g")
                macro_s = "  " + "  ".join(macros) if macros else ""
                notes_s = f"  [{m['notes']}]" if m['notes'] else ""
                print(f"  {time_s}  [{m['meal_type']}]  {m['description']}{macro_s}{notes_s}")
        else:
            print(f"\n\nMEALS LOGGED")
            print("-" * 62)
            print("  No meals logged yet.")

        # ── HR stats ──────────────────────────────────────────────────
        hr_stats = conn.execute("""
            SELECT source,
                   COUNT(*) as n,
                   MIN(bpm) as min_bpm,
                   MAX(bpm) as max_bpm,
                   ROUND(AVG(bpm),1) as avg_bpm
            FROM heartrate
            WHERE timestamp >= ?
            GROUP BY source ORDER BY n DESC
        """, (since + "T00:00:00Z",)).fetchall()
        if hr_stats:
            print(f"\n\nHEART RATE SUMMARY")
            print("-" * 62)
            for r in hr_stats:
                print(f"  [{r['source']:<8}]  n={r['n']:<6}  "
                      f"range {r['min_bpm']}–{r['max_bpm']} bpm  avg {r['avg_bpm']} bpm")

        # ── Summary averages ──────────────────────────────────────────
        avgs = conn.execute("""
            SELECT
                ROUND(AVG(s.score), 0) as avg_sleep,
                ROUND(AVG(r.score), 0) as avg_readiness,
                ROUND(AVG(sp.average_hrv), 0) as avg_hrv,
                ROUND(AVG(sp.lowest_heart_rate), 0) as avg_rhr
            FROM daily_sleep s
            LEFT JOIN daily_readiness r ON s.day = r.day
            LEFT JOIN sleep_periods sp ON s.day = sp.day AND sp.type = 'long_sleep'
            WHERE s.day >= ?
        """, (since,)).fetchone()

        print(f"\n{'='*62}")
        print(f"  {days}-DAY AVERAGES")
        if avgs:
            print(f"  Sleep score:     {avgs['avg_sleep'] or 'N/A'}/100")
            print(f"  Readiness score: {avgs['avg_readiness'] or 'N/A'}/100")
            print(f"  Avg HRV (sleep): {avgs['avg_hrv'] or 'N/A'}ms")
            print(f"  Avg lowest HR:   {avgs['avg_rhr'] or 'N/A'} bpm")
        print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
