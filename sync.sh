#!/usr/bin/env bash
# Sync dev directory → installed OpenClaw skill, then restart the gateway.
set -e
SKILL_DIR="$HOME/.openclaw/skills/nutrition-warden"
WORKSPACE_SKILL_DIR="$HOME/.openclaw/workspace/skills/nutrition-warden"
DEV_DIR="$(cd "$(dirname "$0")" && pwd)"

# Sync scripts and config to both locations
for f in main.py process_meal.py daily_briefing.py suggest_meal.py \
          accept_suggestion.py cancel_meal.py party.py scrape.py update_state.py; do
    cp "$DEV_DIR/scripts/$f" "$SKILL_DIR/scripts/$f"
    cp "$DEV_DIR/scripts/$f" "$WORKSPACE_SKILL_DIR/scripts/$f"
done

cp "$DEV_DIR/config/rules.yaml" "$SKILL_DIR/config/rules.yaml"
cp "$DEV_DIR/config/rules.yaml" "$WORKSPACE_SKILL_DIR/config/rules.yaml"
cp "$DEV_DIR/briefing_cron.sh"  "$SKILL_DIR/briefing_cron.sh"
chmod +x "$SKILL_DIR/briefing_cron.sh"

# Regenerate workspace SKILL.md from template (substitutes {{SKILL_DIR}} and {{USERS_TABLE}})
python3 "$DEV_DIR/build_skill_md.py" "$DEV_DIR/SKILL.md" "$SKILL_DIR" "$DEV_DIR/config/rules.yaml" \
    > "$WORKSPACE_SKILL_DIR/SKILL.md"

systemctl --user restart openclaw-gateway.service
echo "Synced and restarted."
