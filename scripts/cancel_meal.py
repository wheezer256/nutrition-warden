#!/usr/bin/env python3
"""Remove a planned meal by date and meal type."""
import argparse
import os
import subprocess
import sys
import psycopg2
from datetime import datetime, date


def _update_state():
    try:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_state.py")
        subprocess.run([sys.executable, script], capture_output=True, timeout=15)
    except Exception:
        pass


def _require_env(name):
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required environment variable '{name}' is not set.")
    return val


def main():
    parser = argparse.ArgumentParser(description="Cancel a planned meal")
    parser.add_argument("--cook-date", required=True, help="Date of the meal (YYYY-MM-DD or 'today')")
    parser.add_argument("--meal-type", default="dinner",
                        choices=["breakfast", "lunch", "dinner", "snack"])
    args = parser.parse_args()

    cook_date = date.today().isoformat() if args.cook_date == "today" else args.cook_date
    conn_str = _require_env("DB_CONN_STR")

    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM meals WHERE cook_date = %s AND meal_type = %s RETURNING meal_name",
        (cook_date, args.meal_type)
    )
    deleted = cur.fetchall()
    conn.commit()
    cur.close()
    conn.close()

    if not deleted:
        print(f"ERROR: No {args.meal_type} found for {cook_date}")
        sys.exit(1)

    for (meal_name,) in deleted:
        print(f"CANCELLED: {meal_name} ({args.meal_type}, {cook_date})")
    _update_state()


if __name__ == "__main__":
    main()
