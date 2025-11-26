import requests
from datetime import datetime, date
import pytz
from google.adk.tools import FunctionTool

def safe_float(x):
    """
    Converts OpenFoodFacts values to float safely.
    Returns 0.0 if value is invalid (None, string, empty, etc.)
    """
    try:
        if x is None:
            return 0.0
        if isinstance(x, (int, float)):
            return float(x)
        # Replace comma with dot: "12,5" → "12.5"
        return float(str(x).replace(",", "."))
    except:
        return 0.0

def today_key():
    tz = pytz.timezone("Europe/Moscow")  # или Asia/Singapore, либо local
    today = datetime.now(tz).date().isoformat()
    return f"user:nutrition:{today}"

def today_history_key():
    return today_key() + ":history"
# =====================================================
# 1. OPENFOODFACTS: CALCULATE MACROS
# =====================================================
def openfood_search(product_name: str):
    """Search OpenFoodFacts for nutrition per 100g."""
    url = "https://world.openfoodfacts.org/cgi/search.pl"
    params = {
        "search_terms": product_name,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 1,
    }
    headers = {
    "User-Agent": "Mozilla/5.0 (compatible; CalorieTrackerBot/1.0; +https://example.com)"
    }

    r = requests.get(url, params=params, headers=headers, timeout=10)
    try:
        data = r.json()
        if "products" not in data or len(data["products"]) == 0:
            return None
        p = data["products"][0]
        nutr = p.get("nutriments", {})
        return {
            "kcal_100g": nutr.get("energy-kcal_100g"),
            "protein_100g": nutr.get("proteins_100g"),
            "fat_100g": nutr.get("fat_100g"),
            "carbs_100g": nutr.get("carbohydrates_100g"),
        }
    except:
        return None

openfood_search_tool = FunctionTool(openfood_search)

def calculate_calories(ingredients: str, tool_context=None) -> dict:
    """
    Parse food text ('200 g potatoes') and compute macros.
    Now stable: safely converts API values, avoids crashes.
    """
    # Store original text in tool state
    if tool_context is not None and hasattr(tool_context, "state"):
        tool_context.state["last_food_text"] = ingredients

    # --- Parse text ---
    words = ingredients.lower().replace(",", " ").split()
    entries = []
    i = 0

    while i < len(words):
        # match: "200 g ..." or "200 grams ..."
        if words[i].replace(".", "").isdigit() and (i+1 < len(words)) and words[i+1] in ["g", "gram", "grams"]:
            grams = float(words[i])

            name = []
            j = i + 2
            while j < len(words) and not (words[j].replace(".", "").isdigit()):
                name.append(words[j])
                j += 1

            product = " ".join(name).strip()
            if product:
                entries.append((grams, product))

            i = j
        else:
            i += 1

    # --- Compute totals ---
    total = {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}

    for grams, prod in entries:
        data = openfood_search(prod)
        if not data:
            print(f"[WARN] No data for: {prod}")
            continue

        # Safely convert everything
        kcal_100g   = safe_float(data.get("kcal_100g"))
        prot_100g   = safe_float(data.get("protein_100g"))
        fat_100g    = safe_float(data.get("fat_100g"))
        carbs_100g  = safe_float(data.get("carbs_100g"))

        if all(v == 0 for v in [kcal_100g, prot_100g, fat_100g, carbs_100g]):
            print(f"[WARN] Product has invalid nutrient data: {prod}")
            continue

        factor = grams / 100

        total["kcal"]    += kcal_100g * factor
        total["protein"] += prot_100g * factor
        total["fat"]     += fat_100g * factor
        total["carbs"]   += carbs_100g * factor

    return total


calculate_tool = FunctionTool(calculate_calories)


# =====================================================
# 2. UPDATE DAILY TOTALS
# =====================================================
def update_daily_totals(
    delta_kcal: float,
    delta_protein: float,
    delta_fat: float,
    delta_carbs: float,
    tool_context
) -> dict:

    state = tool_context.state
    key = today_key()
    history_key = today_history_key()

    # Тоталы за день
    if key not in state:
        state[key] = {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}

    prev = state[key].copy()

    state[key]["kcal"] += float(delta_kcal)
    state[key]["protein"] += float(delta_protein)
    state[key]["fat"] += float(delta_fat)
    state[key]["carbs"] += float(delta_carbs)

    for k in state[key]:
        state[key][k] = round(max(state[key][k], 0.0), 2)

    # История еды
    if history_key not in state:
        state[history_key] = []

    food_text = state.get("last_food_text")  # ← текст сохраняется calculate_calories

    # Пишем в историю ТОЛЬКО при ADD (положительные КБЖУ)
    if food_text and delta_kcal > 0:
        state[history_key].append({
            "text": food_text,
            "kcal": round(delta_kcal, 2),
            "protein": round(delta_protein, 2),
            "fat": round(delta_fat, 2),
            "carbs": round(delta_carbs, 2),
        })

    print(
        "[DEBUG update_daily_totals]",
        "prev=", prev,
        "delta=", delta_kcal, delta_protein, delta_fat, delta_carbs,
        "new=", state[key],
        flush=True
    )

    return {
        "date": key,
        "totals": state[key],
        "history": state.get(history_key, [])
    }
update_totals_tool = FunctionTool(update_daily_totals)

# =====================================================
# 3. RESET DAILY TOTALS
# =====================================================
def reset_daily_totals(tool_context):
    state = tool_context.state
    key = today_key()
    history_key = today_history_key()

    state[key] = {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0}
    state[history_key] = []

    return {"status": "reset", "date": key, "totals": state[key], "history": []}

reset_tool = FunctionTool(reset_daily_totals)


# =====================================================
# 4. GET DAILY TOTALS (HISTORY)
# =====================================================
def get_daily_totals(date_str: str, tool_context):
    state = tool_context.state

    key = today_key() if date_str == "today" else f"user:nutrition:{date_str}"
    history_key = key + ":history"

    totals = state.get(key)
    history = state.get(history_key, [])

    if not totals:
        return {"status": "not_found", "date": date_str}

    return {
        "status": "ok",
        "date": date_str,
        "totals": totals,
        "history": history
    }

get_totals_tool = FunctionTool(get_daily_totals)


# =====================================================
# 5. REMOVE FOOD — отдельный инструмент!
# =====================================================
def remove_food(ingredients: str, tool_context):
    totals = calculate_calories(ingredients)
    return update_daily_totals(
        -totals["kcal"],
        -totals["protein"],
        -totals["fat"],
        -totals["carbs"],
        tool_context
    )

remove_tool = FunctionTool(remove_food)