"""Sync Withings Body Comp data into SQLite."""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from db import (
    get_conn,
    get_withings_sync_state,
    init_db,
    set_withings_sync_state,
    upsert_withings_measure_group,
)
from withings_client import WithingsClient

ENDPOINT = "measure_getmeas"


def run_sync(full: bool = False):
    init_db()
    sync_started_unix = int(time.time())
    now = datetime.utcnow().isoformat()
    state = get_withings_sync_state(ENDPOINT)
    lastupdate = 0 if full or not state else int(state.get("last_update_unix") or 0)

    client = WithingsClient()
    payload = client.get_body_comp_measurements(lastupdate=lastupdate)
    records = payload.get("measuregrps", [])

    with get_conn() as conn:
        for record in records:
            upsert_withings_measure_group(conn, record)
        set_withings_sync_state(conn, ENDPOINT, now, sync_started_unix)

    print(f"\nWithings sync complete — {now[:19]} UTC")
    print("-" * 50)
    print(f"  endpoint             {ENDPOINT}")
    print(f"  lastupdate           {lastupdate}")
    print(f"  measure_groups       {len(records)}")
    return {"endpoint": ENDPOINT, "lastupdate": lastupdate, "records": len(records)}


def show_status():
    init_db()
    print("\nWithings sync state:")
    print("-" * 50)
    state = get_withings_sync_state(ENDPOINT)
    if state:
        print(f"  {ENDPOINT:<25} {state['last_synced_at']}  lastupdate={state['last_update_unix']}")
    else:
        print("  No Withings syncs recorded yet.")

    print("\nRow counts:")
    print("-" * 50)
    tables = [
        "withings_measure_groups",
        "withings_measure_items",
        "withings_body_composition",
    ]
    with get_conn() as conn:
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:<30} {count:>8} rows")

        rows = conn.execute(
            "SELECT measure_type, COUNT(*) AS cnt FROM withings_measure_items "
            "GROUP BY measure_type ORDER BY measure_type"
        ).fetchall()
        if rows:
            print("\nMeasure types:")
            print("-" * 50)
            for row in rows:
                print(f"  type {row['measure_type']:<4} {row['cnt']:>8} rows")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Withings Body Comp data to local SQLite")
    parser.add_argument("--full", action="store_true", help="Full Withings sync using lastupdate=0")
    parser.add_argument("--status", action="store_true", help="Show Withings sync state and row counts")
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        run_sync(full=args.full)
