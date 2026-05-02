"""
Nutrition lookup via USDA FoodData Central API.
Searches for foods, returns macros + key micronutrients, logs to DB.

Usage:
  from nutrition import search_food, log_meal_with_nutrition

The daily nutrient targets used by `compute_nutrition_score()` (in the
SCORE_COMPONENTS list) are defaults. Adjust them to fit the user's profile.
"""

import json
import math
import os
import requests
from dotenv import load_dotenv

from db import add_meal_item, get_conn, insert_meal, rollup_meal_items
from profile_targets import get_targets
from time_utils import ensure_tz, local_day, now_local

load_dotenv()

API_KEY = os.getenv("USDA_API_KEY")
BASE_URL = "https://api.nal.usda.gov/fdc/v1"

# USDA nutrient IDs we care about
NUTRIENTS = {
    1008: "calories_kcal",
    1003: "protein_g",
    1004: "fat_g",
    1005: "carbs_g",
    1079: "fiber_g",
    1258: "saturated_fat_g",
    1292: "polyunsaturated_fat_g",
    1109: "vitamin_e_mg",
    1110: "vitamin_d_iu",
    1162: "vitamin_c_mg",
    1089: "iron_mg",
    1090: "magnesium_mg",
    1091: "phosphorus_mg",
    1092: "potassium_mg",
    1093: "sodium_mg",
    1095: "zinc_mg",
    1178: "vitamin_b12_ug",
    1167: "vitamin_b6_mg",
    1166: "folate_ug",
    1404: "omega3_g",
    2000: "sugar_g",
    1063: "sugar_g",
}


def search_food(query: str, data_types: list = None, max_results: int = 5) -> list:
    """
    Search USDA FoodData Central for a food.

    data_types options: "Foundation", "SR Legacy", "Survey (FNDDS)", "Branded"
    Foundation + SR Legacy = most accurate whole foods data
    Branded = packaged products
    """
    if data_types is None:
        data_types = ["Foundation", "SR Legacy"]

    resp = requests.get(
        f"{BASE_URL}/foods/search",
        params={
            "query": query,
            "dataType": ",".join(data_types),
            "pageSize": max_results,
            "api_key": API_KEY,
        }
    )
    resp.raise_for_status()
    foods = resp.json().get("foods", [])
    return foods


def get_food_nutrients(fdc_id: int) -> dict:
    """Fetch full nutrient profile for a specific food by FDC ID."""
    resp = requests.get(
        f"{BASE_URL}/food/{fdc_id}",
        params={"api_key": API_KEY}
    )
    resp.raise_for_status()
    return resp.json()


def extract_nutrients(food_data: dict, serving_g: float = 100.0) -> dict:
    """
    Extract key nutrients from a food record.
    All values are per 100g by default; scale by serving_g/100.
    """
    scale = serving_g / 100.0
    result = {
        "fdc_id": food_data.get("fdcId"),
        "description": food_data.get("description"),
        "serving_g": serving_g,
        "data_type": food_data.get("dataType"),
    }

    nutrients_raw = food_data.get("foodNutrients", [])
    for n in nutrients_raw:
        # Handle both search result and detail endpoint formats
        nutrient_id = None
        amount = None

        if "nutrientId" in n:
            nutrient_id = n["nutrientId"]
            amount = n.get("value")
        elif "nutrient" in n:
            nutrient_id = n["nutrient"].get("id")
            amount = n.get("amount")

        if nutrient_id in NUTRIENTS and amount is not None:
            key = NUTRIENTS[nutrient_id]
            result[key] = round(amount * scale, 2)

    return result


def lookup(query: str, serving_g: float = 100.0, verbose: bool = True, fdc_id: int = None) -> dict:
    """
    Main lookup function. Searches, picks best match, returns nutrient profile.

    Args:
        query: food name, e.g. "grilled salmon", "brown rice", "olive oil"
        serving_g: portion size in grams
        verbose: print results to console
        fdc_id: if provided, skip search and use this specific FDC ID directly

    Returns:
        dict with nutrient values for the serving size
    """
    if fdc_id is None:
        results = search_food(query)

        if not results:
            # Fall back to including branded foods
            results = search_food(query, data_types=["Foundation", "SR Legacy", "Survey (FNDDS)", "Branded"])

        if not results:
            print(f"  No results found for: {query}")
            return {}

        # Pick first result (best match by USDA relevance scoring)
        best = results[0]
        fdc_id = best.get("fdcId")

    # Get full nutrient profile
    food_data = get_food_nutrients(fdc_id)
    nutrients = extract_nutrients(food_data, serving_g)

    if verbose:
        print(f"\n  {query} ({serving_g}g serving)")
        print(f"  Match: {nutrients.get('description')} [{nutrients.get('data_type')}] (FDC {fdc_id})")
        print(f"  Calories:  {nutrients.get('calories_kcal', 'N/A')} kcal")
        print(f"  Protein:   {nutrients.get('protein_g', 'N/A')} g")
        print(f"  Carbs:     {nutrients.get('carbs_g', 'N/A')} g  (fiber {nutrients.get('fiber_g', 'N/A')} g)")
        print(f"  Fat:       {nutrients.get('fat_g', 'N/A')} g  (sat {nutrients.get('saturated_fat_g', 'N/A')} g)")
        if nutrients.get("omega3_g"):
            print(f"  Omega-3:   {nutrients.get('omega3_g')} g")
        micros = []
        for k in ["vitamin_d_iu", "vitamin_b12_ug", "magnesium_mg", "zinc_mg", "iron_mg", "potassium_mg"]:
            val = nutrients.get(k)
            if val:
                micros.append(f"{k.replace('_',' ')}: {val}")
        if micros:
            print(f"  Micros:    {' | '.join(micros)}")

    return nutrients


def lookup_multi(items: list, verbose: bool = True) -> dict:
    """
    Look up multiple foods and return combined totals.

    items: list of (query_string, serving_grams) or (query_string, serving_grams, fdc_id) tuples
    e.g. [("grilled salmon", 200), ("cooked white rice", 180), ("olive oil", 15)]
    Use fdc_id as third element to pin a specific USDA entry when search gives wrong results.

    Returns:
        dict with per-item nutrients and combined totals
    """
    results = []
    totals = {k: 0.0 for k in NUTRIENTS.values()}
    totals["calories_kcal"] = 0.0

    if verbose:
        print("\n" + "="*60)
        print("NUTRITIONAL BREAKDOWN")
        print("="*60)

    for item in items:
        query, serving_g = item[0], item[1]
        fdc_id = item[2] if len(item) > 2 else None
        n = lookup(query, serving_g, verbose=verbose, fdc_id=fdc_id)
        if n:
            results.append(n)
            for key in totals:
                totals[key] = round(totals[key] + n.get(key, 0), 2)

    if verbose and len(items) > 1:
        print(f"\n  {'TOTAL':}")
        print(f"  Calories:  {totals.get('calories_kcal')} kcal")
        print(f"  Protein:   {totals.get('protein_g')} g")
        print(f"  Carbs:     {totals.get('carbs_g')} g  (fiber {totals.get('fiber_g')} g)")
        print(f"  Fat:       {totals.get('fat_g')} g")
        print("="*60)

    return {"items": results, "totals": totals}


def log_meal_with_nutrition(
    description: str,
    items: list,
    meal_type: str = "dinner",
    logged_at: str = None,
    notes: str = None,
):
    """
    Look up nutrition for a meal and store it in the DB.

    items: list of (food_name, serving_grams) tuples
    """
    result = lookup_multi(items, verbose=True)
    totals = result["totals"]

    vit_d_iu = totals.get("vitamin_d_iu")
    vit_d_mcg = round(vit_d_iu / 40.0, 2) if vit_d_iu else None

    logged_at = ensure_tz(logged_at) if logged_at else now_local()
    meal_day = local_day(logged_at)

    with get_conn() as conn:
        meal_id = insert_meal(
            conn,
            logged_at,
            meal_type,
            description,
            calories=round(totals.get("calories_kcal", 0)),
            protein_g=totals.get("protein_g"),
            carbs_g=totals.get("carbs_g"),
            fat_g=totals.get("fat_g"),
            sat_fat_g=totals.get("saturated_fat_g"),
            sugar_g=totals.get("sugar_g"),
            fiber_g=totals.get("fiber_g"),
            omega3_g=totals.get("omega3_g"),
            vitamin_d_mcg=vit_d_mcg,
            b12_mcg=totals.get("vitamin_b12_ug"),
            magnesium_mg=totals.get("magnesium_mg"),
            zinc_mg=totals.get("zinc_mg"),
            iron_mg=totals.get("iron_mg"),
            potassium_mg=totals.get("potassium_mg"),
            sodium_mg=totals.get("sodium_mg"),
            vitamin_c_mg=totals.get("vitamin_c_mg"),
            vitamin_e_mg=totals.get("vitamin_e_mg"),
            vitamin_b6_mg=totals.get("vitamin_b6_mg"),
            folate_mcg=totals.get("folate_ug"),
            notes=notes,
        )
        for idx, item in enumerate(result["items"], start=1):
            vit_d_iu_item = item.get("vitamin_d_iu")
            vit_d_mcg_item = round(vit_d_iu_item / 40.0, 2) if vit_d_iu_item else None
            add_meal_item(conn, meal_id, {
                "item_name": item.get("description"),
                "quantity": item.get("serving_g"),
                "unit": "g",
                "serving_grams": item.get("serving_g"),
                "fdc_id": item.get("fdc_id"),
                "calories": item.get("calories_kcal"),
                "protein_g": item.get("protein_g"),
                "carbs_g": item.get("carbs_g"),
                "fat_g": item.get("fat_g"),
                "sat_fat_g": item.get("saturated_fat_g"),
                "sugar_g": item.get("sugar_g"),
                "fiber_g": item.get("fiber_g"),
                "omega3_g": item.get("omega3_g"),
                "vitamin_d_mcg": vit_d_mcg_item,
                "b12_mcg": item.get("vitamin_b12_ug"),
                "magnesium_mg": item.get("magnesium_mg"),
                "zinc_mg": item.get("zinc_mg"),
                "iron_mg": item.get("iron_mg"),
                "potassium_mg": item.get("potassium_mg"),
                "sodium_mg": item.get("sodium_mg"),
                "vitamin_c_mg": item.get("vitamin_c_mg"),
                "vitamin_e_mg": item.get("vitamin_e_mg"),
                "vitamin_b6_mg": item.get("vitamin_b6_mg"),
                "folate_mcg": item.get("folate_ug"),
                "source": "USDA",
                "source_ref": f"fdc:{item.get('fdc_id')}" if item.get("fdc_id") else None,
                "confidence": "usda",
            }, sort_order=idx)
        rollup_meal_items(conn, meal_id)

        # Store full nutrient detail in journal for future reference
        detail = json.dumps({"items": result["items"], "totals": totals}, indent=2)
        conn.execute(
            "INSERT INTO journal (day, category, note) VALUES (?,?,?)",
            (meal_day, "nutrition", f"Meal: {description}\n{detail}"),
        )

    result["meal_id"] = meal_id
    print(f"Logged: {meal_type} on {meal_day} — {description}")
    return result


# ── Nutrition scoring ─────────────────────────────────────────────────────────
# Gaussian/sigmoid curves with asymmetric penalties.
# Based on AHEI-2010, NRF 9.3, and longevity-specific research.
# See: Freedman et al. (J Nutrition 2022) on exponential scoring functions.

def _sigmoid_up(value, target, steepness=10):
    """0-100. Reaches ~50 at half target, ~95 at target. For beneficial nutrients."""
    if not value or value <= 0:
        return 0.0
    x = value / target
    return 100.0 / (1 + math.exp(-steepness * (x - 0.5)))

def _gaussian(value, target, sigma):
    """100 at target, drops with distance. For calories (symmetric-ish)."""
    if value is None:
        return 0.0
    return 100.0 * math.exp(-0.5 * ((value - target) / sigma) ** 2)

def _gaussian_asym(value, target, sigma_under, sigma_over):
    """Gaussian with different widths for under vs over target."""
    if value is None:
        return 0.0
    sigma = sigma_under if value < target else sigma_over
    return 100.0 * math.exp(-0.5 * ((value - target) / sigma) ** 2)

def _limit_penalty(value, limit, steepness=8):
    """100 when well under limit, drops sharply above. For sat fat, sugar."""
    if value is None:
        return 80.0  # benefit of the doubt if not tracked
    x = value / limit
    return 100.0 / (1 + math.exp(steepness * (x - 1.0)))


def _nak_ratio_score(sodium_mg, potassium_mg, sodium_limit=2300):
    """
    Score based on Na:K ratio. More physiologically accurate than a hard sodium cap.
    Ratio <=0.6 = 100. Gradient penalty as ratio rises. Bottoms out ~2.0+.
    Based on INTERSALT study + NHANES cardiovascular mortality data.
    If potassium is 0/missing, falls back to sodium limit penalty.
    """
    if not sodium_mg:
        return 100.0
    if not potassium_mg or potassium_mg <= 0:
        return _limit_penalty(sodium_mg, sodium_limit)
    ratio = sodium_mg / potassium_mg
    # Score 100 at ratio=0.6, sigmoid decay, ~10 at ratio=2.0
    return 100.0 / (1 + math.exp(6 * (ratio - 1.0)))


def _score_components(targets: dict):
    """Return score component definitions with PROFILE.md target overrides applied."""
    return [
        ("calories",      _gaussian_asym, {"target": targets["calories"], "sigma_under": 350, "sigma_over": 450}, 15),
        ("protein_g",     _sigmoid_up,    {"target": targets["protein_g"], "steepness": 8},                       15),
        ("fiber_g",       _sigmoid_up,    {"target": targets["fiber_g"], "steepness": 10},                       10),
        ("sat_fat_g",     _limit_penalty, {"limit": targets["sat_fat_g_limit"]},                                  8),
        ("sugar_g",       _limit_penalty, {"limit": targets["sugar_g_limit"]},                                    7),
        ("na_k_ratio",    None,           {},                                                                      5),
        ("omega3_g",      _sigmoid_up,    {"target": targets["omega3_g"], "steepness": 8},                        8),
        ("magnesium_mg",  _sigmoid_up,    {"target": targets["magnesium_mg"]},                                    4),
        ("potassium_mg",  _sigmoid_up,    {"target": targets["potassium_mg"]},                                    4),
        ("vitamin_d_mcg", _sigmoid_up,    {"target": targets["vitamin_d_mcg"]},                                   4),
        ("iron_mg",       _sigmoid_up,    {"target": targets["iron_mg"]},                                         4),
        ("b12_mcg",       _sigmoid_up,    {"target": targets["b12_mcg"]},                                         3),
        ("vitamin_c_mg",  _sigmoid_up,    {"target": targets["vitamin_c_mg"]},                                    3),
        ("zinc_mg",       _sigmoid_up,    {"target": targets["zinc_mg"]},                                         4),
        ("vitamin_e_mg",  _sigmoid_up,    {"target": targets["vitamin_e_mg"]},                                    2),
        ("vitamin_b6_mg", _sigmoid_up,    {"target": targets["vitamin_b6_mg"]},                                   2),
        ("folate_mcg",    _sigmoid_up,    {"target": targets["folate_mcg"]},                                      2),
    ]

def compute_nutrition_score(day_totals: dict) -> float:
    """
    Compute a 0-100 nutrition score for a day's aggregated nutrient totals.

    day_totals: dict with keys matching DB column names.
    Missing micro values are excluded from weighting (not penalised).
    Missing macro values (calories/protein) score 0.
    """
    MACRO_KEYS = {"calories", "protein_g", "fiber_g", "sat_fat_g", "sugar_g", "na_k_ratio"}
    total_score = 0.0
    total_weight = 0.0
    targets = get_targets()

    for key, curve_fn, params, weight in _score_components(targets):
        if key == "na_k_ratio":
            score = _nak_ratio_score(
                day_totals.get("sodium_mg"),
                day_totals.get("potassium_mg"),
                targets["sodium_mg_limit"],
            )
        else:
            value = day_totals.get(key)
            # Skip missing micros entirely (don't penalise incomplete USDA data)
            if value is None and key not in MACRO_KEYS:
                continue
            score = curve_fn(value, **params)
        total_score += score * weight
        total_weight += weight

    return round(total_score / total_weight, 1) if total_weight > 0 else None


if __name__ == "__main__":
    # Quick test
    print("Testing USDA lookup...")
    result = lookup_multi([
        ("atlantic salmon cooked", 200),
        ("cooked white rice", 180),
        ("olive oil", 15),
        ("romaine lettuce", 50),
    ])
