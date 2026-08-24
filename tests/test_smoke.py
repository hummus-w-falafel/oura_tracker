import importlib
import json
import os
import tempfile
import unittest
from datetime import date, datetime
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
        os.environ["WITHINGS_CLIENT_ID"] = "test-client"
        os.environ["WITHINGS_CLIENT_SECRET"] = "test-secret"
        os.environ["WITHINGS_REDIRECT_URI"] = "https://example.test/callback"
        os.environ["WITHINGS_TOKEN_FILE"] = str(Path(self.tmp.name) / "withings_tokens.json")

        import db
        import nutrition
        import leveling
        import dashboard
        import withings_client

        self.db = importlib.reload(db)
        self.nutrition = importlib.reload(nutrition)
        self.leveling = importlib.reload(leveling)
        self.dashboard = importlib.reload(dashboard)
        self.withings_client = importlib.reload(withings_client)
        self.db.init_db()

    def tearDown(self):
        self.tmp.cleanup()
        for key in (
            "HEALTH_DB_PATH", "PROFILE_PATH", "WITHINGS_CLIENT_ID",
            "WITHINGS_CLIENT_SECRET", "WITHINGS_REDIRECT_URI", "WITHINGS_TOKEN_FILE",
        ):
            os.environ.pop(key, None)

    def test_init_db_creates_core_tables(self):
        with self.db.get_conn() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("meals", tables)
        self.assertIn("meal_items", tables)
        self.assertIn("workout_sets", tables)
        self.assertIn("travel", tables)
        self.assertIn("withings_measure_groups", tables)
        self.assertIn("withings_measure_items", tables)
        self.assertIn("withings_body_composition", tables)
        self.assertIn("withings_sync_state", tables)
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
        self.db.log_travel(
            start_datetime="2026-04-25T20:00:00-04:00",
            end_datetime="2026-04-25T21:30:00-04:00",
            travel_type="flight",
            origin="Toronto",
            destination="New York",
            hours=1.5,
            timezone_shift_hours=0,
            direction="none",
            notes="test flight",
        )
        self.db.log_journal("2026-04-25", "test note", "general")

        with self.db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM meals").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM substances").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sex").fetchone()[0], 1)
            travel = conn.execute("SELECT day, travel_type, hours FROM travel").fetchone()
            self.assertEqual(travel["day"], "2026-04-25")
            self.assertEqual(travel["travel_type"], "flight")
            self.assertEqual(travel["hours"], 1.5)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0], 1)

    def test_travel_day_uses_event_timezone_not_home_timezone(self):
        self.db.log_travel(
            start_datetime="2026-07-02T00:30:00+09:00",
            end_datetime="2026-07-02T02:30:00+09:00",
            travel_type="flight",
            origin="Tokyo",
            destination="Seoul",
            hours=2,
            timezone_shift_hours=0,
            direction="west",
        )

        with self.db.get_conn() as conn:
            travel = conn.execute("SELECT day FROM travel").fetchone()

        self.assertEqual(travel["day"], "2026-07-02")

    def test_withings_measure_group_flattens_body_composition(self):
        record = {
            "grpid": 123,
            "date": 1785157200,
            "created": 1785157201,
            "modified": 1785157202,
            "attrib": 0,
            "category": 1,
            "deviceid": "scale-1",
            "model": "Body Comp",
            "measures": [
                {"type": 1, "value": 81234, "unit": -3},
                {"type": 6, "value": 183, "unit": -1},
                {"type": 8, "value": 14870, "unit": -3},
                {"type": 76, "value": 62000, "unit": -3},
                {"type": 77, "value": 45500, "unit": -3},
                {"type": 88, "value": 3100, "unit": -3},
                {"type": 999, "value": 42, "unit": 0},
            ],
        }
        with self.db.get_conn() as conn:
            self.db.upsert_withings_measure_group(conn, record)

        with self.db.get_conn() as conn:
            body = conn.execute("SELECT * FROM withings_body_composition WHERE grpid = 123").fetchone()
            unknown = conn.execute(
                "SELECT value FROM withings_measure_items WHERE grpid = 123 AND measure_type = 999"
            ).fetchone()

        self.assertAlmostEqual(body["weight_kg"], 81.234)
        self.assertAlmostEqual(body["fat_ratio_pct"], 18.3)
        self.assertAlmostEqual(body["fat_mass_kg"], 14.87)
        self.assertAlmostEqual(body["muscle_mass_kg"], 62.0)
        self.assertAlmostEqual(body["hydration_kg"], 45.5)
        self.assertAlmostEqual(body["water_pct"], 56.01)
        self.assertAlmostEqual(body["muscle_pct"], 76.32)
        self.assertEqual(unknown["value"], 42)

    def test_withings_signature_and_value_conversion(self):
        sig = self.withings_client.sign({
            "action": "getnonce",
            "client_id": "test-client",
            "timestamp": 123,
        })
        self.assertEqual(
            sig,
            "49f78c673aba4bae62f9723ddbd1cc93029f76bba6a32bcc5d0981a92463ba97",
        )
        self.assertEqual(self.db.withings_value({"value": 81234, "unit": -3}), 81.234)

    def test_dashboard_body_composition_api_returns_latest_daily_rows(self):
        today = date.today().isoformat()
        with self.db.get_conn() as conn:
            self.db.upsert_withings_measure_group(conn, {
                "grpid": 200,
                "date": int(datetime.fromisoformat(today + "T08:00:00+00:00").timestamp()),
                "measures": [
                    {"type": 1, "value": 81000, "unit": -3},
                    {"type": 6, "value": 185, "unit": -1},
                    {"type": 8, "value": 14985, "unit": -3},
                    {"type": 76, "value": 61000, "unit": -3},
                    {"type": 77, "value": 45200, "unit": -3},
                    {"type": 88, "value": 3100, "unit": -3},
                ],
            })
            self.db.upsert_withings_measure_group(conn, {
                "grpid": 201,
                "date": int(datetime.fromisoformat(today + "T20:00:00+00:00").timestamp()),
                "measures": [
                    {"type": 1, "value": 80500, "unit": -3},
                    {"type": 6, "value": 181, "unit": -1},
                    {"type": 8, "value": 14570, "unit": -3},
                    {"type": 76, "value": 61200, "unit": -3},
                    {"type": 77, "value": 45400, "unit": -3},
                    {"type": 88, "value": 3100, "unit": -3},
                ],
            })

        client = self.dashboard.app.test_client()
        response = client.get("/api/body-composition/7")
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertEqual(len(payload["body_composition"]), 1)
        row = payload["body_composition"][0]
        self.assertEqual(row["day"], today)
        self.assertEqual(row["weight_kg"], 80.5)
        self.assertEqual(row["fat_ratio_pct"], 18.1)
        self.assertAlmostEqual(row["muscle_pct"], 76.02)

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
        self.assertIn("flight_hours", payload["features"])
        self.assertIn("timezone_shift_abs", payload["features"])
        self.assertIn("big_timezone_shift", payload["features"])
        self.assertIn("flight_hours_yesterday", payload["features"])
        self.assertIn("big_timezone_shift_yesterday", payload["features"])
        self.assertIn("post_travel_1_3d", payload["features"])
        self.assertIn("weight_kg", payload["features"])
        self.assertIn("fat_ratio_pct", payload["features"])
        self.assertIn("muscle_pct", payload["features"])
        self.assertIn("water_pct", payload["features"])

    def test_daily_feature_matrix_derives_travel_recovery_features(self):
        with self.db.get_conn() as conn:
            for idx, day in enumerate(["2026-04-25", "2026-04-26", "2026-04-27", "2026-04-28"]):
                conn.execute("INSERT INTO daily_sleep (day, score) VALUES (?, ?)", (day, 70 + idx))
                conn.execute(
                    "INSERT INTO sleep_periods (id, day, type, total_sleep_duration, average_hrv, lowest_heart_rate) VALUES (?, ?, ?, ?, ?, ?)",
                    (f"travel-s{idx}", day, "long_sleep", 25200, 50 + idx, 55 - idx),
                )
            conn.execute(
                "INSERT INTO travel "
                "(day, start_datetime, end_datetime, travel_type, origin, destination, hours, timezone_shift_hours, direction) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-04-25", "2026-04-25T08:00:00-04:00", "2026-04-25T11:00:00-04:00",
                 "flight", "Toronto", "London", 2.5, 6, "east"),
            )

        days, features, rows = self.dashboard.build_daily_feature_matrix()
        by_day = {day: row for day, row in zip(days, rows)}
        self.assertEqual(by_day["2026-04-25"]["flight_hours"], 2.5)
        self.assertEqual(by_day["2026-04-25"]["big_timezone_shift"], 1)
        self.assertEqual(by_day["2026-04-26"]["travel_yesterday"], 1)
        self.assertEqual(by_day["2026-04-26"]["flight_hours_yesterday"], 2.5)
        self.assertEqual(by_day["2026-04-26"]["timezone_shift_abs_yesterday"], 6)
        self.assertEqual(by_day["2026-04-26"]["big_timezone_shift_yesterday"], 1)
        self.assertEqual(by_day["2026-04-26"]["days_since_travel"], 1)
        self.assertEqual(by_day["2026-04-28"]["days_since_travel"], 3)
        self.assertEqual(by_day["2026-04-28"]["post_travel_1_3d"], 1)

    def test_dashboard_continuous_api_includes_travel(self):
        today = date.today().isoformat()
        self.db.log_travel(
            start_datetime=f"{today}T08:00:00-04:00",
            end_datetime=f"{today}T10:30:00-04:00",
            travel_type="flight",
            origin="Toronto",
            destination="Chicago",
            hours=2.5,
            timezone_shift_hours=-1,
            direction="west",
        )

        client = self.dashboard.app.test_client()
        response = client.get("/api/continuous/7")
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertEqual(payload["travel"][0]["travel_type"], "flight")
        self.assertEqual(payload["travel"][0]["hours"], 2.5)

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
        self.assertEqual(payload["status"], "collecting")
        self.assertIn("results", payload)

    def test_advanced_analytics_routes_smoke(self):
        client = self.dashboard.app.test_client()
        self.assertEqual(client.get("/analytics").status_code, 200)
        for path in (
            "/api/analytics/change-points?metric=hrv&days=90",
            "/api/analytics/sleep-regularity?days=30",
            "/api/analytics/circadian?days=30",
            "/api/analytics/granger?model=recovery",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_dashboard_strength_api_smoke(self):
        today = date.today().isoformat()
        week = date.today().strftime("%Y-W%W")
        with self.db.get_conn() as conn:
            conn.execute(
                "INSERT INTO workouts (id, day, activity, source, start_datetime, end_datetime) VALUES (?,?,?,?,?,?)",
                ("w1", today, "kettlebell", "manual", f"{today}T18:00:00Z", f"{today}T18:30:00Z"),
            )
        self.db.log_workout_session("w1", today, [
            ("double KB press", 1, 5, 25.0),
            ("double KB press", 2, 6, 25.0),
        ])

        client = self.dashboard.app.test_client()
        response = client.get("/api/strength/30")
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertEqual(payload["total_sets"], 2)
        self.assertIn(today, payload["days"])
        self.assertTrue(any(w["week"] == week for w in payload["weeks"]))
        self.assertEqual(payload["daily_sets"][0]["exercise"], "double KB press")
        self.assertEqual(payload["daily_sets"][0]["sets"], 2)
        self.assertEqual(payload["daily_sets"][0]["details"][1]["reps"], 6)
        self.assertEqual(payload["daily_sets"][0]["details"][1]["load"], 300)


if __name__ == "__main__":
    unittest.main()
