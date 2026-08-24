"""Timezone helpers for local health-day grouping."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo


def local_tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("TIMEZONE", "America/Toronto"))


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp, assuming local timezone when no offset is present."""
    if not value:
        raise ValueError("timestamp is required")
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz())
    return dt


def ensure_tz(value: str) -> str:
    """Return an ISO timestamp with timezone information."""
    return parse_timestamp(value).isoformat()


def now_local() -> str:
    return datetime.now(local_tz()).isoformat(timespec="seconds")


def local_day(value: str | None = None) -> str:
    """Return the configured-local date for a timestamp or current local time."""
    dt = parse_timestamp(value) if value else datetime.now(local_tz())
    return dt.astimezone(local_tz()).date().isoformat()


def event_local_day(value: str | None = None) -> str:
    """
    Return the date in the timestamp's own timezone.

    This is useful for manual logs entered while traveling. If a timestamp is
    stored as 2026-06-14T22:30:00-07:00, the user's intended event day is usually
    2026-06-14 even when their configured home timezone is America/Toronto.
    Naive timestamps still assume the configured local timezone.
    """
    dt = parse_timestamp(value) if value else datetime.now(local_tz())
    return dt.date().isoformat()
