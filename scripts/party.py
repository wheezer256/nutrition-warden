#!/usr/bin/env python3
"""Party and hosting meal planning."""
import argparse
import os
import sys
import json
import subprocess
import requests
import yaml
import psycopg2
from datetime import datetime, timedelta


def _update_state():
    try:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_state.py")
        subprocess.run([sys.executable, script], capture_output=True, timeout=15)
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape import scrape_url  # noqa: E402

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "rules.yaml")

COURSE_ORDER = ["starter", "side", "main", "dessert", "drink"]
COURSE_EMOJI = {"starter": "🥗", "main": "🍽️", "dessert": "🍮", "side": "🥦", "drink": "🍷"}


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
    model = os.environ.get("LLM_MODEL") or (
        "meta-llama/llama-4-scout-17b-16e-instruct" if "groq.com" in llm_api_url
        else "ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF"
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    r = requests.post(llm_api_url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def parse_recipe(text, rules, llm_api_url):
    limit_cats = ", ".join(l["category"] for l in rules["limits"])
    prompt = f"""Parse the following recipe into structured JSON.
Convert all measurements to METRIC units.
Assign descriptive categories freely. Only include "{limit_cats}" if the recipe genuinely uses those methods.
Set "is_high_protein" to true if >=30g protein per serving. Set "servings" to the number of servings (integer, default 4).
Set "prep_days_before" to the number of days advance preparation is required (0 for same-day, 1 for overnight soak/marinate, 2-3 for long braise/sous vide).

Output ONLY raw JSON, no preamble:
{{
    "name": "Meal Name",
    "categories": ["Category1"],
    "is_high_protein": true,
    "servings": 4,
    "prep_days_before": 0,
    "ingredients": [{{"item": "name", "amount": "metric amount"}}],
    "instructions": ["step 1"],
    "shopping_list": [{{"item": "name", "freshness": "bulk/standard/fresh/day_of"}}]
}}

Input: {text}"""
    raw = call_llm(prompt, llm_api_url)
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0]
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0]
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}") + 1
    return json.loads(raw[start:end] if start != -1 and end > start else raw)


def find_party(cur, party_id=None, party_date=None):
    if party_id:
        cur.execute(
            "SELECT id, name, date, guest_count FROM parties WHERE id = %s",
            (party_id,)
        )
    else:
        cur.execute(
            "SELECT id, name, date, guest_count FROM parties WHERE date = %s ORDER BY id DESC LIMIT 1",
            (party_date,)
        )
    return cur.fetchone()


def cmd_create(args, conn_str):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO parties (name, date, guest_count) VALUES (%s, %s, %s) RETURNING id",
        (args.name, args.date, args.guests)
    )
    party_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    label = f" — {args.name}" if args.name else ""
    print(f"PARTY_CREATED: id={party_id} | {args.guests} guests on {args.date}{label}")


def cmd_add(args, conn_str, llm_api_url):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()

    party = find_party(cur, party_id=args.party_id, party_date=args.party_date)
    if not party:
        print("ERROR: Party not found")
        sys.exit(1)
    party_id, party_name, party_date, guest_count = party

    with open(RULES_PATH) as f:
        rules = yaml.safe_load(f)["rules"]

    text = scrape_url(args.recipe) if args.recipe.startswith("http") else args.recipe
    meal_data = parse_recipe(text, rules, llm_api_url)

    recipe_serves = meal_data.get("servings") or 4
    scaling = guest_count / recipe_serves

    prep_days = int(meal_data.get("prep_days_before", 0))
    if prep_days > 0:
        prep_dt = party_date - timedelta(days=prep_days)
        if prep_dt < datetime.now().date():
            earliest = datetime.now().date() + timedelta(days=prep_days)
            cur.close()
            conn.close()
            print(json.dumps({
                "status": "prep_conflict",
                "meal_name": meal_data["name"],
                "prep_days_before": prep_days,
                "requested_date": str(party_date),
                "earliest_date": earliest.strftime("%Y-%m-%d"),
            }))
            return

    cur.execute(
        """INSERT INTO meals
           (date_added, cook_date, meal_name, categories, is_high_protein, servings,
            meal_type, source, party_id, course, guest_count, overridden, recipe_json)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            datetime.now().date(),
            str(party_date),
            meal_data["name"],
            meal_data["categories"],
            meal_data["is_high_protein"],
            recipe_serves,
            "dinner",
            "home_cooked",
            party_id,
            args.course,
            guest_count,
            True,
            json.dumps(meal_data),
        )
    )
    conn.commit()
    cur.close()
    conn.close()

    msg = f"SUCCESS: {meal_data['name']} added as {args.course} for party on {party_date} ({guest_count} guests)"
    if abs(scaling - 1) > 0.1:
        factor = f"{scaling:.1f}x" if scaling != round(scaling) else f"{int(scaling)}x"
        msg += f". Recipe serves {recipe_serves} — scale {factor} for {guest_count} guests."
    print(msg)
    _update_state()


def cmd_view(args, conn_str):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()

    party = find_party(cur, party_id=args.party_id, party_date=args.party_date)
    if not party:
        print("ERROR: Party not found")
        sys.exit(1)
    party_id, party_name, party_date, guest_count = party

    cur.execute(
        "SELECT meal_name, course FROM meals WHERE party_id = %s ORDER BY meal_name",
        (party_id,)
    )
    courses = cur.fetchall()
    cur.close()
    conn.close()

    label = party_name or "Party"
    print(f"{label} — {guest_count} guests ({party_date.strftime('%a %d %b')})")
    if not courses:
        print("No courses added yet.")
        return
    by_course = {}
    for meal_name, course in courses:
        by_course.setdefault(course or "main", []).append(meal_name)
    for course in COURSE_ORDER:
        if course in by_course:
            emoji = COURSE_EMOJI.get(course, "🍽️")
            for meal in by_course[course]:
                print(f"  {emoji} {course.capitalize()}: {meal}")


def main():
    parser = argparse.ArgumentParser(description="Party meal planning")
    sub = parser.add_subparsers(dest="action", required=True)

    c = sub.add_parser("create")
    c.add_argument("--date", required=True)
    c.add_argument("--guests", type=int, required=True)
    c.add_argument("--name", default=None)

    a = sub.add_parser("add")
    a.add_argument("recipe", help="Recipe URL or text")
    a.add_argument("--party-id", type=int, default=None)
    a.add_argument("--party-date", default=None)
    a.add_argument("--course", required=True,
                   choices=["starter", "main", "dessert", "side", "drink"])

    v = sub.add_parser("view")
    v.add_argument("--party-id", type=int, default=None)
    v.add_argument("--party-date", default=None)

    args = parser.parse_args()
    conn_str = _require_env("DB_CONN_STR")

    if args.action == "create":
        cmd_create(args, conn_str)
    elif args.action == "add":
        cmd_add(args, conn_str, _require_env("LLM_API_URL"))
    elif args.action == "view":
        cmd_view(args, conn_str)


if __name__ == "__main__":
    main()
