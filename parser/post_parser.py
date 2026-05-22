import json
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Bạn là trợ lý phân tích bài đăng trong nhóm cầu lông Việt Nam trên Facebook.
Ngày hôm nay là {today}.
{post_time_context}
Trích xuất thông tin có cấu trúc từ bài đăng và trả về JSON với các trường sau:
- is_badminton_post  (boolean) — true nếu đây là bài tìm người đánh cầu lông
- players_needed     (integer hoặc null) — số người cần tìm
- play_datetime_raw  (string hoặc null) — thời gian chơi nguyên văn trong bài
- play_datetime_iso  (string hoặc null) — ISO 8601 nếu xác định được (ví dụ: "2026-05-21T19:00"), \
null nếu mơ hồ
- location           (string hoặc null) — địa điểm / sân
- level              (string hoặc null) — trình độ yêu cầu; các giá trị phổ biến: \
"Newbie", "Y", "TBY", "TB", "TB+", "TBK", "K", "Khá"
- shuttlecock        (string hoặc null) — loại cầu: "95%" cho cầu qua sử dụng, \
"new" cho cầu mới; null nếu không đề cập
- notes              (string hoặc null) — thông tin bổ sung quan trọng khác

Quy tắc:
- Nếu không phải bài tìm người đánh cầu, đặt is_badminton_post=false và các trường còn lại là null.
- Nếu có nhiều trình độ được đề cập, ghi tất cả vào trường `level` (ví dụ: "TB-TB+").
- Để xác định play_datetime_iso: dùng thời gian đăng bài làm mốc để suy ra ngày tuyệt đối \
cho các cụm từ tương đối như "tối nay", "ngày mai", "thứ 4", "CN tuần này", v.v.
- Chỉ trả về JSON, không có văn bản thêm.
"""

_BATCH_SYSTEM_PROMPT = """\
Bạn là trợ lý phân tích bài đăng trong nhóm cầu lông Việt Nam trên Facebook.
Ngày hôm nay là {today}.

Phân tích danh sách bài đăng bên dưới và trả về JSON array với đúng {n} phần tử, \
theo đúng thứ tự bài đăng.
Mỗi bài có ghi thời điểm đăng (nếu có) — dùng đó làm mốc để suy ra ngày tuyệt đối \
cho các cụm từ tương đối như "tối nay", "ngày mai", "thứ 4", "CN tuần này", v.v.

Mỗi phần tử có các trường:
- is_badminton_post  (boolean)
- players_needed     (integer hoặc null)
- play_datetime_raw  (string hoặc null)
- play_datetime_iso  (string ISO 8601 hoặc null)
- location           (string hoặc null)
- level              (string hoặc null) — "Newbie", "Y", "TBY", "TB", "TB+", "TBK", "K", …
- shuttlecock        (string hoặc null) — "95%" hoặc "new" hoặc null
- notes              (string hoặc null)

Chỉ trả về JSON array, không có văn bản thêm.
"""

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ParsedPost:
    is_badminton_post: bool
    players_needed: Optional[int]
    play_datetime_raw: Optional[str]
    play_datetime_iso: Optional[str]
    location: Optional[str]
    level: Optional[str]
    shuttlecock: Optional[str]
    notes: Optional[str]


_EMPTY = ParsedPost(
    is_badminton_post=False,
    players_needed=None,
    play_datetime_raw=None,
    play_datetime_iso=None,
    location=None,
    level=None,
    shuttlecock=None,
    notes=None,
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
        return _parse_item(json.loads(response))
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

        parsed = [_parse_item(item) for item in data[: len(posts)]]
        # Pad if LLM returned fewer items than expected
        while len(parsed) < len(posts):
            parsed.append(parse_post(posts[len(parsed)]["text"], llm, today))
        return parsed

    except Exception:
        # Fall back to individual parsing for this chunk
        return [parse_post(p["text"], llm, today, p.get("post_time")) for p in posts]


def _parse_item(data: dict) -> ParsedPost:
    return ParsedPost(
        is_badminton_post=bool(data.get("is_badminton_post", False)),
        players_needed=_to_int(data.get("players_needed")),
        play_datetime_raw=_to_str(data.get("play_datetime_raw")),
        play_datetime_iso=_to_str(data.get("play_datetime_iso")),
        location=_to_str(data.get("location")),
        level=_to_str(data.get("level")),
        shuttlecock=_to_str(data.get("shuttlecock")),
        notes=_to_str(data.get("notes")),
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
