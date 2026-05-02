import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test-health.db"
        self.profile_path = Path(self.tmp.name) / "PROFILE.md"
        os.environ["HEALTH_DB_PATH"] = str(self.db_path)
        os.environ["PROFILE_PATH"] = str(self.profile_path)
        os.environ["TIMEZONE"] = "America/Toronto"

        import db
        import nutrition
        import leveling
        import dashboard

        self.db = importlib.reload(db)
        self.nutrition = importlib.reload(nutrition)
        self.leveling = importlib.reload(leveling)
        self.dashboard = importlib.reload(dashboard)
        self.db.init_db()

    def tearDown(self):
        self.tmp.cleanup()
        for key in ("HEALTH_DB_PATH", "PROFILE_PATH"):
            os.environ.pop(key, None)

    def test_init_db_creates_core_tables(self):
        with self.db.get_conn() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("meals", tables)
        self.assertIn("meal_items", tables)
        self.assertIn("workout_sets", tables)
        self.assertIn("leveling_daily_cache", tables)

    def test_raw_logging_helpers(self):
        self.db.log_meal(
            day="2026-04-25",
            meal_type="lunch",
            description="known nutrition meal",
            calories=500,
            protein_g=40,
            logged_at="2026-04-25T12:00:00-04:00",
        )
        self.db.log_substance("2026-04-25T18:00:00-04:00", "caffeine", 100, "mg", None, "coffee")
        self.db.log_sex("2026-04-25T23:00:00-04:00", "sex", 20)
        self.db.log_journal("2026-04-25", "test note", "general")

        with self.db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM meals").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM substances").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sex").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0], 1)

    def test_meal_item_logging_rolls_up_parent_totals(self):
        meal_id = self.db.log_meal_with_items(
            day="2026-04-25",
            meal_type="dinner",
            description="dumplings and spring rolls",
            logged_at="2026-04-25T19:00:00-04:00",
            items=[
                {"item_name": "pork dumplings", "quantity": 12, "unit": "pieces", "calories": 600, "protein_g": 30},
                {"item_name": "veggie spring rolls", "quantity": 6, "unit": "pieces", "calories": 420, "protein_g": 12},
            ],
        )

        with self.db.get_conn() as conn:
            meal = conn.execute("SELECT calories, protein_g FROM meals WHERE id=?", (meal_id,)).fetchone()
            items = conn.execute(
                "SELECT item_name, sort_order FROM meal_items WHERE meal_id=? ORDER BY sort_order",
                (meal_id,),
            ).fetchall()

        self.assertEqual(meal["calories"], 1020)
        self.assertEqual(meal["protein_g"], 42)
        self.assertEqual([r["item_name"] for r in items], ["pork dumplings", "veggie spring rolls"])
        self.assertEqual([r["sort_order"] for r in items], [1, 2])

    def test_meal_item_logging_rolls_back_on_item_failure(self):
        with self.assertRaises(ValueError):
            self.db.log_meal_with_items(
                day="2026-04-25",
                meal_type="dinner",
                description="bad structured meal",
                logged_at="2026-04-25T19:00:00-04:00",
                items=[{"calories": 100}],
            )

        with self.db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM meals").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM meal_items").fetchone()[0], 0)

    def test_usda_meal_logging_uses_local_day_for_journal(self):
        fake_lookup = {
            "items": [{
                "fdc_id": 123,
                "description": "mock food",
                "serving_g": 100,
                "calories_kcal": 600,
                "protein_g": 45,
                "carbs_g": 50,
                "fat_g": 20,
                "saturated_fat_g": 5,
                "sugar_g": 8,
                "fiber_g": 9,
                "omega3_g": 1.0,
                "vitamin_d_iu": 400,
                "vitamin_b12_ug": 2.0,
                "magnesium_mg": 100,
                "zinc_mg": 4,
                "iron_mg": 3,
                "potassium_mg": 800,
                "sodium_mg": 700,
                "vitamin_c_mg": 20,
                "vitamin_e_mg": 3,
                "vitamin_b6_mg": 0.4,
                "folate_ug": 120,
            }],
            "totals": {
                "calories_kcal": 600,
                "protein_g": 45,
                "carbs_g": 50,
                "fat_g": 20,
                "saturated_fat_g": 5,
                "sugar_g": 8,
                "fiber_g": 9,
                "omega3_g": 1.0,
                "vitamin_d_iu": 400,
                "vitamin_b12_ug": 2.0,
                "magnesium_mg": 100,
                "zinc_mg": 4,
                "iron_mg": 3,
                "potassium_mg": 800,
                "sodium_mg": 700,
                "vitamin_c_mg": 20,
                "vitamin_e_mg": 3,
                "vitamin_b6_mg": 0.4,
                "folate_ug": 120,
            },
        }

        with patch.object(self.nutrition, "lookup_multi", return_value=fake_lookup):
            self.nutrition.log_meal_with_nutrition(
                "mock meal",
                [("mock food", 100)],
                logged_at="2026-04-25T03:30:00+00:00",
            )

        with self.db.get_conn() as conn:
            meal = conn.execute("SELECT logged_at, protein_g FROM meals").fetchone()
            item = conn.execute("SELECT item_name, fdc_id, protein_g, source, confidence FROM meal_items").fetchone()
            journal = conn.execute("SELECT day, category, note FROM journal").fetchone()

        self.assertEqual(meal["logged_at"], "2026-04-25T03:30:00+00:00")
        self.assertEqual(meal["protein_g"], 45)
        self.assertEqual(item["item_name"], "mock food")
        self.assertEqual(item["fdc_id"], 123)
        self.assertEqual(item["protein_g"], 45)
        self.assertEqual(item["source"], "USDA")
        self.assertEqual(item["confidence"], "usda")
        self.assertEqual(journal["day"], "2026-04-24")
        self.assertEqual(journal["category"], "nutrition")
        self.assertIn("mock meal", journal["note"])

    def test_usda_meal_logging_rolls_back_on_item_failure(self):
        fake_lookup = {
            "items": [{"calories_kcal": 100}],
            "totals": {"calories_kcal": 100},
        }

        with patch.object(self.nutrition, "lookup_multi", return_value=fake_lookup):
            with self.assertRaises(ValueError):
                self.nutrition.log_meal_with_nutrition(
                    "bad usda meal",
                    [("bad", 100)],
                    logged_at="2026-04-25T12:00:00-04:00",
                )

        with self.db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM meals").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM meal_items").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0], 0)

    def test_workout_session_logging(self):
        with self.db.get_conn() as conn:
            conn.execute(
                "INSERT INTO workouts (id, day, activity, source, start_datetime, end_datetime) VALUES (?,?,?,?,?,?)",
                ("w1", "2026-04-25", "kettlebell", "manual", "2026-04-25T18:00:00Z", "2026-04-25T18:30:00Z"),
            )

        self.db.log_workout_session("w1", "2026-04-25", [
            ("double KB press", 1, 5, 25.0),
            ("double KB press", 2, 4, 25.0, True, "hard"),
        ])

        with self.db.get_conn() as conn:
            rows = conn.execute("SELECT workout_id, exercise, reps FROM workout_sets ORDER BY set_number").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["workout_id"], "w1")
        self.assertEqual(rows[1]["reps"], 4)

    def test_profile_targets_override_scoring(self):
        self.profile_path.write_text(
            """# User Profile

```yaml
targets:
  calories: 2500
  protein_g: 150
  training_sessions_per_week: 4
```
"""
        )
        targets = self.nutrition.get_targets()
        self.assertEqual(targets["calories"], 2500)
        self.assertEqual(targets["protein_g"], 150)

    def test_compute_snapshot_smoke(self):
        with self.db.get_conn() as conn:
            conn.execute(
                "INSERT INTO daily_sleep (day, score, synced_at) VALUES (?, ?, ?)",
                ("2026-04-25", 80, "2026-04-25T12:00:00Z"),
            )
        result = self.leveling.compute_snapshot()
        self.assertIn("level", result)
        self.assertIn("rank", result)

    def test_dashboard_correlation_api_smoke(self):
        with self.db.get_conn() as conn:
            for idx, day in enumerate(["2026-04-23", "2026-04-24", "2026-04-25"]):
                conn.execute("INSERT INTO daily_sleep (day, score) VALUES (?, ?)", (day, 70 + idx))
                conn.execute("INSERT INTO daily_readiness (day, score) VALUES (?, ?)", (day, 65 + idx))
                conn.execute(
                    "INSERT INTO sleep_periods (id, day, type, total_sleep_duration, average_hrv, lowest_heart_rate) VALUES (?, ?, ?, ?, ?, ?)",
                    (f"s{idx}", day, "long_sleep", 25200, 40 + idx, 55 - idx),
                )

        client = self.dashboard.app.test_client()
        response = client.get("/api/correlations")
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertIn("pvalues", payload)
        self.assertIn("counts", payload)
        self.assertIn("nicotine_count", payload["features"])
        self.assertIn("nicotine_mg", payload["features"])

    def test_dashboard_granger_api_smoke(self):
        with self.db.get_conn() as conn:
            for idx, day in enumerate(["2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24", "2026-04-25"]):
                conn.execute("INSERT INTO daily_sleep (day, score) VALUES (?, ?)", (day, 70 + idx))
                conn.execute("INSERT INTO daily_readiness (day, score) VALUES (?, ?)", (day, 65 + idx))
                conn.execute(
                    "INSERT INTO sleep_periods (id, day, type, total_sleep_duration, average_hrv, lowest_heart_rate) VALUES (?, ?, ?, ?, ?, ?)",
                    (f"g{idx}", day, "long_sleep", 25200, 40 + idx, 55 - idx),
                )

        client = self.dashboard.app.test_client()
        response = client.get("/api/granger")
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertIn("mature_days", payload)
        self.assertIn("results", payload)


if __name__ == "__main__":
    unittest.main()
