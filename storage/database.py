import sqlite3
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Post:
    id: str
    group_id: str
    post_url: str
    raw_text: str
    fetched_at: str
    post_time: Optional[str] = None
    is_badminton_post: Optional[int] = None
    players_needed: Optional[int] = None
    play_datetime_raw: Optional[str] = None
    play_datetime_iso: Optional[str] = None
    location: Optional[str] = None
    level: Optional[str] = None
    shuttlecock: Optional[str] = None
    notes: Optional[str] = None
    is_full: Optional[int] = None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id                 TEXT PRIMARY KEY,
    group_id           TEXT NOT NULL,
    post_url           TEXT NOT NULL,
    raw_text           TEXT NOT NULL,
    fetched_at         TEXT NOT NULL,
    post_time          TEXT,
    is_badminton_post  INTEGER,
    players_needed     INTEGER,
    play_datetime_raw  TEXT,
    play_datetime_iso  TEXT,
    location           TEXT,
    level              TEXT,
    shuttlecock        TEXT,
    notes              TEXT,
    is_full            INTEGER
);

CREATE TABLE IF NOT EXISTS groups (
    id        TEXT PRIMARY KEY,
    url       TEXT NOT NULL,
    name      TEXT,
    added_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keywords (
    keyword   TEXT PRIMARY KEY,
    added_at  TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        # Migrate existing databases that predate the is_full column
        try:
            conn.execute("ALTER TABLE posts ADD COLUMN is_full INTEGER")
        except sqlite3.OperationalError:
            pass  # Column already exists


def cleanup_old_posts(db_path: str) -> int:
    """Delete posts that are no longer relevant:
    - play_datetime_iso is in the past (match time has already passed), or
    - fetched more than 2 days ago.
    Returns the number of deleted rows.
    """
    now = datetime.now().isoformat()
    two_days_ago = (datetime.now() - timedelta(days=2)).isoformat()
    with _connect(db_path) as conn:
        result = conn.execute(
            """
            DELETE FROM posts
            WHERE (play_datetime_iso IS NOT NULL AND play_datetime_iso < ?)
               OR fetched_at < ?
            """,
            (now, two_days_ago),
        )
    return result.rowcount


def upsert_post(db_path: str, post: Post) -> None:
    data = asdict(post)
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    updates = ", ".join(
        f"{k}=excluded.{k}" for k in data.keys() if k != "id"
    )
    sql = (
        f"INSERT INTO posts ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    with _connect(db_path) as conn:
        conn.execute(sql, list(data.values()))


def get_cached_ids(db_path: str, group_id: str) -> set:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM posts WHERE group_id = ?", (group_id,)
        ).fetchall()
    return {row["id"] for row in rows}


def query_posts(
    db_path: str,
    where_clause: str = "1=1",
    params: Optional[list] = None,
    limit: int = 50,
) -> list:
    sql = (
        "SELECT * FROM posts "
        "WHERE is_badminton_post = 1 AND ({clause}) "
        "ORDER BY play_datetime_iso DESC, post_time DESC "
        "LIMIT ?"
    ).format(clause=where_clause)
    all_params = (params or []) + [limit]
    with _connect(db_path) as conn:
        rows = conn.execute(sql, all_params).fetchall()
    return [Post(**dict(row)) for row in rows]


def upsert_group(
    db_path: str, group_id: str, url: str, name: str = ""
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO groups (id, url, name, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET url=excluded.url, name=excluded.name",
            (group_id, url, name, datetime.now().isoformat()),
        )


def list_groups(db_path: str) -> list:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM groups").fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

def add_keyword(db_path: str, keyword: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO keywords (keyword, added_at) VALUES (?, ?)",
            (keyword.strip(), datetime.now().isoformat()),
        )


def remove_keyword(db_path: str, keyword: str) -> bool:
    with _connect(db_path) as conn:
        result = conn.execute(
            "DELETE FROM keywords WHERE keyword = ?", (keyword.strip(),)
        )
    return result.rowcount > 0


def list_keywords(db_path: str) -> list:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT keyword, added_at FROM keywords ORDER BY keyword"
        ).fetchall()
    return [dict(row) for row in rows]
