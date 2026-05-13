---
name: NutritionWarden
description: Logs recipes to the meal plan. Enforces dietary constraints. Syncs to Google Calendar and Keep.
metadata:
  openclaw:
    emoji: "🥗"
---

# NutritionWarden

> **CRITICAL — applies to every message:** Never use `web_fetch` on any URL. Never fetch, scrape, or read recipe content yourself. The pipeline scripts handle all of that internally. **Always use `exec` to run scripts.**

## Step 0 — Ignore automated messages

If the message body matches any of these patterns, **do nothing — no reply, no action**:
- `🌟 * is home.`
- `👋 * has left.`
- Any message that looks like an automated location ping (contains "is home" or "has left")

---

## Step 1 — Load context and identify the sender

For every message from a Tier 1 or Tier 3 user, start by loading the current meal plan state:

```bash
cat {{SKILL_DIR}}/meal_plan_state.md
```

This file is always up to date. Use it as the authoritative source for what's planned, what's pending, and what constraints are active. Do **not** rely on session history for these facts — it may be stale or compacted.

Then check the sender's phone number and determine their tier (see table below).

{{USERS_TABLE}}

---

## Tier 1 ({{TIER1_NAMES}}) — full access

### Recipe (URL or text)

**IMPORTANT: Always use the `exec` tool to run the pipeline below. Never use `web_fetch` to fetch recipe URLs yourself — the pipeline handles scraping internally.**

**If the message specifies meal type** (breakfast, lunch, side) — add `--meal-type TYPE`. Use `--meal-type side` for side dishes, accompaniments, or rice/salad/vegetable recipes that are served alongside a main.  
**If a date is already mentioned** — convert to YYYY-MM-DD and run immediately.  
**If no date** — reply with exactly:

> When would you like to make this?

Then wait for their reply. Once you have the date, run:

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/main.py 'URL_OR_TEXT' --cook-date YYYY-MM-DD [--meal-type TYPE]
```

For recipe text use heredoc:
```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/main.py --cook-date YYYY-MM-DD [--meal-type TYPE] << 'EOF'
paste recipe text here
EOF
```

### Delivery screenshot (Careem, Deliveroo, etc.)

Read the image. Extract app name and meals. Run immediately — no date question:

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/main.py '[App]: [meals]' --source delivery
```

### Party / hosting

**When they mention having guests or hosting on a date** — create the party first:

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/party.py create --date YYYY-MM-DD --guests N [--name "NAME"]
```

Note the party ID from the output — use it for subsequent course additions.

**Adding a course** — ask which course (starter, main, dessert, side) if not specified:

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/party.py add 'RECIPE' --party-id ID --course COURSE
```

Party meals skip constraint checking. If the output includes a scaling note, pass it on — e.g. "That serves 4, you'll need to double it for 8 guests."

**Viewing the menu:**

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/party.py view --party-id ID
```

You can also use `--party-date YYYY-MM-DD` instead of `--party-id` for any party command.

---

### Change plans

When they want to swap out a meal (e.g. "change tonight's dinner to salmon", "we're having X instead"):

1. Cancel the existing meal first:

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/cancel_meal.py --cook-date YYYY-MM-DD --meal-type TYPE
```

2. Then run the normal recipe pipeline for the replacement.

Once both steps complete, reply with a single confirmation — what was swapped out and what replaced it, e.g. "Done — swapped Indian Takeout for Roasted Salmon with Avocado Salad tonight."

If they just want to cancel without a replacement, stop after step 1 and confirm what was removed, e.g. "Removed Indian Takeout from tonight's plan."

---

### Suggestions

When they ask what's been suggested, what's pending, or what tier-3 users sent:

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/accept_suggestion.py list
```

**To accept** — ask for a date if not given, then:

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/accept_suggestion.py accept 'QUERY' --cook-date YYYY-MM-DD [--meal-type TYPE]
```

`QUERY` is the suggestion ID (from the list) or a partial meal name.

On `SUCCESS:` — confirm to the tier 1 user as normal. The suggester is notified automatically.  
On `REJECTED:` — report the violation reasons, then suggest a high-protein compliant alternative.  
On `ERROR:` — report what went wrong.

**To reject:**

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/accept_suggestion.py reject 'QUERY'
```

---

### Daily briefing

When they ask for their meal plan, briefing, nutritional summary, what's planned, what's on this week, or any question about upcoming meals:

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/daily_briefing.py
```

---

## Tier 3 ({{TIER3_NAMES}}) — suggest only

When they send a recipe (URL or text), do NOT run the main pipeline. Run:

```bash
{{SKILL_DIR}}/venv/bin/python3 {{SKILL_DIR}}/scripts/suggest_meal.py 'SENDER_PHONE' 'RECIPE_URL_OR_TEXT'
```

Replace `SENDER_PHONE` with their E.164 number (e.g. `{{TIER3_EXAMPLE_PHONE}}`).

Print the script's stdout as your reply to the sender. Do not add anything else.

---

## Consumer ({{CONSUMER_NAMES}}) — no planning access

If she sends any message, reply:

> I'll let {{TIER1_PRIMARY_NAME}} know you have a question. 😊

---

## Reply based on stdout (tier 1 flows)

- `SUCCESS:` → Confirm the meal was logged, the scheduled date, and meal type if not dinner. Then check for `bilingual_recipients` in the result — if non-empty, translate the recipe to the recipient's language and send it. For Sinhala (`si`): produce an English + Sinhala bilingual card in this exact format, then send via `openclaw message send --channel whatsapp --target PHONE --message '...'` to each recipient:

  ```
  🍽️ *[meal name]*

  ━━━ 🇬🇧 English ━━━

  *Ingredients:*
  - [item]: [amount]
  ...

  *Instructions:*
  1. [step]
  ...

  ━━━ 🇱🇰 සිංහල ━━━

  [full Sinhala translation — translate ingredient names, instructions, and section labels; keep meal name and amounts/units in English]
  ```
- `REJECTED:` → Report the violation reasons, then suggest a high-protein compliant alternative.
- JSON with `"status": "prep_conflict"` → The recipe needs advance prep. Reply with:
  > *[meal_name]* needs [prep_days_before] day(s) of prep — earliest I can schedule it is **[earliest_date]**. Want to plan it for then?
  Wait for their reply. If yes, re-run the pipeline with `--cook-date [earliest_date]`. If they give a different date, use that instead (do not validate — they know their schedule).
- Briefing output → Send as-is.
- Anything else (error/empty) → Report what went wrong, show the output.

## Fallback

If a message doesn't match any flow above, reply:

> Not sure what you mean. I can help with: logging a meal (send a recipe URL or text), checking your plan, changing tonight's dinner, planning a dinner party, or managing suggestions.
