"""
Backfill micronutrient columns for existing meals.
Idempotent: only updates rows where fiber_g IS NULL.

Run from the project root:
    python3 scripts/backfill_micros.py
"""
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nutrition import lookup_multi

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "health.db")

# Manual item mappings for meals without journal entries
MEAL_ITEMS = {
    2: [  # Whole wheat bread + hummus + olive oil + cherry tomatoes
        ("whole wheat bread", 80),
        ("hummus commercial", 30),
        ("olive oil", 14),
        ("cherry tomatoes raw", 68),
    ],
    3: [  # 2x BK chicken sandwich + 8 mozz sticks + marinara
        ("BURGER KING Original Chicken Sandwich", 400),
        ("fried mozzarella sticks", 160),
        ("marinara sauce", 60),
    ],
    4: [  # Ouzi surar (lamb rice with pine nuts and ghee), orange juice
        ("lamb cooked", 150),
        ("cooked white rice", 200),
        ("pine nuts", 15),
        ("ghee", 20),
        ("orange juice", 250),
    ],
    5: [  # Whole wheat bread + hummus + olive oil + cherry tomatoes (bigger portions)
        ("whole wheat bread", 100),
        ("hummus commercial", 40),
        ("olive oil", 14),
        ("cherry tomatoes raw", 80),
    ],
    6: [  # Pork dumplings, spring rolls, spinach bun, orange juice
        ("pork dumplings", 200),
        ("spring rolls", 120),
        ("spinach bun steamed", 80),
        ("orange juice", 250),
    ],
    7: [  # Hummus with whole wheat bread and cherry tomatoes
        ("hummus commercial", 60),
        ("whole wheat bread", 100),
        ("cherry tomatoes raw", 80),
    ],
    8: [  # 2x BK chicken sandwich + 8 mozz sticks + marinara + OJ
        ("BURGER KING Original Chicken Sandwich", 400),
        ("fried mozzarella sticks", 160),
        ("marinara sauce", 60),
        ("orange juice", 250),
    ],
    9: [  # Protein whole wheat bread with hummus and cherry tomatoes
        ("whole wheat bread", 100),
        ("hummus commercial", 40),
        ("cherry tomatoes raw", 80),
    ],
}


def backfill():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id FROM meals WHERE fiber_g IS NULL ORDER BY id").fetchall()
    if not rows:
        print("Nothing to backfill — all meals already have micronutrient data.")
        return

    print(f"Backfilling {len(rows)} meals...")
    for row in rows:
        meal_id = row["id"]
        items = MEAL_ITEMS.get(meal_id)
        if not items:
            print(f"  Skipping meal id={meal_id} — no item mapping defined")
            continue

        print(f"\n  Meal id={meal_id}:")
        result = lookup_multi(items, verbose=True)
        totals = result["totals"]

        vit_d_iu = totals.get("vitamin_d_iu", 0)
        vit_d_mcg = round(vit_d_iu / 40.0, 2) if vit_d_iu else None

        con.execute("""
            UPDATE meals SET
                fiber_g=?, omega3_g=?, vitamin_d_mcg=?, b12_mcg=?,
                magnesium_mg=?, zinc_mg=?, iron_mg=?, potassium_mg=?,
                sodium_mg=?, vitamin_c_mg=?, vitamin_e_mg=?, vitamin_b6_mg=?,
                folate_mcg=?, sat_fat_g=COALESCE(sat_fat_g, ?), sugar_g=COALESCE(sugar_g, ?)
            WHERE id=?
        """, (
            totals.get("fiber_g"), totals.get("omega3_g"), vit_d_mcg,
            totals.get("vitamin_b12_ug"), totals.get("magnesium_mg"),
            totals.get("zinc_mg"), totals.get("iron_mg"), totals.get("potassium_mg"),
            totals.get("sodium_mg"), totals.get("vitamin_c_mg"), totals.get("vitamin_e_mg"),
            totals.get("vitamin_b6_mg"), totals.get("folate_ug"),
            totals.get("saturated_fat_g"), totals.get("sugar_g"),
            meal_id,
        ))
        con.commit()
        time.sleep(0.3)  # courtesy delay for USDA API

    con.close()
    print("\nBackfill complete.")


if __name__ == "__main__":
    backfill()
