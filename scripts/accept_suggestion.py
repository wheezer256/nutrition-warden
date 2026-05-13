#!/usr/bin/env python3
"""Accept or reject tier-3 meal suggestions."""
import argparse
import os
import shutil
import sys
import subprocess
import psycopg2

MAIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


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


def find_suggestion(conn_str, query):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    try:
        suggestion_id = int(query)
        cur.execute(
            "SELECT id, suggested_by_phone, suggested_by_name, recipe_input, meal_name"
            " FROM suggestions WHERE id = %s AND status = 'pending'",
            (suggestion_id,)
        )
    except ValueError:
        cur.execute(
            "SELECT id, suggested_by_phone, suggested_by_name, recipe_input, meal_name"
            " FROM suggestions WHERE status = 'pending' AND meal_name ILIKE %s"
            " ORDER BY created_at DESC LIMIT 1",
            (f"%{query}%",)
        )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def list_pending(conn_str):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, suggested_by_name, meal_name, created_at"
        " FROM suggestions WHERE status = 'pending' ORDER BY created_at DESC"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def set_status(conn_str, suggestion_id, status):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute("UPDATE suggestions SET status = %s WHERE id = %s", (status, suggestion_id))
    conn.commit()
    cur.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Manage meal suggestions from tier-3 users")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("list", help="List pending suggestions")

    accept_p = sub.add_parser("accept", help="Accept a suggestion and add it to the meal plan")
    accept_p.add_argument("query", help="Suggestion ID or partial meal name")
    accept_p.add_argument("--cook-date", required=True, help="Date to cook (YYYY-MM-DD)")
    accept_p.add_argument("--meal-type", default="dinner",
                          choices=["breakfast", "lunch", "dinner", "snack"])

    reject_p = sub.add_parser("reject", help="Reject a suggestion")
    reject_p.add_argument("query", help="Suggestion ID or partial meal name")

    args = parser.parse_args()
    conn_str = _require_env("DB_CONN_STR")

    if args.action == "list":
        rows = list_pending(conn_str)
        if not rows:
            print("No pending suggestions.")
            return
        for sid, name, meal, created in rows:
            print(f"[{sid}] {name}: {meal} ({created.strftime('%d %b')})")
        return

    suggestion = find_suggestion(conn_str, args.query)
    if not suggestion:
        print(f"ERROR: No pending suggestion found matching '{args.query}'")
        sys.exit(1)

    sid, phone, name, recipe_input, meal_name = suggestion

    if args.action == "accept":
        set_status(conn_str, sid, "accepted")

        result = subprocess.run(
            [sys.executable, MAIN_SCRIPT, recipe_input,
             "--cook-date", args.cook_date,
             "--meal-type", args.meal_type],
            capture_output=True, text=True
        )

        if result.returncode != 0:
            set_status(conn_str, sid, "pending")
            print(f"ERROR: Pipeline failed: {result.stderr.strip()}")
            sys.exit(1)

        output = result.stdout.strip()
        print(output)

        if output.startswith("SUCCESS"):
            send_whatsapp(
                phone,
                f"🎉 Great news, {name}! Your suggestion *{meal_name}* has been added"
                f" to the meal plan for {args.cook_date}."
            )

    elif args.action == "reject":
        set_status(conn_str, sid, "rejected")
        print(f"Rejected: {meal_name} (from {name})")
        _update_state()


if __name__ == "__main__":
    main()
