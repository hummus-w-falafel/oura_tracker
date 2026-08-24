import math
import unittest
from datetime import date, datetime, timedelta, timezone

import numpy as np

from advanced_analytics import (
    compute_sleep_regularity,
    detect_change_points,
    fit_cosinor,
    run_var_analysis,
)


class AdvancedAnalyticsTests(unittest.TestCase):
    def test_change_points_recovers_stable_baseline_shift(self):
        rng = np.random.default_rng(12)
        start = date(2026, 1, 1)
        days = [(start + timedelta(days=index)).isoformat() for index in range(100)]
        values = np.concatenate([rng.normal(50, 1, 50), rng.normal(65, 1, 50)])

        result = detect_change_points(days, values.tolist(), higher_is_better=True)

        self.assertEqual(result["status"], "ready")
        self.assertTrue(any(abs((date.fromisoformat(item["day"]) - (start + timedelta(days=50))).days) <= 2
                            for item in result["changes"]))
        change = result["changes"][0]
        self.assertTrue(change["favorable"])
        self.assertGreater(change["delta"], 10)

    def test_sleep_regularity_is_100_for_repeating_schedule(self):
        periods = []
        start = date(2026, 1, 1)
        for index in range(7):
            bedtime = datetime.combine(start + timedelta(days=index), datetime.min.time(), tzinfo=timezone.utc)
            bedtime = bedtime.replace(hour=23)
            periods.append({
                "day": (start + timedelta(days=index + 1)).isoformat(),
                "type": "long_sleep",
                "bedtime_start": bedtime.isoformat(),
                "sleep_phase_5_min": "2" * 96,
            })

        result = compute_sleep_regularity(periods, tz_name="UTC")

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["sri"], 100.0)
        self.assertEqual(result["valid_day_pairs"], 6)

    def test_sleep_regularity_counts_awake_stage_as_wake(self):
        periods = []
        start = date(2026, 1, 1)
        for index in range(5):
            phases = list("2" * 96)
            if index == 2:
                phases[30:42] = "4" * 12
            periods.append({
                "day": (start + timedelta(days=index + 1)).isoformat(),
                "type": "long_sleep",
                "bedtime_start": datetime.combine(
                    start + timedelta(days=index), datetime.min.time(), tzinfo=timezone.utc
                ).replace(hour=23).isoformat(),
                "sleep_phase_5_min": "".join(phases),
            })

        result = compute_sleep_regularity(periods, tz_name="UTC")

        self.assertLess(result["sri"], 100)

    def test_sleep_regularity_raster_excludes_naps(self):
        periods = []
        start = date(2026, 1, 1)
        for index in range(5):
            day = start + timedelta(days=index)
            periods.append({
                "day": (day + timedelta(days=1)).isoformat(),
                "type": "long_sleep",
                "bedtime_start": datetime.combine(
                    day, datetime.min.time(), tzinfo=timezone.utc
                ).replace(hour=23).isoformat(),
                "sleep_phase_5_min": "2" * 96,
            })
        periods.append({
            "day": (start + timedelta(days=3)).isoformat(),
            "type": "late_nap",
            "bedtime_start": datetime.combine(
                start + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc
            ).replace(hour=15).isoformat(),
            "sleep_phase_5_min": "2" * 12,
        })

        result = compute_sleep_regularity(periods, tz_name="UTC")
        nap_day = result["raster"][2]["states"]

        self.assertEqual(result["raster_scope"], "long_sleep")
        self.assertEqual(nap_day[36:48], "0" * 12)
        self.assertEqual(nap_day[132:228], "1" * 96)
        self.assertLess(result["sri"], 100)

    def test_cosinor_recovers_known_amplitude_and_peak(self):
        readings = []
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for day in range(30):
            for slot in range(96):
                hour = slot / 4
                bpm = 60 + 8 * math.cos(2 * math.pi * (hour - 15) / 24)
                readings.append({
                    "timestamp": (start + timedelta(days=day, minutes=15 * slot)).isoformat(),
                    "bpm": bpm,
                    "source": "awake",
                })

        result = fit_cosinor(readings, tz_name="UTC", bootstrap_samples=20)

        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(result["mesor"], 60, places=1)
        self.assertAlmostEqual(result["amplitude"], 8, places=1)
        self.assertAlmostEqual(result["peak_hour"], 15, places=1)

    def test_var_detects_planted_lagged_predictor(self):
        rng = np.random.default_rng(44)
        n = 150
        predictor = np.zeros(n)
        outcome = np.zeros(n)
        sleep = rng.normal(0, 1, n)
        lowest = rng.normal(0, 1, n)
        for index in range(1, n):
            predictor[index] = 0.45 * predictor[index - 1] + rng.normal(0, 0.5)
            outcome[index] = 0.35 * outcome[index - 1] + 0.8 * predictor[index - 1] + rng.normal(0, 0.35)
        start = date(2026, 1, 1)
        days = [(start + timedelta(days=index)).isoformat() for index in range(n)]
        rows = [{
            "readiness_score": outcome[index],
            "sleep_score": sleep[index],
            "hrv": predictor[index],
            "lowest_hr": lowest[index],
        } for index in range(n)]

        result = run_var_analysis(days, rows, model_name="recovery", max_lag=2)
        planted = next(item for item in result["results"]
                       if item["predictor"] == "hrv" and item["outcome"] == "readiness_score")

        self.assertLess(planted["p"], 0.01)
        self.assertLess(planted["q"], 0.05)
        self.assertGreater(planted["prediction_gain_pct"], 0)
        self.assertEqual(len(planted["irf"]), 7)


if __name__ == "__main__":
    unittest.main()
