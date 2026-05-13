#!/usr/bin/env python3
"""Retry bilingual recipe sends for meals where bilingual_sent = FALSE.
Called from briefing_cron.sh so failures are retried automatically the next morning.
"""
import os
import sys
import psycopg2
import yaml

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "rules.yaml")


def main():
    conn_str = os.environ.get("DB_CONN_STR")
    llm_api_url = os.environ.get("LLM_API_URL")
    if not conn_str or not llm_api_url:
        return

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from process_meal import build_bilingual_recipe, send_whatsapp

    with open(RULES_PATH) as f:
        rules = yaml.safe_load(f)["rules"]
    bilingual_users = [u for u in rules.get("users", []) if u.get("receives_bilingual")]
    if not bilingual_users:
        return

    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, recipe_json, meal_name
        FROM meals
        WHERE bilingual_sent = FALSE
          AND source = 'home_cooked'
          AND jsonb_array_length(recipe_json->'ingredients') > 0
          AND date_added >= CURRENT_DATE - INTERVAL '7 days'
        ORDER BY date_added DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return

    for meal_id, recipe_json, meal_name in rows:
        try:
            msg = build_bilingual_recipe(recipe_json, llm_api_url)
            for user in bilingual_users:
                send_whatsapp(user["phone"], msg)
            conn = psycopg2.connect(conn_str)
            cur = conn.cursor()
            cur.execute("UPDATE meals SET bilingual_sent = TRUE WHERE id = %s", (meal_id,))
            conn.commit()
            cur.close()
            conn.close()
            print(f"Bilingual sent: {meal_name} (id={meal_id})")
        except Exception as e:
            sys.stderr.write(f"Retry failed for meal {meal_id} ({meal_name}): {e}\n")


if __name__ == "__main__":
    main()
