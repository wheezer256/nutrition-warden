"""Standalone pipeline for testing outside of OpenClaw.
In production, the OpenClaw agent handles LLM parsing and calls gog directly.
"""
import argparse
import os
import sys
import json
import yaml
import subprocess
import psycopg2
import requests
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape import scrape_url  # noqa: E402

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "rules.yaml")
PROCESS_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "process_meal.py")


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
    print(f"Calling LLM ({model})...", file=sys.stderr)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    response = requests.post(llm_api_url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _extract_json(raw):
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0]
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0]
    raw = raw.strip()
    if not raw:
        raise RuntimeError("LLM returned an empty response")
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]
    return json.loads(raw)


def parse_meal(input_text, rules, llm_api_url):
    limit_cats = ", ".join([l["category"] for l in rules["limits"]])
    prompt = f"""Parse the following recipe into structured JSON.
Convert all measurements to METRIC units.

Assign descriptive categories freely (e.g. "Salad", "Seafood", "Asian", "Vegetarian", "Soup", "Quick", "High-Protein", etc.).
Only include "{limit_cats}" if the recipe GENUINELY uses those cooking methods — do NOT force these labels onto recipes that don't involve them.
Set "is_high_protein" to true only if the meal has >=30g protein per serving.
Set "servings" to the number of servings the recipe makes (integer, default 4 if unknown).

Set "prep_days_before" to the number of days advance preparation is required (0 for same-day, 1 if ingredients must be soaked/marinated overnight, 2-3 for long braises or sous vide, etc.). Read the instructions carefully to determine this.

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

Input: {input_text}"""
    return _extract_json(call_llm(prompt, llm_api_url))


def parse_delivery(description, llm_api_url):
    prompt = f"""Parse this food delivery order into structured JSON.
Estimate categories and protein content from the meal names.
No ingredients, instructions, or shopping list needed.
Set "servings" to 1 (single order).

Output ONLY raw JSON, no preamble:
{{
    "name": "Meal Name (summarise order if multiple items)",
    "categories": ["Category1"],
    "is_high_protein": true,
    "servings": 1,
    "ingredients": [],
    "instructions": [],
    "shopping_list": []
}}

Input: {description}"""
    return _extract_json(call_llm(prompt, llm_api_url))


def run_process_meal(meal_data):
    result = subprocess.run(
        [sys.executable, PROCESS_SCRIPT, json.dumps(meal_data)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"process_meal.py failed: {result.stderr}")
    return json.loads(result.stdout)


def main(input_data, cook_date=None, meal_type="dinner", source="home_cooked"):
    llm_api_url = _require_env("LLM_API_URL")

    with open(RULES_PATH, "r") as f:
        rules = yaml.safe_load(f)["rules"]

    if source == "delivery":
        meal_data = parse_delivery(input_data, llm_api_url)
        if not cook_date:
            cook_date = datetime.now().strftime("%Y-%m-%d")
    else:
        text = scrape_url(input_data) if input_data.startswith("http") else input_data
        meal_data = parse_meal(text, rules, llm_api_url)

    cook_date = cook_date or datetime.now().strftime("%Y-%m-%d")
    prep_days = int(meal_data.get("prep_days_before", 0))

    if prep_days > 0:
        cook_dt = datetime.strptime(cook_date, "%Y-%m-%d").date()
        prep_dt = cook_dt - timedelta(days=prep_days)
        if prep_dt < datetime.now().date():
            earliest = datetime.now().date() + timedelta(days=prep_days)
            print(json.dumps({
                "status": "prep_conflict",
                "meal_name": meal_data["name"],
                "prep_days_before": prep_days,
                "requested_date": cook_date,
                "earliest_date": earliest.strftime("%Y-%m-%d"),
            }))
            return

    meal_data["cook_date"] = cook_date
    meal_data["meal_type"] = meal_type
    meal_data["source"] = source

    output = run_process_meal(meal_data)

    if output["status"] == "rejected":
        print(f"REJECTED: {', '.join(output['violations'])}")
        suggestion_prompt = (
            f"Suggest a compliant, high-protein alternative to '{output['meal_name']}'. "
            f"Avoid these categories: {output['categories']}."
        )
        suggestion = call_llm(suggestion_prompt, llm_api_url)
        print(f"SUGGESTION:\n{suggestion}")
        return

    msg = f"SUCCESS: {output['meal_name']} added."
    if cook_date:
        msg += f" Scheduled for {cook_date}."
    if meal_type != "dinner":
        msg += f" ({meal_type.capitalize()})"
    if source == "delivery":
        msg += " [Delivery]"
    if output.get("warnings"):
        msg += f" Warnings: {'; '.join(output['warnings'])}"
    print(msg)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NutritionWarden pipeline")
    parser.add_argument("input", nargs="?", help="Recipe URL, text, or delivery order description")
    parser.add_argument("--cook-date", help="Date to cook the meal (YYYY-MM-DD)")
    parser.add_argument("--meal-type", default="dinner",
                        choices=["breakfast", "lunch", "dinner", "snack", "side"],
                        help="Meal type (default: dinner)")
    parser.add_argument("--source", default="home_cooked",
                        choices=["home_cooked", "delivery"],
                        help="Meal source (default: home_cooked)")
    args = parser.parse_args()

    input_data = args.input
    if not input_data:
        if not sys.stdin.isatty():
            input_data = sys.stdin.read().strip()
        else:
            parser.print_help()
            sys.exit(1)

    main(input_data, args.cook_date, args.meal_type, args.source)
