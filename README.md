# Facebook Badminton Post Aggregation Agent

An agentic CLI tool that scrapes Vietnamese Facebook badminton groups, uses an LLM to extract structured data from each post, caches everything locally in SQLite, and lets you search with natural language queries.

## Features

- **Scrapes public Facebook groups** using Playwright (Chromium) with a persistent login session
- **Parses Vietnamese posts** with GPT-4o-mini to extract: play time, location, level, player count, gender breakdown, and more
- **Caches posts locally** in SQLite — re-queries are instant and don't re-scrape
- **Natural language search** — ask "trình độ TBY lúc 7 giờ tối" and get a filtered, formatted table
- **Swappable LLM backends** — switch between OpenAI, Anthropic, and Gemini via a single env var

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`pip install uv` or see [uv install guide](https://docs.astral.sh/uv/getting-started/installation/))
- A Facebook account with access to the target groups
- An OpenAI API key (or Anthropic / Gemini key if using an alternative provider)

## Setup

**1. Clone and install dependencies**

```bash
git clone <repo-url>
cd facebook-post-aggregation-agent
uv sync
```

**2. Install the Playwright browser**

```bash
uv run playwright install chromium
```

**3. Configure environment variables**

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
FB_EMAIL=your_facebook_email@example.com
FB_PASSWORD=your_facebook_password

OPENAI_API_KEY=sk-...
MODEL_NAME=gpt-4o-mini
LLM_PROVIDER=openai
```

## Usage

All commands are run via `uv run python main.py`.

### First run — log in to Facebook

On the first run the browser profile is empty, so launch with `--headed` (the default) to log in manually (handles 2FA, CAPTCHA, etc.). The session is saved to `data/browser_profiles/` and reused automatically on all future runs.

```bash
uv run python main.py fetch --group-url "https://www.facebook.com/groups/<group-id>"
```

> **Note:** `--headed` is the default. Use `--headless` only if you are sure your session is still active, since Facebook may return 0 posts for headless requests.

---

### `fetch` — Scrape and cache posts

```bash
# Fetch the last 12 hours of posts (up to 100) from a specific group (default)
uv run python main.py fetch --group-url "https://www.facebook.com/groups/<group-id>"

# Fetch the 50 most recent badminton posts with no time constraint
uv run python main.py fetch --group-url "https://www.facebook.com/groups/<group-id>" --latest 50

# Fetch only the last 6 hours, limit to 50 raw posts
uv run python main.py fetch --group-url "https://www.facebook.com/groups/<group-id>" --hours 6 --max-posts 50

# Fetch all saved groups at once (no --group-url needed after first add)
uv run python main.py fetch

# Run browser in headless mode (only if your session is still valid)
uv run python main.py fetch --group-url "https://www.facebook.com/groups/<group-id>" --headless
```

| Flag | Default | Description |
|---|---|---|
| `--group-url` / `-g` | — | Facebook group URL. Omit to fetch all saved groups. |
| `--latest` / `-l` | — | Fetch the N most recent badminton posts; overrides `--hours` and `--max-posts`. |
| `--hours` / `-H` | `12` | How many hours back to look for posts. |
| `--max-posts` / `-n` | `100` | Maximum raw posts to fetch per group. |
| `--headed` / `--headless` | `--headed` | Show or hide the browser window. |

Output example:
```
Group: 123456789
  latest=30 badminton posts  already_seen=47
  Saved 12 new post(s) to cache
  ✓ Saved 12 posts (9 badminton-related).

Done. 12 new post(s) fetched and cached in data/cache.db. (took 45s)
```

---

### `posts` — View cached posts

Browse posts that have already been fetched and stored locally.

```bash
# Show the 50 most recent badminton posts (default)
uv run python main.py posts

# Show up to 100 posts
uv run python main.py posts --limit 100

# Filter by group ID
uv run python main.py posts --group 906525958436246

# Include non-badminton posts
uv run python main.py posts --all
```

| Flag | Default | Description |
|---|---|---|
| `--limit` / `-l` | `50` | Maximum number of posts to display. |
| `--group` / `-g` | — | Filter by group ID or slug. |
| `--all` | `false` | Include non-badminton posts (by default only badminton posts are shown). |

---

### `seen` — Manage the scrape cache

The scrape cache tracks which post IDs have already been processed so they are not re-fetched. Use this command to inspect or reset it.

```bash
# List the 100 most recently seen post IDs
uv run python main.py seen

# List seen posts for a specific group
uv run python main.py seen --group 906525958436246

# Remove a single post from the scrape cache (it will be fetched again on the next run)
uv run python main.py seen --remove <post-id>

# Clear the entire scrape cache (all posts will be re-fetched on the next run)
uv run python main.py seen --clear
```

| Flag | Default | Description |
|---|---|---|
| `--limit` / `-l` | `100` | Maximum number of entries to display. |
| `--group` / `-g` | — | Filter by group ID or slug. |
| `--remove` / `-r` | — | Remove a single post ID from the scrape cache. |
| `--clear` | `false` | Clear **all** entries from the scrape cache. |

---

### `query` — Natural language search

```bash
uv run python main.py query "trình độ TBY lúc 7 giờ tối"
uv run python main.py query "quận 1 cầu mới sáng mai"
uv run python main.py query "cần 2 người TB+"
uv run python main.py query "trình độ TB 19h" --limit 10
```

| Flag | Default | Description |
|---|---|---|
| `--limit` / `-l` | `20` | Maximum number of results to display. |

Results are displayed as a formatted table:

```
                 Results for: "trình độ TBY lúc 7 giờ tối"
┌───┬──────────────────┬────────┬──────────────────┬──────┬────────────┬────────┐
│ # │ Play Time        │ Level  │ Location         │ Need │ Fetched    │ Link   │
├───┼──────────────────┼────────┼──────────────────┼──────┼────────────┼────────┤
│ 1 │ tối nay 7h       │ TBY    │ Sân Phú Nhuận    │ 2    │ 2026-05-27 │ ↗ open │
│ 2 │ Thứ 4 19:00      │ TBY    │ Quận 3           │ 1    │ 2026-05-27 │ ↗ open │
└───┴──────────────────┴────────┴──────────────────┴──────┴────────────┴────────┘
2 result(s).
```

---

### `clear-posts` — Delete all cached posts

Wipes all posts from the local database. The scrape cache (seen IDs) is **not** affected — use `seen --clear` if you also want to re-scrape everything.

```bash
# With confirmation prompt
uv run python main.py clear-posts

# Skip confirmation
uv run python main.py clear-posts --yes
```

| Flag | Default | Description |
|---|---|---|
| `--yes` / `-y` | `false` | Skip the confirmation prompt. |

---

### `groups` — Manage monitored groups

```bash
# Save a group so it's fetched automatically with `fetch` (no --group-url needed)
uv run python main.py groups add "https://www.facebook.com/groups/<group-id>" --name "Cầu lông HN"

# List saved groups
uv run python main.py groups list
```

---

### `keywords` — Keyword exclusion filters

Posts whose text contains any excluded keyword are silently skipped — they are **not** sent to the LLM for parsing, **not** stored in the database, and any existing posts matching the keyword are purged immediately on `keywords add`.

```bash
# Add keywords to exclude (e.g. ads, recruitment, off-topic posts)
uv run python main.py keywords add "tuyển dụng"
uv run python main.py keywords add "quảng cáo"
uv run python main.py keywords add "bán vợt"

# List active filters
uv run python main.py keywords list

# Remove a filter
uv run python main.py keywords remove "bán vợt"
```

Matching is **case-insensitive substring** search, so `"tuyển dụng"` will match any post containing that phrase regardless of capitalisation. Filters are stored in `data/cache.db` and applied automatically on every `fetch`.

## Project Structure

```
facebook-post-aggregation-agent/
├── main.py                  # CLI entry point (Typer)
├── config.py                # Pydantic settings — reads from .env
├── pyproject.toml           # Project metadata and dependencies (uv)
├── scraper/
│   ├── auth.py              # Playwright login + persistent browser profile
│   └── facebook.py          # Feed scrolling and post extraction
├── filters/
│   └── keywords.py          # Keyword exclusion filter
├── llm/
│   └── provider.py          # LLM abstraction (OpenAI / Anthropic / Gemini)
├── parser/
│   └── post_parser.py       # Vietnamese post → structured fields via LLM
├── storage/
│   └── database.py          # SQLite schema, upsert, and query helpers
├── query/
│   └── query_engine.py      # NL query → SQL filter → results
└── data/                    # Created at runtime, gitignored
    ├── browser_profiles/    # Persistent Playwright session (one per account)
    └── cache.db             # SQLite post cache (includes keyword filters)
```

## Configuration Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `FB_EMAIL` | Yes | — | Facebook account email |
| `FB_PASSWORD` | Yes | — | Facebook account password |
| `OPENAI_API_KEY` | Yes* | — | OpenAI API key (*if `LLM_PROVIDER=openai`) |
| `MODEL_NAME` | No | `gpt-4o-mini` | LLM model name |
| `LLM_PROVIDER` | No | `openai` | `openai` \| `anthropic` \| `gemini` |
| `ANTHROPIC_API_KEY` | Yes* | — | Required if `LLM_PROVIDER=anthropic` |
| `GEMINI_API_KEY` | Yes* | — | Required if `LLM_PROVIDER=gemini` |
| `COOKIE_PATH` | No | `data/browser_profiles` | Browser profile storage directory |
| `DB_PATH` | No | `data/cache.db` | SQLite database path |

### Switching LLM providers

Edit `.env`:

```env
# Use Anthropic Claude
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
MODEL_NAME=claude-3-haiku-20240307

# Use Google Gemini
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
MODEL_NAME=gemini-1.5-flash
```

No code changes required.

## Post Fields Extracted

Each post is parsed by the LLM into the following structured fields stored in SQLite:

| Field | Description | Example values |
|---|---|---|
| `is_badminton_post` | Whether this is a player-search post | `true` / `false` |
| `players_needed` | Number of additional players being recruited | `1`, `2`, `3` |
| `players_gender` | Gender breakdown of players needed (as written) | `"1 nữ"`, `"2 nam"`, `"1 nam 1 nữ"` |
| `play_datetime_raw` | Play time exactly as written in the post | `"tối nay 7h"`, `"CN 19:00"` |
| `play_datetime_iso` | ISO 8601 datetime if a single specific date/time can be determined | `"2026-05-21T19:00"` |
| `location` | Full venue name and address | `"Sân Phú Nhuận"`, `"Quận 1"` |
| `level` | Skill level required | `"Y"`, `"TBY"`, `"TB"`, `"TB+"`, `"K"` |
| `notes` | Any other relevant info from the post | — |

## Caveats

- **Facebook ToS**: Scraping Facebook with automated tools may violate their Terms of Service. This tool is intended for personal, non-commercial use. Use responsibly and at your own risk.
- **Selector fragility**: Facebook's frontend changes frequently. If scraping stops working, the CSS selectors in `scraper/facebook.py` may need updating.
- **Session expiry**: If your Facebook session expires, re-run with `--headed` to log in again. The new session is saved automatically.
- **Vietnamese date parsing**: Relative dates like “tối nay”, “ngày mai”, “CN tuần này” are resolved to an absolute ISO date by the LLM using the post’s own creation timestamp as a reference point. If the post time cannot be determined, `play_datetime_iso` falls back to `null` and `play_datetime_raw` retains the original text.
