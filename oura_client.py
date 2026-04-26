"""
Oura Ring API v2 client.
Fetches all available data types with automatic pagination.
"""

import json
import os
from datetime import date, datetime, timedelta
import requests
from dotenv import load_dotenv

from auth import get_valid_token

load_dotenv()

BASE_URL = "https://api.ouraring.com/v2/usercollection"


class OuraClient:
    def __init__(self):
        self.token = get_valid_token()
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    def _get(self, endpoint: str, params: dict = None) -> list:
        """Fetch all pages from an endpoint. Returns [] if endpoint unavailable."""
        url = f"{BASE_URL}/{endpoint}"
        results = []
        next_token = None

        while True:
            p = params.copy() if params else {}
            if next_token:
                p["next_token"] = next_token

            resp = self.session.get(url, params=p)

            # Endpoint not available for this ring/subscription
            if resp.status_code in (401, 403, 404, 426):
                return []

            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("data", []))
            next_token = data.get("next_token")
            if not next_token:
                break

        return results

    def _date_params(self, start_date: str = None, end_date: str = None, days: int = None) -> dict:
        """Build date range params. Default: last 30 days."""
        if not end_date:
            end_date = date.today().isoformat()
        if not start_date:
            if days:
                start_date = (date.today() - timedelta(days=days)).isoformat()
            else:
                start_date = (date.today() - timedelta(days=30)).isoformat()
        return {"start_date": start_date, "end_date": end_date}

    # ── Daily summaries ──────────────────────────────────────────────────────

    def get_daily_sleep(self, start_date=None, end_date=None, days=30):
        """Daily sleep scores and contributors."""
        return self._get("daily_sleep", self._date_params(start_date, end_date, days))

    def get_daily_readiness(self, start_date=None, end_date=None, days=30):
        """Daily readiness scores and contributors."""
        return self._get("daily_readiness", self._date_params(start_date, end_date, days))

    def get_daily_activity(self, start_date=None, end_date=None, days=30):
        """Daily activity scores, steps, calories, active time."""
        return self._get("daily_activity", self._date_params(start_date, end_date, days))

    def get_daily_stress(self, start_date=None, end_date=None, days=30):
        """Daily stress and recovery time."""
        return self._get("daily_stress", self._date_params(start_date, end_date, days))

    def get_daily_resilience(self, start_date=None, end_date=None, days=30):
        """Daily resilience score (daytime recovery + sleep recovery)."""
        return self._get("daily_resilience", self._date_params(start_date, end_date, days))

    def get_daily_spo2(self, start_date=None, end_date=None, days=30):
        """Daily blood oxygen saturation during sleep."""
        return self._get("daily_spo2", self._date_params(start_date, end_date, days))

    def get_daily_cardiovascular_age(self, start_date=None, end_date=None, days=90):
        """Estimated cardiovascular age."""
        return self._get("daily_cardiovascular_age", self._date_params(start_date, end_date, days))

    # ── Detailed / time-series ───────────────────────────────────────────────

    def get_sleep_periods(self, start_date=None, end_date=None, days=30):
        """Detailed sleep periods: stages, HRV, heart rate during sleep."""
        return self._get("sleep", self._date_params(start_date, end_date, days))

    def get_heart_rate(self, start_datetime=None, end_datetime=None, days=7):
        """Raw heart rate time series."""
        if not end_datetime:
            end_datetime = datetime.utcnow().isoformat() + "Z"
        if not start_datetime:
            start_datetime = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
        return self._get("heartrate", {"start_datetime": start_datetime, "end_datetime": end_datetime})

    def get_workouts(self, start_date=None, end_date=None, days=90):
        """Workout sessions with type, duration, calories, heart rate."""
        return self._get("workout", self._date_params(start_date, end_date, days))

    def get_sessions(self, start_date=None, end_date=None, days=30):
        """Guided/unguided sessions (meditation, breathing, naps)."""
        return self._get("session", self._date_params(start_date, end_date, days))

    def get_vo2_max(self, start_date=None, end_date=None, days=180):
        """VO2 max estimates."""
        return self._get("vO2_max", self._date_params(start_date, end_date, days))

    def get_sleep_time(self, start_date=None, end_date=None, days=30):
        """Optimal bedtime recommendations."""
        return self._get("sleep_time", self._date_params(start_date, end_date, days))

    # ── User info ────────────────────────────────────────────────────────────

    def get_personal_info(self):
        """User profile: age, weight, height, biological sex."""
        resp = self.session.get(f"{BASE_URL}/personal_info")
        resp.raise_for_status()
        return resp.json()

    def get_ring_configuration(self, start_date=None, end_date=None, days=365):
        """Ring hardware info and firmware version."""
        return self._get("ring_configuration", self._date_params(start_date, end_date, days))

    # ── Convenience / aggregated views ───────────────────────────────────────

    def get_snapshot(self, days=7):
        """
        Returns a consolidated health snapshot for the last N days.
        Useful for a quick overview before coaching sessions.
        """
        return {
            "period_days": days,
            "as_of": date.today().isoformat(),
            "sleep": self.get_daily_sleep(days=days),
            "readiness": self.get_daily_readiness(days=days),
            "activity": self.get_daily_activity(days=days),
            "stress": self.get_daily_stress(days=days),
            "resilience": self.get_daily_resilience(days=days),
            "spo2": self.get_daily_spo2(days=days),
            "workouts": self.get_workouts(days=days),
        }

    def get_full_history(self, days=365):
        """Pull a full year of all data. Use for onboarding/deep analysis."""
        return {
            "period_days": days,
            "as_of": date.today().isoformat(),
            "personal_info": self.get_personal_info(),
            "sleep": self.get_daily_sleep(days=days),
            "sleep_periods": self.get_sleep_periods(days=days),
            "readiness": self.get_daily_readiness(days=days),
            "activity": self.get_daily_activity(days=days),
            "stress": self.get_daily_stress(days=days),
            "resilience": self.get_daily_resilience(days=days),
            "spo2": self.get_daily_spo2(days=days),
            "cardiovascular_age": self.get_daily_cardiovascular_age(days=days),
            "workouts": self.get_workouts(days=days),
            "vo2_max": self.get_vo2_max(days=days),
            "sleep_time": self.get_sleep_time(days=days),
        }


def save_snapshot(data: dict, filename: str = "oura_snapshot.json"):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved to {filename}")


if __name__ == "__main__":
    client = OuraClient()
    print("Fetching 7-day snapshot...")
    snapshot = client.get_snapshot(days=7)
    save_snapshot(snapshot)
    print(json.dumps(snapshot, indent=2))
