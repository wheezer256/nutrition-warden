#!/usr/bin/env python3
"""Handle meal suggestions from tier 3 users.
Stores the suggestion, notifies tier 1 users, prints reply for the sender.
"""
import argparse
import os
import shutil
import sys
import json
import yaml
import psycopg2
import requests
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape import scrape_url  # noqa: E402

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "rules.yaml")


def _require_env(name):
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required environment variable '{name}' is not set.")
    return val


def _openclaw_argv():
    node = os.environ.get("OPENCLAW_NODE") or shutil.which("node")
    js = os.environ.get("OPENCLAW_JS")
    if node and js:
        return [node, js]
    openclaw = shutil.which("openclaw")
    if openclaw:
        return [openclaw]
    raise RuntimeError("openclaw not found. Set OPENCLAW_NODE+OPENCLAW_JS or ensure openclaw is in PATH.")


def call_llm(prompt, llm_api_url):
    headers = {"Content-Type": "application/json"}
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        headers["Authorization"] = f"Bearer {groq_key}"
    model = os.environ.get("LLM_MODEL") or ("meta-llama/llama-4-scout-17b-16e-instruct" if "groq.com" in llm_api_url else "ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    response = requests.post(llm_api_url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def extract_meal_name(recipe_input, llm_api_url):
    if recipe_input.startswith("http"):
        try:
            content = scrape_url(recipe_input)
        except Exception:
            content = recipe_input
    else:
        content = recipe_input
    prompt = f"""What is the name of the meal in the following recipe text?
Reply with ONLY the meal name, nothing else. 3-6 words max.

Input: {content[:500]}"""
    return call_llm(prompt, llm_api_url)


def send_whatsapp(phone, message):
    subprocess.run(
        _openclaw_argv() + ["message", "send",
         "--channel", "whatsapp",
         "--target", phone,
         "--message", message],
        capture_output=True, text=True
    )


def main(sender_phone, recipe_input):
    llm_api_url = _require_env("LLM_API_URL")
    conn_str = _require_env("DB_CONN_STR")

    with open(RULES_PATH) as f:
        rules = yaml.safe_load(f)["rules"]

    sender = next((u for u in rules["users"] if u["phone"] == sender_phone), None)
    sender_name = sender["name"] if sender else "Someone"

    meal_name = extract_meal_name(recipe_input, llm_api_url)

    # Store suggestion
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO suggestions (suggested_by_phone, suggested_by_name, recipe_input, meal_name)"
        " VALUES (%s, %s, %s, %s)",
        (sender_phone, sender_name, recipe_input, meal_name),
    )
    conn.commit()
    cur.close()
    conn.close()

    # Notify all tier 1 users with the recipe included so they can forward it
    tier1 = [u for u in rules["users"] if u.get("tier") == 1]
    recipe_preview = recipe_input if len(recipe_input) <= 300 else recipe_input[:300] + "..."
    notification = (
        f"💡 *{sender_name} suggested:* {meal_name}\n\n"
        f"Forward this recipe to add it to the plan:\n{recipe_preview}"
    )
    for user in tier1:
        send_whatsapp(user["phone"], notification)

    tier1_names = [u["name"] for u in rules["users"] if u.get("tier") == 1]
    names_str = " and ".join(tier1_names) if tier1_names else "the household"
    print(f"Got it! I've passed *{meal_name}* on to {names_str} for consideration 👍")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phone", help="Sender's phone number in E.164 format")
    parser.add_argument("recipe_input", help="Recipe URL or text")
    args = parser.parse_args()
    main(args.phone, args.recipe_input)
