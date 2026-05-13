#!/usr/bin/env python3
"""Generate workspace SKILL.md from template, substituting {{SKILL_DIR}} and {{USERS_TABLE}}."""
import argparse
import sys
import yaml

TIER_LABELS = {
    1: "1 — full access",
    3: "3 — suggest only",
    "consumer": "consumer — no planning",
}


def build_users_table(users):
    rows = ["| Phone | Name | Tier |", "|---|---|---|"]
    for u in users:
        tier = u.get("tier", "?")
        rows.append(f"| {u['phone']} | {u['name']} | {TIER_LABELS.get(tier, str(tier))} |")
    return "\n".join(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("template", help="Path to SKILL.md template")
    parser.add_argument("skill_dir", help="Absolute path to the installed skill directory")
    parser.add_argument("rules_yaml", help="Path to config/rules.yaml")
    args = parser.parse_args()

    with open(args.template) as f:
        template = f.read()

    with open(args.rules_yaml) as f:
        rules = yaml.safe_load(f)["rules"]

    users = rules.get("users", [])
    tier1 = [u for u in users if u.get("tier") == 1]
    tier3 = [u for u in users if u.get("tier") == 3]
    consumers = [u for u in users if u.get("tier") == "consumer"]

    users_table = build_users_table(users)
    tier1_names = ", ".join(u["name"] for u in tier1) or "Tier1Users"
    tier3_names = ", ".join(u["name"] for u in tier3) or "Tier3Users"
    consumer_names = ", ".join(u["name"] for u in consumers) or "ConsumerUsers"
    tier1_primary = tier1[0]["name"] if tier1 else "the household"
    tier3_example_phone = tier3[0]["phone"] if tier3 else "+12345000003"

    result = (
        template
        .replace("{{SKILL_DIR}}", args.skill_dir)
        .replace("{{USERS_TABLE}}", users_table)
        .replace("{{TIER1_NAMES}}", tier1_names)
        .replace("{{TIER3_NAMES}}", tier3_names)
        .replace("{{CONSUMER_NAMES}}", consumer_names)
        .replace("{{TIER1_PRIMARY_NAME}}", tier1_primary)
        .replace("{{TIER3_EXAMPLE_PHONE}}", tier3_example_phone)
    )
    sys.stdout.write(result)


if __name__ == "__main__":
    main()
