"""
Incremental sync from Oura API → local SQLite.

Strategy:
  - sync_state table stores last_synced_at per endpoint
  - Each run fetches from (last_synced - 1 day) to today
  - 1-day overlap catches late-arriving or updated records
  - Upserts on primary key — safe to re-run

Usage:
  python3 sync.py           # sync all endpoints
  python3 sync.py --full    # force full re-sync from ring start date
  python3 sync.py --status  # show sync state only
"""

import argparse
import sys
from datetime import date, datetime, timedelta

from db import (
    get_conn, init_db, get_sync_state, set_sync_state,
    upsert_daily_sleep, upsert_daily_readiness, upsert_daily_activity,
    upsert_daily_stress, upsert_daily_spo2, upsert_daily_cardiovascular_age,
    upsert_daily_resilience, upsert_vo2_max, upsert_sleep_period,
    upsert_heartrate_batch, upsert_workout, upsert_session, upsert_sleep_time,
)
from oura_client import OuraClient

# Earliest date to use for full sync — set to when ring was first worn
RING_START_DATE = "2026-03-22"


def date_to_iso(d) -> str:
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def sync_start_date(endpoint: str, full: bool) -> str:
    """Determine start date for this sync run."""
    if full:
        return RING_START_DATE
    last = get_sync_state(endpoint)
    if not last:
        return RING_START_DATE
    # 1-day overlap to catch updates
    dt = datetime.fromisoformat(last).date() - timedelta(days=1)
    return date_to_iso(dt)


def sync_heartrate_start(full: bool) -> str:
    """Heartrate uses datetime precision, not date."""
    if full:
        return RING_START_DATE + "T00:00:00Z"
    last = get_sync_state("heartrate")
    if not last:
        return RING_START_DATE + "T00:00:00Z"
    # Oura ring batches HR uploads — data can arrive 12-24h+ after recording.
    # Use 48h overlap to catch late-arriving readings (INSERT OR IGNORE handles dupes).
    dt = datetime.fromisoformat(last) - timedelta(hours=48)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def heartrate_windows(start_dt: str, end_dt: str, max_days: int = 30):
    """Yield API-safe heartrate datetime windows.

    Oura rejects heartrate ranges longer than 30 days, so full syncs need to be
    split while incremental 48-hour syncs usually produce a single window.
    """
    start = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
    step = timedelta(days=max_days)

    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        yield (
            cur.strftime("%Y-%m-%dT%H:%M:%SZ"),
            nxt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        cur = nxt


def run_sync(full: bool = False):
    init_db()
    client = OuraClient()
    now = datetime.utcnow().isoformat()

    results = {}
    # Use tomorrow as end_date to guarantee today is always within the window,
    # regardless of inclusive/exclusive API semantics and local/UTC edge cases.
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    # ── Daily summaries ───────────────────────────────────────────────────────
    endpoints_daily = [
        ("daily_sleep",     client.get_daily_sleep,     upsert_daily_sleep),
        ("daily_readiness", client.get_daily_readiness, upsert_daily_readiness),
        ("daily_activity",  client.get_daily_activity,  upsert_daily_activity),
        ("daily_stress",    client.get_daily_stress,    upsert_daily_stress),
        ("daily_spo2",      client.get_daily_spo2,      upsert_daily_spo2),
        ("daily_cardiovascular_age", client.get_daily_cardiovascular_age, upsert_daily_cardiovascular_age),
        ("daily_resilience", client.get_daily_resilience, upsert_daily_resilience),
    ]

    for ep_name, fetch_fn, upsert_fn in endpoints_daily:
        start = sync_start_date(ep_name, full)
        try:
            records = fetch_fn(start_date=start, end_date=tomorrow)
            with get_conn() as conn:
                for r in records:
                    upsert_fn(conn, r)
                set_sync_state(conn, ep_name, now)
            results[ep_name] = len(records)
        except Exception as e:
            results[ep_name] = f"ERROR: {e}"

    # ── Sleep periods ─────────────────────────────────────────────────────────
    ep = "sleep_periods"
    start = sync_start_date(ep, full)
    # Sleep periods are assigned to wake-up day, but bedtime_start may be the night before.
    # Use 2-day lookback so overnight sleeps (e.g. started Mar 24, woke Mar 25) aren't missed.
    if not full:
        from datetime import date as _date
        start = date_to_iso(datetime.fromisoformat(start).date() - timedelta(days=1))
    try:
        records = client.get_sleep_periods(start_date=start, end_date=tomorrow)
        with get_conn() as conn:
            for r in records:
                upsert_sleep_period(conn, r)
            set_sync_state(conn, ep, now)
        results[ep] = len(records)
    except Exception as e:
        results[ep] = f"ERROR: {e}"

    # ── Heart rate (datetime precision, batch insert) ─────────────────────────
    ep = "heartrate"
    start_dt = sync_heartrate_start(full)
    end_dt = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        records = []
        for window_start, window_end in heartrate_windows(start_dt, end_dt):
            records.extend(client.get_heart_rate(
                start_datetime=window_start,
                end_datetime=window_end,
            ))
        with get_conn() as conn:
            upsert_heartrate_batch(conn, records)
            set_sync_state(conn, ep, now)
        results[ep] = len(records)
    except Exception as e:
        results[ep] = f"ERROR: {e}"

    # ── Workouts ──────────────────────────────────────────────────────────────
    ep = "workouts"
    start = sync_start_date(ep, full)
    try:
        records = client.get_workouts(start_date=start, end_date=tomorrow)
        with get_conn() as conn:
            for r in records:
                upsert_workout(conn, r)
            set_sync_state(conn, ep, now)
        results[ep] = len(records)
    except Exception as e:
        results[ep] = f"ERROR: {e}"

    # ── Sessions ──────────────────────────────────────────────────────────────
    ep = "sessions"
    start = sync_start_date(ep, full)
    try:
        records = client.get_sessions(start_date=start, end_date=tomorrow)
        with get_conn() as conn:
            for r in records:
                upsert_session(conn, r)
            set_sync_state(conn, ep, now)
        results[ep] = len(records)
    except Exception as e:
        results[ep] = f"ERROR: {e}"

    # ── VO2 max ──────────────────────────────────────────────────────────────
    ep = "vo2_max"
    start = sync_start_date(ep, full)
    try:
        records = client.get_vo2_max(start_date=start, end_date=tomorrow)
        with get_conn() as conn:
            for r in records:
                upsert_vo2_max(conn, r)
            set_sync_state(conn, ep, now)
        results[ep] = len(records)
    except Exception as e:
        results[ep] = f"ERROR: {e}"

    # ── Sleep time recommendations ─────────────────────────────────────────────
    ep = "sleep_time"
    start = sync_start_date(ep, full)
    try:
        records = client.get_sleep_time(start_date=start, end_date=tomorrow)
        with get_conn() as conn:
            for r in records:
                upsert_sleep_time(conn, r)
            set_sync_state(conn, ep, now)
        results[ep] = len(records)
    except Exception as e:
        results[ep] = f"ERROR: {e}"

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\nSync complete — {now[:19]} UTC")
    print("-" * 50)
    for ep, count in results.items():
        status = f"{count} records" if isinstance(count, int) else str(count)
        print(f"  {ep:<25} {status}")
    print()
    return results


def show_status():
    init_db()
    print("\nSync state:")
    print("-" * 50)
    with get_conn() as conn:
        rows = conn.execute("SELECT endpoint, last_synced_at FROM sync_state ORDER BY endpoint").fetchall()
        if not rows:
            print("  No syncs recorded yet.")
        for row in rows:
            print(f"  {row['endpoint']:<25} {row['last_synced_at']}")

    print("\nRow counts:")
    print("-" * 50)
    tables = [
        "daily_sleep", "daily_readiness", "daily_activity", "daily_stress",
        "daily_spo2", "daily_cardiovascular_age", "daily_resilience",
        "sleep_periods", "heartrate", "workouts", "sessions",
        "sleep_time", "vo2_max", "meals", "journal"
    ]
    with get_conn() as conn:
        for t in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<25} {count:>8} rows")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Oura data to local SQLite")
    parser.add_argument("--full",   action="store_true", help="Full re-sync from ring start date")
    parser.add_argument("--status", action="store_true", help="Show sync state and row counts")
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        run_sync(full=args.full)
