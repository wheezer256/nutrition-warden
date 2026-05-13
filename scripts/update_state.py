#!/usr/bin/env python3
"""Write meal_plan_state.md from live DB data.
Called after any mutation so the agent always has fresh context without session history."""
import os
import sys
import psycopg2
import yaml
from datetime import datetime, timedelta

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "rules.yaml")
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "meal_plan_state.md")

MEAL_EMOJI = {"breakfast": "🍳", "lunch": "🥗", "dinner": "🍽️", "snack": "🍎"}


def main():
    conn_str = os.environ.get("DB_CONN_STR")
    if not conn_str:
        return

    try:
        with open(RULES_PATH) as f:
            rules = yaml.safe_load(f)["rules"]
    except Exception:
        return

    try:
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor()
        today = datetime.now().date()
        window = rules.get("rolling_window_days", 7)

        cur.execute("""
            SELECT meal_name, cook_date, meal_type, source
            FROM meals
            WHERE cook_date BETWEEN %s AND %s AND party_id IS NULL
            ORDER BY cook_date, meal_type
        """, (today, today + timedelta(days=7)))
        upcoming = cur.fetchall()

        cur.execute("""
            SELECT meal_name, categories, is_high_protein
            FROM meals WHERE date_added >= %s
        """, (today - timedelta(days=window),))
        rolling = cur.fetchall()

        try:
            cur.execute("""
                SELECT meal_name, prep_date, cook_date
                FROM meals WHERE prep_date BETWEEN %s AND %s
                ORDER BY prep_date
            """, (today, today + timedelta(days=3)))
            preps = cur.fetchall()
        except Exception:
            conn.rollback()
            preps = []

        cur.execute("""
            SELECT id, suggested_by_name, meal_name
            FROM suggestions WHERE status = 'pending'
            ORDER BY created_at
        """)
        pending = cur.fetchall()

        cur.close()
        conn.close()
    except Exception:
        return

    lines = [f"# NutritionWarden State — {today.strftime('%a %d %b %Y')}", ""]

    lines.append("## Upcoming meals")
    if upcoming:
        for name, cook_date, meal_type, source in upcoming:
            if cook_date == today:
                label = "Today"
            elif cook_date == today + timedelta(days=1):
                label = "Tomorrow"
            else:
                label = cook_date.strftime("%a %d %b")
            emoji = "🛵" if source == "delivery" else MEAL_EMOJI.get(meal_type, "🍽️")
            lines.append(f"- {label}: {emoji} {name} ({meal_type})")
    else:
        lines.append("- Nothing planned yet")
    lines.append("")

    if preps:
        lines.append("## Prep needed")
        for name, prep_date, cook_date in preps:
            if prep_date == today:
                when = "today"
            elif prep_date == today + timedelta(days=1):
                when = "tomorrow"
            else:
                when = prep_date.strftime("%a %d %b")
            lines.append(f"- 🥣 {name} — prep {when} (for {cook_date.strftime('%a %d %b')})")
        lines.append("")

    lines.append("## Constraints (rolling window)")
    all_cats = [c.lower() for m in rolling for c in m[1]]
    hp_target = next(
        (t["min_per_window"] for t in rules.get("targets", []) if t["attribute"] == "is_high_protein"),
        5
    )
    hp_count = sum(1 for m in rolling if m[2])
    lines.append(f"- High-protein: {hp_count}/{hp_target} {'✅' if hp_count >= hp_target else f'⚠️ need {hp_target - hp_count} more'}")
    for limit in rules.get("limits", []):
        cat = limit["category"]
        count = sum(1 for c in all_cats if c == cat.lower())
        status = "⚠️ AT MAX — do not add more" if count >= limit["max_per_window"] else "✅"
        lines.append(f"- {cat}: {count}/{limit['max_per_window']} {status}")
    for target in rules.get("nutritional_targets", []):
        match_cats = [c.lower() for c in target["match_categories"]]
        count = sum(1 for c in all_cats if c in match_cats)
        if "min_per_week" in target:
            ok = "✅" if count >= target["min_per_week"] else f"⚠️ need {target['min_per_week'] - count} more"
            lines.append(f"- {target['group']}: {count}/{target['min_per_week']} {ok}")
        elif "max_per_week" in target:
            ok = "⚠️ over limit" if count > target["max_per_week"] else "✅"
            lines.append(f"- {target['group']}: {count}/{target['max_per_week']} {ok}")
    lines.append("")

    if pending:
        lines.append("## Pending suggestions")
        for sid, by_name, meal_name in pending:
            lines.append(f"- #{sid} {by_name}: {meal_name}")
        lines.append("")

    try:
        with open(STATE_PATH, "w") as f:
            f.write("\n".join(lines))
    except Exception:
        pass


if __name__ == "__main__":
    main()
