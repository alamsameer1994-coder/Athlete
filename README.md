# Athlete Coach

A local MCP server that unifies your **Strava** and **Garmin** data into one
SQLite database and gives Claude tools to act as your AI athlete coach:
analyze your training week, adjust upcoming weeks based on fatigue/recovery
signals, build/rebuild periodized plans for a race, and coach calories, body
composition, and strength.

It runs entirely on your machine. Your Strava tokens, Garmin credentials, and
all training/body/nutrition data stay in a local SQLite file
(`~/.athlete_coach/athlete.db`) — nothing is sent anywhere except to
Strava's/Garmin's own APIs to pull your data.

## Why no TrainingPeaks integration?

TrainingPeaks' API requires an approved partner agreement that individual
developers generally can't get. Instead, this system treats Strava (+ Garmin)
as the source of truth for *actuals*, and the AI coach builds/adjusts your
plan directly — you can export any plan to a standard `.ics` calendar file
(`export_plan_ics`) to view it anywhere, including importing into TrainingPeaks
manually if you want.

## Architecture

```
Strava API v3  ──┐
                  ├─▶ SQLite (~/.athlete_coach/athlete.db) ─▶ MCP tools ─▶ Claude
Garmin Connect ──┘        (activities, daily wellness,
 (unofficial lib)          body logs, plans, notes)
```

- **`strava_client.py`** — your own Strava OAuth app (one-time browser auth),
  token refresh handled automatically.
- **`garmin_client.py`** — wraps the unofficial `garminconnect` library using
  your Garmin login. This is a reverse-engineered client; if Garmin changes
  their backend it may need a `pip install -U garminconnect`.
- **`sync/`** — pulls from both, normalizes into the `activities` table, and
  de-duplicates Garmin activities that already appear via Strava auto-upload
  (flagged via `duplicate_of` rather than dropped, so Garmin-only fields
  aren't lost).
- **`coaching/`** — pure-logic modules:
  - `training_load.py` — TSS per activity (power/HR/RPE-based, with a
    duration-only fallback) and CTL/ATL/TSB (fitness/fatigue/form).
  - `plan_builder.py` — periodized block structure (base → build → peak →
    taper, deload every 4th week) and a starter session skeleton.
  - `plan_adjuster.py` — planned-vs-actual analysis and a rule-of-thumb
    suggested volume adjustment from TSB, acute:chronic ratio, adherence,
    and sleep trend.
  - `nutrition.py` — BMR (Mifflin-St Jeor) + NEAT baseline + real logged
    training calories → TDEE, goal-based calorie/macro targets, and an
    adaptive-TDEE-style calorie adjustment based on your actual weight trend.
  - `strength.py` — session consistency tracking + phase-based strength
    focus guidance.
- **`server.py`** — the MCP server (FastMCP) wiring all of the above as tools.

**Design choice:** the code provides structure and real data (periodization
math, load calculations, weight trends) — it deliberately does *not* hardcode
"the AI coach's" workout prescriptions or final decisions in Python. Claude
reads the data via these tools and does the actual coaching reasoning and
conversation, then writes decisions back (e.g. `upsert_planned_workout`,
`apply_week_adjustment`). That's what makes it an *AI* coach rather than a
rules engine with a chat window on top.

## Setup

### 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
```

- **Strava**: create an API app at https://www.strava.com/settings/api
  (Authorization Callback Domain: `localhost`). Put the Client ID/Secret in
  `.env`.
- **Garmin**: put your Garmin Connect email/password in `.env`. (Consider a
  dedicated Garmin account or app-specific credentials if you're not
  comfortable storing your main password in a local file — Garmin has no
  official OAuth app flow for this.)

### 3. Initialize the DB and authorize Strava

```bash
athlete-coach-init-db
athlete-coach-strava-auth   # opens a browser once, saves a refresh token
```

Garmin doesn't need a separate step — the first sync will log in and cache
the session.

### 4. Add to Claude Desktop / Claude Code

Claude Desktop (`claude_desktop_config.json`) or Claude Code MCP config:

```json
{
  "mcpServers": {
    "athlete-coach": {
      "command": "/absolute/path/to/.venv/bin/athlete-coach",
      "env": {
        "ATHLETE_COACH_HOME": "/absolute/path/to/home/.athlete_coach"
      }
    }
  }
}
```

(If you keep `.env` in the project directory, set `"cwd"` to the project
directory instead of duplicating env vars here.)

Restart Claude Desktop / Claude Code. You should see `athlete-coach` tools
available (sync_strava, sync_garmin, get_week_summary, create_plan, etc).

## First conversation with your coach

```
You: Sync my data and set my profile — FTP 250W, threshold HR 172, 34 years
     old, male, 178cm. Goal: fat loss, targeting -0.4%/week.
Claude: [calls sync_all, update_settings]

You: I'm racing a marathon on 2026-04-19. I can currently handle about
     6 hours/week and want to peak around 10 hours/week. Build me a plan.
Claude: [calls create_race_target, create_plan, then reviews/edits the
         default sessions with upsert_planned_workout based on your history]

You: How did this week go, and what should I do next week?
Claude: [calls get_week_summary, suggest_week_adjustment, get_fitness_trend,
         then explains + optionally calls apply_week_adjustment /
         upsert_planned_workout]

You: Am I losing fat at the rate I wanted? Adjust my calories if not.
Claude: [calls get_body_comp_trend, review_calorie_adherence,
         get_nutrition_targets]
```

## Tests

```bash
pytest
```

## Caveats

- **Garmin access is unofficial.** `garminconnect` reverse-engineers Garmin
  Connect's own API; it can break on Garmin-side changes and its use may be
  against Garmin's Terms of Service. Use at your own discretion.
- **TSS estimates are approximate**, especially the HR-based and RPE-based
  fallbacks — treat CTL/ATL/TSB as directional trends, not lab-grade numbers.
- **Nutrition/strength guidance is a heuristic starting point**, not medical
  advice — large or sustained deficits/surpluses should involve a
  professional, especially alongside high training load.
