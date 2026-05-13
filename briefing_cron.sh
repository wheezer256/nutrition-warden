#!/usr/bin/env bash
# Daily meal plan briefing — runs via systemd timer, sends to WhatsApp.
# All config comes from env vars. Set them in openclaw.json skill env or source .env.local.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Source local overrides if present (gitignored, never committed)
if [ -f "$SCRIPT_DIR/.env.local" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env.local"
fi

: "${SKILL_DIR:=$HOME/.openclaw/skills/nutrition-warden}"
: "${DB_CONN_STR:?DB_CONN_STR is required}"

PYTHON="$SKILL_DIR/venv/bin/python3"
SCRIPT="$SKILL_DIR/scripts/daily_briefing.py"

BRIEFING=$(DB_CONN_STR="$DB_CONN_STR" "$PYTHON" "$SCRIPT" 2>/dev/null)

if [ -z "$BRIEFING" ]; then
    exit 0
fi

send() {
    openclaw message send \
        --channel whatsapp \
        --target "$1" \
        --message "$BRIEFING"
}

: "${BRIEFING_RECIPIENTS:?BRIEFING_RECIPIENTS is required (space-separated E.164 numbers, e.g. \"+12345000001 +12345000002\")}"

for recipient in $BRIEFING_RECIPIENTS; do
    send "$recipient"
done
