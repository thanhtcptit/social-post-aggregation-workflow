# Facebook Badminton Post Aggregation Agent

An agentic CLI tool that scrapes Vietnamese Facebook badminton groups, uses an LLM to extract structured data from each post, caches everything locally in SQLite, and lets you search with natural language queries.

## Features

- **Scrapes public Facebook groups** using Playwright (Chromium) with a persistent login session
- **Parses Vietnamese posts** with GPT-4o-mini to extract: play time, location, level, player count, shuttlecock type, and more
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

On the first run the browser profile is empty, so launch with `--headed` to log in manually (handles 2FA, CAPTCHA, etc.). The session is saved to `data/browser_profiles/` and reused automatically on all future runs.

```bash
uv run python main.py fetch --group-url "https://www.facebook.com/groups/<group-id>" --headed
```

### Fetch posts

```bash
# Fetch the last 12 hours of posts (up to 100) from a specific group (default)
uv run python main.py fetch --group-url "https://www.facebook.com/groups/<group-id>"

# Fetch the 50 most recent posts with no time constraint
uv run python main.py fetch --group-url "https://www.facebook.com/groups/<group-id>" --latest 50

# Fetch only the last 6 hours, limit to 50 posts
uv run python main.py fetch --group-url "https://www.facebook.com/groups/<group-id>" --hours 6 --max-posts 50

# Fetch all saved groups at once (no --group-url needed after first add)
uv run python main.py fetch
```

Output example:
```
Group: 123456789
  latest=30  already_cached=47
  Fetched 30 posts (12 new)
  ✓ Saved 12 posts (9 badminton-related).

Done. 12 new post(s) fetched and cached in data/cache.db.
```

### Query the cache

```bash
uv run python main.py query "trình độ TBY lúc 7 giờ tối"
uv run python main.py query "quận 1 cầu mới sáng mai"
uv run python main.py query "cần 2 người TB+"
uv run python main.py query "trình độ TB 19h" --limit 10
```

Results are displayed as a formatted table:

```
                 Results for: "trình độ TBY lúc 7 giờ tối"
┌──────────────────┬────────┬──────────────────┬──────┬─────────┬───────────────────────────────────────┐
│ Time             │ Level  │ Location         │ Need │ Shuttle │ URL                                   │
├──────────────────┼────────┼──────────────────┼──────┼─────────┼───────────────────────────────────────┤
│ tối nay 7h       │ TBY    │ Sân Phú Nhuận    │ 2    │ 95%     │ https://facebook.com/groups/.../posts/…│
│ Thứ 4 19:00      │ TBY    │ Quận 3           │ 1    │ new     │ https://facebook.com/groups/.../posts/…│
└──────────────────┴────────┴──────────────────┴──────┴─────────┴───────────────────────────────────────┘
2 result(s).
```

### Manage groups

```bash
# Save a group so it's fetched automatically
uv run python main.py groups add "https://www.facebook.com/groups/<group-id>" --name "Cầu lông HN"

# List saved groups
uv run python main.py groups list
```

### Keyword filters

Posts whose text contains any excluded keyword are silently skipped — they are **not** sent to the LLM for parsing and **not** stored in the database.

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
| `players_needed` | Number of players being sought | `1`, `2`, `3` |
| `play_datetime_raw` | Play time as written in the post | `"tối nay 7h"`, `"CN 19:00"` |
| `play_datetime_iso` | Normalized ISO 8601 (if determinable) | `"2026-05-21T19:00"` |
| `location` | Court / district / address | `"Sân Phú Nhuận"`, `"Quận 1"` |
| `level` | Skill level required | `"Y"`, `"TBY"`, `"TB"`, `"TB+"`, `"K"` |
| `shuttlecock` | Shuttlecock type | `"95%"` (used), `"new"` (brand new) |
| `notes` | Any other relevant info from the post | — |

## Caveats

- **Facebook ToS**: Scraping Facebook with automated tools may violate their Terms of Service. This tool is intended for personal, non-commercial use. Use responsibly and at your own risk.
- **Selector fragility**: Facebook's frontend changes frequently. If scraping stops working, the CSS selectors in `scraper/facebook.py` may need updating.
- **Session expiry**: If your Facebook session expires, re-run with `--headed` to log in again. The new session is saved automatically.
- **Vietnamese date parsing**: Relative dates like “tối nay”, “ngày mai”, “CN tuần này” are resolved to an absolute ISO date by the LLM using the post’s own creation timestamp as a reference point. If the post time cannot be determined, `play_datetime_iso` falls back to `null` and `play_datetime_raw` retains the original text.
