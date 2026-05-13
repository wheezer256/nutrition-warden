#!/usr/bin/env bash
# Install NutritionWarden into the OpenClaw skill directory.
# Creates the skill directory, sets up the venv, and generates SKILL.md from the template.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="${1:-$HOME/.openclaw/skills/nutrition-warden}"
WORKSPACE_SKILL_DIR="$HOME/.openclaw/workspace/skills/nutrition-warden"

echo "Installing to: $SKILL_DIR"

# Create directories
mkdir -p "$SKILL_DIR/scripts" "$SKILL_DIR/config"
mkdir -p "$WORKSPACE_SKILL_DIR/scripts" "$WORKSPACE_SKILL_DIR/config"

# Copy scripts and config
cp "$SCRIPT_DIR"/scripts/*.py "$SKILL_DIR/scripts/"
cp "$SCRIPT_DIR/config/rules.yaml" "$SKILL_DIR/config/rules.yaml"
cp "$SCRIPT_DIR/briefing_cron.sh" "$SKILL_DIR/briefing_cron.sh"
chmod +x "$SKILL_DIR/briefing_cron.sh"

# Copy to workspace
cp "$SCRIPT_DIR"/scripts/*.py "$WORKSPACE_SKILL_DIR/scripts/"
cp "$SCRIPT_DIR/config/rules.yaml" "$WORKSPACE_SKILL_DIR/config/rules.yaml"

# Copy rules.yaml from example if not already present
if [ ! -f "$SCRIPT_DIR/config/rules.yaml" ]; then
    cp "$SCRIPT_DIR/config/rules.example.yaml" "$SCRIPT_DIR/config/rules.yaml"
    echo "Created config/rules.yaml from example — edit it with your users and preferences."
fi

# Generate SKILL.md with absolute paths and user table substituted in
python3 "$SCRIPT_DIR/build_skill_md.py" "$SCRIPT_DIR/SKILL.md" "$SKILL_DIR" "$SCRIPT_DIR/config/rules.yaml" \
    > "$WORKSPACE_SKILL_DIR/SKILL.md"

# Set up venv if not already present
if [ ! -d "$SKILL_DIR/venv" ]; then
    echo "Creating venv..."
    python3 -m venv "$SKILL_DIR/venv"
    "$SKILL_DIR/venv/bin/pip" install --quiet psycopg2-binary pyyaml requests yt-dlp
    echo "Venv ready."
fi

echo "Done. Run 'systemctl --user restart openclaw-gateway.service' to reload."
