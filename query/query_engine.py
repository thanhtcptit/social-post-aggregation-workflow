import json
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Filter extraction prompt
# ---------------------------------------------------------------------------

_FILTER_PROMPT = """\
Bạn là trợ lý phân tích câu truy vấn tìm bài đăng cầu lông.

Phân tích câu truy vấn và trả về JSON với các trường sau:
- level            (string hoặc null) — trình độ ("Y", "TBY", "TB", "TB+", "TBK", "K", "Newbie", …)
- location_contains (string hoặc null) — từ khoá địa điểm
- hour             (integer 0-23 hoặc null) — giờ chơi theo định dạng 24h (ví dụ: "7 giờ tối" → 19, "19h" → 19, "7h sáng" → 7)
- date_iso         (string "YYYY-MM-DD" hoặc null) — ngày chơi nếu được nhắc đến (ví dụ: "ngày 26/5" → "2026-05-26")
- time_contains    (string hoặc null) — chuỗi thời gian để tìm kiếm LIKE trong play_datetime_raw (chỉ dùng khi không xác định được giờ cụ thể)
- players_needed   (integer hoặc null) — số người cần tìm
- text_contains    (string hoặc null) — từ khoá bất kỳ khác cần tìm trong nội dung bài

Năm hiện tại là 2026.

Ví dụ:
- "trình độ TBY lúc 7 giờ tối" →
  {"level":"TBY","hour":19,"date_iso":null,"time_contains":null,"location_contains":null,"players_needed":null,"text_contains":null}
- "cầu lông quận 1 sáng mai" →
  {"level":null,"hour":null,"date_iso":null,"time_contains":"sáng","location_contains":"quận 1","players_needed":null,"text_contains":null}
- "sân lúc 19h ngày 26/5" →
  {"level":null,"hour":19,"date_iso":"2026-05-26","time_contains":null,"location_contains":"sân","players_needed":null,"text_contains":null}

Chỉ trả về JSON, không có văn bản thêm.
"""

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FilterCriteria:
    level: Optional[str] = None
    location_contains: Optional[str] = None
    hour: Optional[int] = None
    date_iso: Optional[str] = None
    time_contains: Optional[str] = None
    players_needed: Optional[int] = None
    text_contains: Optional[str] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search(
    nl_query: str,
    db_path: str,
    llm,
    limit: int = 20,
) -> list:
    """
    Translate *nl_query* into SQL filters via LLM, query SQLite, and return
    matching Post objects.  Falls back to a raw-text LIKE search if the
    structured filter returns zero results.
    """
    from storage.database import query_posts

    filters = _extract_filters(nl_query, llm)
    where, params = _build_sql(filters)
    results = query_posts(db_path, where, params, limit)

    # Fallback: full-text LIKE on raw_text
    if not results and nl_query.strip():
        results = query_posts(
            db_path,
            "raw_text LIKE ?",
            [f"%{nl_query}%"],
            limit,
        )

    return results


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _extract_filters(nl_query: str, llm) -> FilterCriteria:
    try:
        response = llm.complete(
            system_prompt=_FILTER_PROMPT,
            user_message=nl_query,
            json_mode=True,
        )
        data = json.loads(response)
        return FilterCriteria(
            level=data.get("level") or None,
            location_contains=data.get("location_contains") or None,
            hour=_to_int(data.get("hour")),
            date_iso=data.get("date_iso") or None,
            time_contains=data.get("time_contains") or None,
            players_needed=_to_int(data.get("players_needed")),
            text_contains=data.get("text_contains") or None,
        )
    except Exception:
        return FilterCriteria()


def _build_sql(f: FilterCriteria) -> tuple:
    conditions: list = []
    params: list = []

    if f.level:
        # Accept exact match OR level field that contains the string
        conditions.append("(level = ? OR level LIKE ?)")
        params.extend([f.level, f"%{f.level}%"])

    if f.location_contains:
        conditions.append("location LIKE ?")
        params.append(f"%{f.location_contains}%")

    if f.date_iso is not None:
        # Use SQLite date() so both "2026-05-26T…" and "2026-05-26 …" formats work
        conditions.append("date(play_datetime_iso) = ?")
        params.append(f.date_iso)

    if f.hour is not None:
        # When play_datetime_iso is present, trust it exclusively.
        # Only fall back to raw-text matching when the ISO field is NULL,
        # to avoid false positives like "18h-20h 19h30-21h30" matching hour=19.
        hour_24 = f.hour
        conditions.append(
            "("
            "CAST(strftime('%H', play_datetime_iso) AS INTEGER) = ? OR "
            "(play_datetime_iso IS NULL AND ("
            "play_datetime_raw LIKE ? OR "
            "play_datetime_raw LIKE ?"
            "))"
            ")"
        )
        params.extend([
            hour_24,
            f"%{hour_24}h%",
            f"%{hour_24} giờ%",
        ])
    elif f.time_contains:
        conditions.append(
            "(play_datetime_raw LIKE ? OR play_datetime_iso LIKE ?)"
        )
        params.extend([f"%{f.time_contains}%", f"%{f.time_contains}%"])

    if f.players_needed is not None:
        conditions.append("players_needed = ?")
        params.append(f.players_needed)

    if f.text_contains:
        conditions.append("raw_text LIKE ?")
        params.append(f"%{f.text_contains}%")

    where = " AND ".join(conditions) if conditions else "1=1"
    return where, params


def _to_int(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None
