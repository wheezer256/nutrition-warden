import os
import sys
import json
import shutil
import subprocess
import yaml
import psycopg2
import requests
from datetime import datetime, timedelta

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "rules.yaml")

MEAL_TYPE_EMOJI = {"breakfast": "🍳", "lunch": "🥗", "dinner": "🍽️", "snack": "🍎", "side": "🥦"}
SINHALA_LANG = "si"


def _require_env(name):
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required environment variable '{name}' is not set.")
    return val


def call_llm(prompt, llm_api_url):
    headers = {"Content-Type": "application/json"}
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        headers["Authorization"] = f"Bearer {groq_key}"
    model = os.environ.get("LLM_MODEL") or ("meta-llama/llama-4-scout-17b-16e-instruct" if "groq.com" in llm_api_url else "ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    response = requests.post(llm_api_url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def _openclaw_argv():
    node = os.environ.get("OPENCLAW_NODE") or shutil.which("node")
    js = os.environ.get("OPENCLAW_JS")
    if node and js:
        return [node, js]
    openclaw = shutil.which("openclaw")
    if openclaw:
        return [openclaw]
    raise RuntimeError("openclaw not found. Set OPENCLAW_NODE+OPENCLAW_JS or ensure openclaw is in PATH.")


def send_whatsapp(phone, message):
    subprocess.run(
        _openclaw_argv() + ["message", "send",
         "--channel", "whatsapp",
         "--target", phone,
         "--message", message],
        capture_output=True, text=True, timeout=30
    )


def build_bilingual_recipe(meal_data, llm_api_url):
    """Translate recipe to Sinhala and return English + Sinhala formatted message."""
    name = meal_data["name"]
    ingredients = "\n".join(f"- {i['item']}: {i['amount']}" for i in meal_data.get("ingredients", []))
    instructions = "\n".join(f"{n}. {s}" for n, s in enumerate(meal_data.get("instructions", []), 1))
    english_text = f"*{name}*\n\n*Ingredients:*\n{ingredients}\n\n*Instructions:*\n{instructions}"

    prompt = f"""Translate the following recipe into Sinhala (සිංහල).
Keep the meal name, ingredient amounts/units in English.
Only translate the ingredient names, instructions, and section labels.

Recipe:
{english_text}

Output ONLY the Sinhala translation, no preamble."""
    sinhala_text = call_llm(prompt, llm_api_url)

    return (
        f"🍽️ *{name}*\n\n"
        f"━━━ 🇬🇧 English ━━━\n\n"
        f"*Ingredients:*\n{ingredients}\n\n"
        f"*Instructions:*\n{instructions}\n\n"
        f"━━━ 🇱🇰 සිංහල ━━━\n\n"
        f"{sinhala_text}"
    )


def init_db(conn_str):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meals (
            id SERIAL PRIMARY KEY,
            date_added DATE NOT NULL,
            cook_date DATE,
            meal_name TEXT NOT NULL,
            categories TEXT[] NOT NULL,
            is_high_protein BOOLEAN NOT NULL,
            nutritional_flags TEXT[] DEFAULT '{}',
            overridden BOOLEAN DEFAULT FALSE,
            meal_type TEXT DEFAULT 'dinner',
            source TEXT DEFAULT 'home_cooked',
            party_id INTEGER,
            course TEXT,
            guest_count INTEGER,
            servings INTEGER,
            recipe_json JSONB NOT NULL
        )
    """)
    # Idempotent migrations for existing installs
    for col, defn in [
        ("cook_date", "DATE"),
        ("nutritional_flags", "TEXT[] DEFAULT '{}'"),
        ("overridden", "BOOLEAN DEFAULT FALSE"),
        ("meal_type", "TEXT DEFAULT 'dinner'"),
        ("source", "TEXT DEFAULT 'home_cooked'"),
        ("party_id", "INTEGER"),
        ("course", "TEXT"),
        ("guest_count", "INTEGER"),
        ("servings", "INTEGER"),
        ("prep_date", "DATE"),
        ("bilingual_sent", "BOOLEAN DEFAULT FALSE"),
    ]:
        cur.execute(f"ALTER TABLE meals ADD COLUMN IF NOT EXISTS {col} {defn}")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parties (
            id SERIAL PRIMARY KEY,
            name TEXT,
            date DATE NOT NULL,
            guest_count INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS suggestions (
            id SERIAL PRIMARY KEY,
            suggested_by_phone TEXT NOT NULL,
            suggested_by_name TEXT,
            recipe_input TEXT NOT NULL,
            meal_name TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def check_constraints(meal_data, rules, conn_str):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    window_days = rules["rolling_window_days"]
    cutoff_date = (datetime.now() - timedelta(days=window_days)).date()
    cur.execute(
        "SELECT categories, is_high_protein FROM meals WHERE date_added >= %s",
        (cutoff_date,),
    )
    history = cur.fetchall()
    cur.close()
    conn.close()

    violations = []
    for limit in rules["limits"]:
        cat = limit["category"]
        max_val = limit["max_per_window"]
        count = sum(1 for h in history if cat in h[0])
        if cat in meal_data["categories"] and count >= max_val:
            violations.append(
                f"Max {max_val} {cat} meals per {window_days} days reached (current: {count})."
            )
    return violations


def format_tables(meal_data):
    ing_table = "| Ingredient | Amount |\n| --- | --- |\n"
    for ing in meal_data["ingredients"]:
        ing_table += f"| {ing['item']} | {ing['amount']} |\n"

    inst_table = "| Step | Description |\n| --- | --- |\n"
    for i, step in enumerate(meal_data["instructions"], 1):
        inst_table += f"| {i} | {step} |\n"

    return ing_table, inst_table


def sync_ha(meal_data, ha_url, ha_token):
    url = f"{ha_url}/api/states/sensor.nutrition_warden_today"
    headers = {"Authorization": f"Bearer {ha_token}", "content-type": "application/json"}
    data = {
        "state": meal_data["name"],
        "attributes": {
            "categories": meal_data["categories"],
            "is_high_protein": meal_data["is_high_protein"],
            "meal_type": meal_data.get("meal_type", "dinner"),
            "source": meal_data.get("source", "home_cooked"),
            "friendly_name": "Today's Meal",
        },
    }
    try:
        requests.post(url, headers=headers, json=data, timeout=10)
    except Exception as e:
        sys.stderr.write(f"Warning: Home Assistant sync failed: {e}\n")


def _run_gog(args, account):
    gog_bin = os.environ.get("GOG_BIN") or shutil.which("gog")
    if not gog_bin:
        raise RuntimeError("gog not found. Install it or set GOG_BIN env var.")
    env = {**os.environ, "GOG_KEYRING_PASSWORD": os.environ.get("GOG_KEYRING_PASSWORD", "y")}
    result = subprocess.run(
        [gog_bin, "-a", account, "--no-input"] + args,
        capture_output=True, text=True, env=env, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def sync_calendar(summary, date_str, calendar_id, description, account, attendees=None):
    args = [
        "calendar", "create", calendar_id,
        "--summary", summary,
        "--from", date_str,
        "--to", date_str,
        "--all-day",
        "--description", description,
    ]
    if attendees:
        args += ["--attendees", ",".join(attendees), "--send-updates", "all"]
    _run_gog(args, account)


def sync_keep(meal_name, shopping_list, cook_date=None):
    if not shopping_list:
        return
    gkeep_py = os.environ.get("GKEEP_PY")
    gkeep_script = os.environ.get("GKEEP_SCRIPT")
    if not gkeep_py or not gkeep_script:
        raise RuntimeError("GKEEP_PY and GKEEP_SCRIPT env vars required for Keep sync.")

    title = f"Shopping: {meal_name}"
    if cook_date:
        title = f"Shopping: {meal_name} ({cook_date})"

    freshness_order = {"day_of": 0, "fresh": 1, "standard": 2, "bulk": 3}
    sorted_items = sorted(
        shopping_list,
        key=lambda x: freshness_order.get(x.get("freshness", "standard"), 2)
    )
    items = [f"[{item['freshness'].upper()}] {item['item']}" for item in sorted_items]

    cmd = [gkeep_py, gkeep_script, "create", "--title", title, "--list", "--items"] + items
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _update_state():
    try:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_state.py")
        subprocess.run([sys.executable, script], capture_output=True, timeout=15)
    except Exception:
        pass


def main(meal_json_str):
    conn_str = _require_env("DB_CONN_STR")
    ha_url = _require_env("HA_URL")
    ha_token = _require_env("HA_TOKEN")
    calendar_id = _require_env("GOOGLE_CALENDAR_ID")
    gog_account = _require_env("GOG_ACCOUNT")
    llm_api_url = os.environ.get("LLM_API_URL", "")

    with open(RULES_PATH, "r") as f:
        rules = yaml.safe_load(f)["rules"]

    meal_data = json.loads(meal_json_str)
    meal_type = meal_data.get("meal_type", "dinner")
    source = meal_data.get("source", "home_cooked")
    cook_date_str = meal_data.get("cook_date") or datetime.now().strftime("%Y-%m-%d")
    prep_days = int(meal_data.get("prep_days_before", 0))
    prep_date_str = (
        (datetime.strptime(cook_date_str, "%Y-%m-%d") - timedelta(days=prep_days)).strftime("%Y-%m-%d")
        if prep_days > 0 else None
    )

    if source == "home_cooked":
        for note_rule in rules.get("persistent_notes", []):
            target = note_rule["ingredient"].lower()
            note_line = f"NOTE: {note_rule['note']}"
            for ing in meal_data["ingredients"]:
                if target in ing["item"].lower() and note_line not in meal_data["instructions"]:
                    meal_data["instructions"].append(note_line)

    init_db(conn_str)
    violations = check_constraints(meal_data, rules, conn_str)

    if violations:
        print(json.dumps({
            "status": "rejected",
            "meal_name": meal_data["name"],
            "categories": meal_data["categories"],
            "violations": violations,
        }))
        return

    # Build calendar summary with emoji prefix
    emoji = MEAL_TYPE_EMOJI.get(meal_type, "🍽️")
    if source == "delivery":
        calendar_summary = f"🛵 {meal_data['name']}"
    else:
        calendar_summary = f"{emoji} {meal_data['name']}"

    ing_table, inst_table = format_tables(meal_data)
    if source == "delivery":
        description = f"Delivery order — {meal_data['name']}"
    else:
        description = f"### Ingredients\n{ing_table}\n\n### Instructions\n{inst_table}"

    attendees = [
        u["email"] for u in rules.get("users", [])
        if u.get("email") and u.get("email") != gog_account
    ]

    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO meals
           (date_added, cook_date, prep_date, meal_name, categories, is_high_protein, servings,
            meal_type, source, recipe_json)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (
            datetime.now().date(),
            cook_date_str,
            prep_date_str,
            meal_data["name"],
            meal_data["categories"],
            meal_data["is_high_protein"],
            meal_data.get("servings"),
            meal_type,
            source,
            json.dumps(meal_data),
        ),
    )
    new_meal_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    sync_ha(meal_data, ha_url, ha_token)

    warnings = []

    try:
        sync_calendar(calendar_summary, cook_date_str, calendar_id, description, gog_account, attendees)
        if prep_date_str:
            sync_calendar(
                f"🥣 Prep: {meal_data['name']}",
                prep_date_str, calendar_id,
                f"Prep day for {meal_data['name']} — cooking on {cook_date_str}.",
                gog_account, attendees
            )
    except Exception as e:
        warnings.append(f"Calendar sync failed: {e}")
        sys.stderr.write(f"Warning: Calendar sync failed: {e}\n")

    # Skip shopping list for delivery orders
    if source == "home_cooked" and meal_data.get("shopping_list"):
        try:
            sync_keep(meal_data["name"], meal_data["shopping_list"], cook_date=cook_date_str)
        except Exception as e:
            warnings.append(f"Keep sync failed: {e}")
            sys.stderr.write(f"Warning: Keep sync failed: {e}\n")

    # Send bilingual recipe to consumer users (e.g. Nilusha) for home-cooked meals only.
    # bilingual_sent tracks success; retry_bilingual.py retries failures on next cron run.
    if source == "home_cooked" and llm_api_url and meal_data.get("ingredients"):
        bilingual_users = [u for u in rules.get("users", []) if u.get("receives_bilingual")]
        if bilingual_users:
            try:
                bilingual_msg = build_bilingual_recipe(meal_data, llm_api_url)
                for user in bilingual_users:
                    send_whatsapp(user["phone"], bilingual_msg)
                conn2 = psycopg2.connect(conn_str)
                cur2 = conn2.cursor()
                cur2.execute("UPDATE meals SET bilingual_sent = TRUE WHERE id = %s", (new_meal_id,))
                conn2.commit()
                cur2.close()
                conn2.close()
            except Exception as e:
                warnings.append(f"Bilingual send queued for retry: {e}")
                sys.stderr.write(f"Warning: Bilingual send failed, will retry: {e}\n")

    result = {
        "status": "success",
        "meal_name": meal_data["name"],
        "cook_date": cook_date_str,
        "meal_type": meal_type,
        "source": source,
        "shopping_items": meal_data.get("shopping_list", []) if source == "home_cooked" else [],
    }
    if warnings:
        result["warnings"] = warnings

    _update_state()
    print(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1])
    else:
        data = sys.stdin.read().strip()
        if data:
            main(data)
        else:
            print("Usage: process_meal.py '<meal JSON>' or pipe JSON via stdin", file=sys.stderr)
            sys.exit(1)
