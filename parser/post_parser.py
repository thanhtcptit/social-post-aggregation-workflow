import json
import re
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an assistant that extracts structured data from Vietnamese Facebook posts in badminton groups.
Today is {today}.
{post_time_context}
Extract the following fields and return valid JSON:
- is_badminton_post  (boolean) — true if this post is looking for badminton players
- players_needed     (integer or null) — number of additional players being recruited
  (e.g. "Tuyển 2 bạn", "cần 1 nam"); NOT court capacity or max players per session
  (e.g. "max 8", "tối đa 8 người", "đủ 10 người")
- players_gender     (string or null) — gender breakdown of players needed as written
  (e.g. "1 nữ", "2 nam", "1 nam 1 nữ"); null if no gender is specified
- play_datetime_raw  (string or null) — play time exactly as written in the post
- play_datetime_iso  (string or null) — ISO 8601 datetime if a single specific date/time can be
  determined (e.g. "2026-05-25T19:00"); null if ambiguous or a recurring weekly schedule
- play_time_only     (string or null) — time in HH:MM 24-hour format when a time is mentioned
  but no specific date can be determined (e.g. "19:00"); null otherwise
- location           (string or null) — full venue name and address; strip leading prepositions
  like "Tại"/"tại" but keep venue-type words like "Sân"/"sân"; never truncate, capture the
  complete string including street name and district
- level              (string or null) — skill level; common values: "Newbie", "Y", "TBY", "TB",
  "TB+", "TBK", "K", "Khá"

Rules:
- If not a badminton player-search post, set is_badminton_post=false and all other fields to null.
- If multiple skill levels are mentioned, combine them (e.g. "TB-TB+").
- For play_datetime_iso: use the post timestamp as a reference to resolve relative expressions
  such as "tối nay" (tonight), "ngày mai" (tomorrow), "thứ 4" (Wednesday), "CN tuần này"
  (this Sunday), etc.
- If no explicit date is mentioned and the post was made today, assume the play date is today.
- If a recurring weekly schedule is mentioned (e.g. "thứ 2.4.6", "every Mon/Wed/Fri"),
  set play_datetime_iso=null and keep the raw expression in play_datetime_raw.
- All times use 24-hour format unless explicitly qualified: "5h" = 05:00, "17h" = 17:00,
  "8h tối" = 20:00, "8h sáng" = 08:00, "12h trưa" = 12:00.
- Return only the JSON object, no extra text.
"""

_BATCH_SYSTEM_PROMPT = """\
You are an assistant that extracts structured data from Vietnamese Facebook posts in badminton groups.
Today is {today}.

Analyze the list of posts below and return a JSON array with exactly {n} elements in the same order.
Each post may include its post timestamp — use it as a reference to resolve relative date expressions
such as "tối nay" (tonight), "ngày mai" (tomorrow), "thứ 4" (Wednesday), "CN tuần này" (this Sunday).

Each element has these fields:
- is_badminton_post  (boolean)
- players_needed     (integer or null) — number of additional players being recruited
  (e.g. "Tuyển 2 bạn", "cần 1 nam"); NOT court capacity or max players per session
  (e.g. "max 8", "tối đa 8 người", "đủ 10 người")
- players_gender     (string or null) — gender breakdown as written (e.g. "1 nữ", "2 nam",
  "1 nam 1 nữ"); null if no gender specified
- play_datetime_raw  (string or null)
- play_datetime_iso  (string ISO 8601 or null) — null if ambiguous or a recurring weekly schedule
- play_time_only     (string or null) — HH:MM 24-hour time when a time is mentioned but no
  specific date can be determined; null otherwise
- location           (string or null) — full venue name and address; strip leading prepositions
  like "Tại"/"tại" but keep venue-type words like "Sân"/"sân"; never truncate, capture the
  complete string including street name and district
- level              (string or null) — "Newbie", "Y", "TBY", "TB", "TB+", "TBK", "K", …

Rules:
- If not a badminton player-search post, set is_badminton_post=false and all other fields to null.
- If multiple skill levels are mentioned, combine them (e.g. "TB-TB+").
- If no explicit date is mentioned and the post was made today, assume the play date is today.
- If a recurring weekly schedule is mentioned (e.g. "thứ 2.4.6", "every Mon/Wed/Fri"),
  set play_datetime_iso=null and keep the raw expression in play_datetime_raw.
- All times use 24-hour format unless explicitly qualified: "5h" = 05:00, "17h" = 17:00,
  "8h tối" = 20:00, "8h sáng" = 08:00, "12h trưa" = 12:00.
- Return only the JSON array, no extra text.
"""

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ParsedPost:
    is_badminton_post: bool
    players_needed: Optional[int]
    players_gender: Optional[str]
    play_datetime_raw: Optional[str]
    play_datetime_iso: Optional[str]
    location: Optional[str]
    level: Optional[str]


_EMPTY = ParsedPost(
    is_badminton_post=False,
    players_needed=None,
    players_gender=None,
    play_datetime_raw=None,
    play_datetime_iso=None,
    location=None,
    level=None,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_post(raw_text: str, llm, today: str, post_time: Optional[str] = None) -> ParsedPost:
    """Parse a single post. Falls back to _EMPTY on any error."""
    post_time_context = (
        f"Bài đăng này được tạo lúc: {post_time}.\n"
        if post_time
        else ""
    )
    system = _SYSTEM_PROMPT.format(today=today, post_time_context=post_time_context)
    try:
        response = llm.complete(
            system_prompt=system, user_message=raw_text, json_mode=True
        )
        post_date = _extract_date(post_time, today)
        return _parse_item(json.loads(response), post_date)
    except Exception:
        return _EMPTY


def parse_posts_batch(
    posts: list,
    llm,
    today: str,
    batch_size: int = 10,
) -> list:
    """
    Parse a list of {"text": str, "post_time": str | None} dicts.
    Sends them to the LLM in chunks of *batch_size* to avoid context overflow.
    Returns a list of ParsedPost in the same order.
    """
    results: list = []
    for chunk_start in range(0, len(posts), batch_size):
        chunk = posts[chunk_start : chunk_start + batch_size]
        results.extend(_parse_chunk(chunk, llm, today))
    return results


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _parse_chunk(posts: list, llm, today: str) -> list:
    if not posts:
        return []
    if len(posts) == 1:
        return [parse_post(posts[0]["text"], llm, today, posts[0].get("post_time"))]

    system = _BATCH_SYSTEM_PROMPT.format(today=today, n=len(posts))
    user_msg = "\n\n".join(
        "--- Bài {n}{ts} ---\n{text}".format(
            n=i + 1,
            ts=f" (đăng lúc: {p['post_time']})" if p.get("post_time") else "",
            text=p["text"],
        )
        for i, p in enumerate(posts)
    )

    try:
        response = llm.complete(
            system_prompt=system, user_message=user_msg, json_mode=True
        )
        data = json.loads(response)

        # Normalise: LLM might return {"posts": [...]} instead of bare array
        if isinstance(data, dict):
            for key in ("posts", "results", "items"):
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break

        if not isinstance(data, list):
            raise ValueError("Expected JSON array")

        parsed = [
            _parse_item(item, _extract_date(posts[i].get("post_time"), today))
            for i, item in enumerate(data[: len(posts)])
        ]
        # Pad if LLM returned fewer items than expected
        while len(parsed) < len(posts):
            parsed.append(parse_post(posts[len(parsed)]["text"], llm, today))
        return parsed

    except Exception:
        # Fall back to individual parsing for this chunk
        return [parse_post(p["text"], llm, today, p.get("post_time")) for p in posts]


def _extract_date(post_time: Optional[str], today: str) -> str:
    """Return the YYYY-MM-DD portion of *post_time*, falling back to *today*."""
    if post_time and len(post_time) >= 10:
        return post_time[:10]
    return today


def _parse_item(data: dict, post_date: Optional[str] = None) -> ParsedPost:
    play_datetime_iso = _to_str(data.get("play_datetime_iso"))
    if play_datetime_iso is None and post_date:
        time_only = _to_str(data.get("play_time_only"))
        if time_only and re.match(r"^\d{1,2}:\d{2}$", time_only):
            play_datetime_iso = f"{post_date}T{time_only}"
    return ParsedPost(
        is_badminton_post=bool(data.get("is_badminton_post", False)),
        players_needed=_to_int(data.get("players_needed")),
        players_gender=_to_str(data.get("players_gender")),
        play_datetime_raw=_to_str(data.get("play_datetime_raw")),
        play_datetime_iso=play_datetime_iso,
        location=_to_str(data.get("location")),
        level=_to_str(data.get("level")),
    )


def _to_int(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _to_str(val) -> Optional[str]:
    if val is None or val == "":
        return None
    return str(val).strip() or None
