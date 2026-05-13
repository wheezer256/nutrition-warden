#!/usr/bin/env python3
"""Daily meal plan briefing with nutritional gap detection."""

import os
import sys
import yaml
import psycopg2
from datetime import datetime, timedelta, date

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "rules.yaml")

MEAL_TYPE_EMOJI = {"breakfast": "🍳", "lunch": "🥗", "dinner": "🍽️", "snack": "🍎"}


def _require_env(name):
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required environment variable '{name}' is not set.")
    return val


def get_rolling_meals(conn_str, days):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).date()
    cur.execute("""
        SELECT meal_name, categories, is_high_protein, cook_date, meal_type, source
        FROM meals
        WHERE date_added >= %s
        ORDER BY cook_date NULLS LAST, date_added
    """, (cutoff,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_prep_reminders(conn_str, days=2):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    today = datetime.now().date()
    end = today + timedelta(days=days)
    cur.execute("""
        SELECT meal_name, prep_date, cook_date
        FROM meals
        WHERE prep_date BETWEEN %s AND %s
        ORDER BY prep_date, cook_date
    """, (today, end))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_upcoming_meals(conn_str, days=3):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    today = datetime.now().date()
    end = today + timedelta(days=days)
    cur.execute("""
        SELECT meal_name, cook_date, meal_type, source
        FROM meals
        WHERE cook_date BETWEEN %s AND %s
        ORDER BY cook_date, meal_type
    """, (today, end))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def check_nutritional_gaps(rolling_meals, targets):
    all_categories = []
    for meal in rolling_meals:
        all_categories.extend([c.lower() for c in meal[1]])

    gaps = []
    for target in targets:
        match = [c.lower() for c in target["match_categories"]]
        count = sum(1 for cat in all_categories if cat in match)

        if "min_per_week" in target and count < target["min_per_week"]:
            gaps.append({
                "group": target["group"],
                "count": count,
                "target": target["min_per_week"],
                "type": "below_min",
                "suggestion": target["suggestion"],
            })
        elif "max_per_week" in target and count > target["max_per_week"]:
            gaps.append({
                "group": target["group"],
                "count": count,
                "target": target["max_per_week"],
                "type": "above_max",
                "suggestion": target["suggestion"],
            })

    return gaps


def format_briefing(rolling_meals, upcoming_meals, prep_reminders, gaps, hp_target, today):
    lines = []
    lines.append(f"📅 *Meal Plan — {today.strftime('%A, %d %b')}*")
    lines.append("")

    # Upcoming 3 days
    lines.append("*This week:*")
    if upcoming_meals:
        by_date = {}
        for meal_name, cook_date, meal_type, source in upcoming_meals:
            if cook_date not in by_date:
                by_date[cook_date] = []
            if source == "delivery":
                label = f"🛵 {meal_name}"
            else:
                emoji = MEAL_TYPE_EMOJI.get(meal_type, "🍽️")
                label = f"{emoji} {meal_name}"
            by_date[cook_date].append(label)

        for d in sorted(by_date.keys()):
            if d == today:
                label = "Today"
            elif d == today + timedelta(days=1):
                label = "Tomorrow"
            else:
                label = d.strftime("%A")
            lines.append(f"  *{label}:* {' · '.join(by_date[d])}")
    else:
        lines.append("  Nothing planned yet — send a recipe to get started.")
    lines.append("")

    # Prep reminders
    if prep_reminders:
        lines.append("*Prep needed:*")
        for meal_name, prep_date, cook_date in prep_reminders:
            if prep_date == today:
                when = "today"
            elif prep_date == today + timedelta(days=1):
                when = "tomorrow"
            else:
                when = prep_date.strftime("%A")
            cook_label = cook_date.strftime("%a %d %b") if cook_date else "?"
            lines.append(f"  🥣 *{meal_name}* — prep {when} (for {cook_label})")
        lines.append("")

    # High protein check
    hp_count = sum(1 for m in rolling_meals if m[2])
    hp_status = "✅" if hp_count >= hp_target else "⚠️"
    lines.append(f"{hp_status} *High-protein meals this week:* {hp_count}/{hp_target}")
    lines.append("")

    # Nutritional gaps
    if gaps:
        lines.append("*Nutritional priorities:*")
        for gap in gaps:
            if gap["type"] == "below_min":
                lines.append(
                    f"  ⚠️ *{gap['group']}:* {gap['count']}/{gap['target']} this week — {gap['suggestion']}"
                )
            else:
                lines.append(
                    f"  ℹ️ *{gap['group']}:* {gap['count']} this week — {gap['suggestion']}"
                )
    else:
        lines.append("✅ *All nutritional targets on track.*")

    return "\n".join(lines)


def main():
    conn_str = _require_env("DB_CONN_STR")

    with open(RULES_PATH) as f:
        rules = yaml.safe_load(f)["rules"]

    window_days = rules["rolling_window_days"]
    hp_target = next(
        (t["min_per_window"] for t in rules.get("targets", []) if t["attribute"] == "is_high_protein"),
        5
    )

    today = datetime.now().date()
    rolling_meals = get_rolling_meals(conn_str, window_days)
    upcoming_meals = get_upcoming_meals(conn_str, days=7)
    prep_reminders = get_prep_reminders(conn_str, days=2)
    gaps = check_nutritional_gaps(rolling_meals, rules.get("nutritional_targets", []))

    print(format_briefing(rolling_meals, upcoming_meals, prep_reminders, gaps, hp_target, today))


if __name__ == "__main__":
    main()
