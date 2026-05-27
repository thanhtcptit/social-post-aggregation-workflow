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
    players_gender: Optional[str] = None
    play_datetime_raw: Optional[str] = None
    play_datetime_iso: Optional[str] = None
    location: Optional[str] = None
    level: Optional[str] = None
    notes: Optional[str] = None


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
    players_gender     TEXT,
    play_datetime_raw  TEXT,
    play_datetime_iso  TEXT,
    location           TEXT,
    level              TEXT,
    notes              TEXT
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

CREATE TABLE IF NOT EXISTS scrape_cache (
    post_id    TEXT NOT NULL,
    group_id   TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    PRIMARY KEY (post_id, group_id)
);

CREATE TABLE IF NOT EXISTS content_hash_cache (
    post_id      TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    cached_at    TEXT NOT NULL
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
        # Migrate existing databases that predate the scrape_cache table
        try:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS scrape_cache ("
                "post_id TEXT NOT NULL, group_id TEXT NOT NULL, "
                "scraped_at TEXT NOT NULL, PRIMARY KEY (post_id, group_id));"
            )
        except sqlite3.OperationalError:
            pass
        # Migrate existing databases that predate the content_hash_cache table
        try:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS content_hash_cache ("
                "post_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, "
                "cached_at TEXT NOT NULL);"
            )
        except sqlite3.OperationalError:
            pass
        # Index for fast cross-ID duplicate detection
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chc_hash "
                "ON content_hash_cache(content_hash)"
            )
        except sqlite3.OperationalError:
            pass
        # Migrate: add players_gender column if absent
        try:
            conn.execute("ALTER TABLE posts ADD COLUMN players_gender TEXT")
        except sqlite3.OperationalError:
            pass


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


def clear_posts(db_path: str) -> int:
    """Delete all posts from the database. Returns the number of deleted rows."""
    with _connect(db_path) as conn:
        result = conn.execute("DELETE FROM posts")
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


def get_seen_ids(db_path: str, group_id: str) -> set:
    """Return all post IDs ever scraped for this group (from scrape_cache)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT post_id FROM scrape_cache WHERE group_id = ?", (group_id,)
        ).fetchall()
    return {row["post_id"] for row in rows}


def mark_seen(db_path: str, post_ids: list, group_id: str) -> None:
    """Record that these post IDs have been scraped so they are never re-processed."""
    now = datetime.now().isoformat()
    with _connect(db_path) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO scrape_cache (post_id, group_id, scraped_at) VALUES (?, ?, ?)",
            [(pid, group_id, now) for pid in post_ids],
        )


def get_cached_content_hash(db_path: str, post_id: str) -> Optional[str]:
    """Return the cached content hash for *post_id*, or None if not yet stored."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_hash FROM content_hash_cache WHERE post_id = ?",
            (post_id,),
        ).fetchone()
    return row["content_hash"] if row else None


def is_content_hash_seen(db_path: str, content_hash: str) -> bool:
    """Return True if *content_hash* is already stored for ANY post (cross-ID dedup)."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM content_hash_cache WHERE content_hash = ? LIMIT 1",
            (content_hash,),
        ).fetchone()
    return row is not None


def upsert_content_hash(db_path: str, post_id: str, content_hash: str) -> None:
    """Store or update the content hash for *post_id*."""
    now = datetime.now().isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO content_hash_cache (post_id, content_hash, cached_at) VALUES (?, ?, ?)"
            " ON CONFLICT(post_id) DO UPDATE SET content_hash=excluded.content_hash, cached_at=excluded.cached_at",
            (post_id, content_hash, now),
        )


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
    return [Post(**{k: v for k, v in dict(row).items() if k in Post.__dataclass_fields__}) for row in rows]


def list_all_posts(
    db_path: str,
    group_id: Optional[str] = None,
    badminton_only: bool = True,
    limit: int = 50,
) -> list:
    """Return posts ordered by play_datetime_iso then post_time, newest first."""
    conditions: list = []
    params: list = []
    if badminton_only:
        conditions.append("is_badminton_post = 1")
    if group_id:
        conditions.append("group_id = ?")
        params.append(group_id)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = (
        f"SELECT * FROM posts {where} "
        f"ORDER BY play_datetime_iso DESC, post_time DESC LIMIT ?"
    )
    params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [Post(**{k: v for k, v in dict(row).items() if k in Post.__dataclass_fields__}) for row in rows]


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


def list_seen(
    db_path: str,
    group_id: Optional[str] = None,
    limit: int = 100,
) -> list:
    """Return rows from scrape_cache, newest first."""
    if group_id:
        sql = (
            "SELECT post_id, group_id, scraped_at FROM scrape_cache "
            "WHERE group_id = ? ORDER BY scraped_at DESC LIMIT ?"
        )
        params = [group_id, limit]
    else:
        sql = (
            "SELECT post_id, group_id, scraped_at FROM scrape_cache "
            "ORDER BY scraped_at DESC LIMIT ?"
        )
        params = [limit]
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def remove_seen(db_path: str, post_id: str) -> int:
    """Remove a post ID from scrape_cache. Returns number of rows deleted."""
    with _connect(db_path) as conn:
        result = conn.execute(
            "DELETE FROM scrape_cache WHERE post_id = ?", (post_id,)
        )
    return result.rowcount


def clear_seen(db_path: str) -> int:
    """Remove all entries from scrape_cache. Returns number of rows deleted."""
    with _connect(db_path) as conn:
        result = conn.execute("DELETE FROM scrape_cache")
    return result.rowcount
