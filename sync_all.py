"""Run all configured health data syncs."""

from __future__ import annotations

import argparse
from collections.abc import Callable

import sync as oura_sync
import withings_sync


def _run_provider(name: str, sync_fn: Callable[[bool], object], full: bool) -> bool:
    print(f"\n{name} sync")
    print("=" * 50)
    try:
        sync_fn(full)
    except Exception as exc:
        print(f"\n{name} sync failed: {exc}")
        return False
    return True


def run_sync(full: bool = False) -> int:
    results = [
        _run_provider("Oura", oura_sync.run_sync, full),
        _run_provider("Withings", withings_sync.run_sync, full),
    ]
    return 0 if all(results) else 1


def show_status() -> None:
    print("\nOura")
    print("=" * 50)
    oura_sync.show_status()

    print("\nWithings")
    print("=" * 50)
    withings_sync.show_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync all configured health data sources")
    parser.add_argument("--full", action="store_true", help="Run full syncs for all sources")
    parser.add_argument("--status", action="store_true", help="Show sync state for all sources")
    args = parser.parse_args()

    if args.status:
        show_status()
        raise SystemExit(0)

    raise SystemExit(run_sync(full=args.full))
