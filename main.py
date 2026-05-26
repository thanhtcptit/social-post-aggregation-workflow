"""
Facebook Badminton Post Aggregation Agent
CLI entry point.

Usage
-----
  python main.py fetch --group-url URL [--latest 50]
  python main.py fetch --group-url URL [--hours 12] [--max-posts 100]
  python main.py posts [--limit 50] [--group GROUP_ID] [--all]
  python main.py query "trình độ TBY lúc 7 giờ tối"
  python main.py groups add URL [--name "Group name"]
  python main.py groups list
"""

from __future__ import annotations

import sys as _sys
import hashlib

# On Windows, piped stdout defaults to cp1252 which can't encode Vietnamese
# characters.  Reconfigure to UTF-8 (with replacement fallback) before any
# Rich / Typer output is produced.
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.text import Text
from rich import print as rprint

app = typer.Typer(
    name="badminton-agent",
    help="Aggregate & query Vietnamese Facebook badminton group posts.",
    no_args_is_help=True,
)
groups_app = typer.Typer(help="Manage monitored Facebook groups.", no_args_is_help=True)
keywords_app = typer.Typer(help="Manage keyword filters (exclude matching posts).", no_args_is_help=True)
app.add_typer(groups_app, name="groups")
app.add_typer(keywords_app, name="keywords")

console = Console()


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

@app.command()
def fetch(
    group_url: Optional[str] = typer.Option(
        None,
        "--group-url",
        "-g",
        help="Facebook group URL. Omit to fetch all saved groups.",
    ),
    latest: Optional[int] = typer.Option(
        None,
        "--latest",
        "-l",
        help="Fetch the N most recent posts with no time constraint. Overrides --hours and --max-posts.",
    ),
    hours: int = typer.Option(
        12, "--hours", "-H", help="How many hours back to look for posts."
    ),
    max_posts: int = typer.Option(
        100, "--max-posts", "-n", help="Maximum posts to fetch per group."
    ),
    headed: bool = typer.Option(
        True,
        "--headed/--headless",
        help="Show browser window. Defaults to headed; use --headless to hide (Facebook detects headless and may return 0 posts).",
    ),
) -> None:
    """Scrape the latest posts from Facebook group(s) and cache them locally.

    Use --latest N to simply grab the N most recent posts regardless of age.
    Use --hours / --max-posts together for time-windowed fetching (default: last 12 h).
    """
    import time as _time
    _fetch_start = _time.monotonic()

    from playwright.sync_api import sync_playwright

    from config import settings
    from filters.keywords import should_exclude
    from llm.provider import get_provider
    from parser.post_parser import parse_post
    from scraper.auth import ensure_logged_in, get_profile_dir
    from scraper.facebook import extract_group_id, fetch_group_posts
    from storage.database import (
        Post,
        cleanup_old_posts,
        get_cached_content_hash,
        get_seen_ids,
        mark_seen,
        init_db,
        list_keywords,
        upsert_content_hash,
        upsert_group,
        upsert_post,
        list_groups,
    )

    _validate_config(settings)
    init_db(settings.db_path)
    deleted = cleanup_old_posts(settings.db_path)
    if deleted:
        console.print(f"[dim]Cleaned up {deleted} stale post(s) from database.[/dim]")

    # --latest N overrides --hours and sets a raw scrape cap; stops once N badminton posts found
    if latest is not None:
        hours = 0        # no time constraint
        max_posts = min(latest * 10, 500)  # raw cap; break early once N badminton posts are saved

    # Resolve which group URLs to process
    if group_url:
        urls = [group_url]
        upsert_group(settings.db_path, extract_group_id(group_url), group_url)
    else:
        saved = list_groups(settings.db_path)
        if not saved:
            rprint(
                "[red]No group URL provided and no groups saved. "
                "Run: python main.py groups add <URL>[/red]"
            )
            raise typer.Exit(1)
        urls = [g["url"] for g in saved]

    profile_dir = get_profile_dir(settings.fb_email, settings.cookie_path)
    llm = get_provider()
    today = datetime.now().strftime("%Y-%m-%d")
    headless = not headed
    total_new = 0
    exclude_keywords = [row["keyword"] for row in list_keywords(settings.db_path)]
    if exclude_keywords:
        console.print(
            f"[dim]Keyword filters active ({len(exclude_keywords)}): "
            + ", ".join(f'"{k}"' for k in exclude_keywords[:5])
            + (" …" if len(exclude_keywords) > 5 else "")
            + "[/dim]"
        )

    with sync_playwright() as pw:
        context, _ = ensure_logged_in(
            pw, profile_dir, settings.fb_email, settings.fb_password,
            headless=headless, chrome_profile=settings.chrome_profile,
            fb_cookies_json=settings.fb_cookies,
        )
        try:
            for url in urls:
                gid = extract_group_id(url)
                # --latest mode: skip ALL previously-seen posts (scrape_cache) so only
                # genuinely new posts are processed. Increased scroll depth handles cases
                # where new posts appear below older active posts (RECENT_ACTIVITY sort).
                # --hours/--max-posts mode: same strategy for efficiency.
                stop_ids = get_seen_ids(settings.db_path, gid)

                console.print(f"\n[cyan]Group:[/cyan] {gid}")
                mode_label = f"latest={latest} badminton posts" if latest is not None else f"hours_back={hours}  max_posts={max_posts}"
                console.print(
                    f"  [dim]{mode_label}  "
                    f"already_seen={len(stop_ids)}[/dim]"
                )

                now_iso = datetime.now().isoformat()
                expired_skipped = 0
                keyword_skipped = 0
                short_skipped = 0
                hash_skipped = 0
                saved_count = 0
                badminton_count = 0
                seen_this_run: list = []

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold green]Fetching & parsing[/bold green] [cyan]{task.description}[/cyan]"),
                    BarColumn(),
                    MofNCompleteColumn(),
                    TaskProgressColumn(),
                    console=console,
                    transient=True,
                ) as progress:
                    task = progress.add_task(gid, total=max_posts)
                    def _on_progress(collected: int, total: int, _task=task, _progress=progress) -> None:
                        _progress.update(_task, completed=collected, total=total)
                    for raw_post in fetch_group_posts(
                        url, hours, max_posts, context, stop_ids, on_progress=_on_progress
                    ):
                        if settings.verbose:
                            console.rule(f"[yellow]Raw post id={raw_post.id}[/yellow]")
                            console.print(raw_post.raw_text)

                        # Skip parsing if content hasn't changed since last scrape
                        content_hash = hashlib.sha256(raw_post.raw_text.encode()).hexdigest()
                        if get_cached_content_hash(settings.db_path, raw_post.id) == content_hash:
                            seen_this_run.append(raw_post.id)
                            hash_skipped += 1
                            continue

                        if len(raw_post.raw_text.split()) < 10:
                            short_skipped += 1
                            continue

                        if should_exclude(raw_post.raw_text, exclude_keywords):
                            keyword_skipped += 1
                            continue

                        p = parse_post(raw_post.raw_text, llm, today, raw_post.post_time)

                        if settings.verbose:
                            console.rule(f"[blue]Parsed post id={raw_post.id}[/blue]")
                            console.print(p)

                        if p.play_datetime_iso and p.play_datetime_iso < now_iso:
                            expired_skipped += 1
                            continue

                        upsert_post(
                            settings.db_path,
                            Post(
                                id=raw_post.id,
                                group_id=raw_post.group_id,
                                post_url=raw_post.post_url,
                                raw_text=raw_post.raw_text,
                                fetched_at=now_iso,
                                post_time=raw_post.post_time,
                                is_badminton_post=1 if p.is_badminton_post else 0,
                                players_needed=p.players_needed,
                                players_gender=p.players_gender,
                                play_datetime_raw=p.play_datetime_raw,
                                play_datetime_iso=p.play_datetime_iso,
                                location=p.location,
                                level=p.level,
                                notes=None,
                            ),
                        )
                        upsert_content_hash(settings.db_path, raw_post.id, content_hash)
                        seen_this_run.append(raw_post.id)
                        saved_count += 1
                        if p.is_badminton_post:
                            badminton_count += 1
                            if latest is not None and badminton_count >= latest:
                                break

                if seen_this_run:
                    mark_seen(settings.db_path, seen_this_run, gid)

                console.print(f"  Saved [bold]{len(seen_this_run)}[/bold] new post(s) to cache")
                if short_skipped:
                    console.print(f"  [dim]Skipped {short_skipped} post(s) with fewer than 10 words.[/dim]")
                if hash_skipped:
                    console.print(f"  [dim]Skipped {hash_skipped} post(s) with unchanged content (hash match).[/dim]")
                if keyword_skipped:
                    console.print(f"  [dim]Skipped {keyword_skipped} post(s) matching keyword filters.[/dim]")
                if expired_skipped:
                    console.print(f"  [dim]Skipped {expired_skipped} post(s) with play time already passed.[/dim]")
                console.print(
                    f"  [green]✓ Saved {saved_count} posts "
                    f"({badminton_count} badminton-related).[/green]"
                )
                total_new += saved_count
        finally:
            context.close()

    _elapsed = _time.monotonic() - _fetch_start
    _mins, _secs = divmod(int(_elapsed), 60)
    _elapsed_str = f"{_mins}m {_secs}s" if _mins else f"{_secs}s"
    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"{total_new} new post(s) fetched and cached in [dim]{settings.db_path}[/dim]. "
        f"[dim](took {_elapsed_str})[/dim]"
    )


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------

@app.command()
def posts(
    limit: int = typer.Option(
        50, "--limit", "-l", help="Maximum number of posts to display."
    ),
    group: Optional[str] = typer.Option(
        None, "--group", "-g", help="Filter by group ID or slug."
    ),
    all_posts: bool = typer.Option(
        False, "--all", help="Include non-badminton posts."
    ),
) -> None:
    """View cached posts from the local database."""
    from config import settings
    from storage.database import init_db, list_all_posts

    init_db(settings.db_path)
    results = list_all_posts(
        settings.db_path,
        group_id=group,
        badminton_only=not all_posts,
        limit=limit,
    )

    if not results:
        rprint(
            "[yellow]No posts found.[/yellow]  "
            "Run [bold]fetch[/bold] to populate the database."
        )
        raise typer.Exit(0)

    title = "Cached Posts"
    if group:
        title += f" — group: {group}"
    if all_posts:
        title += " (all)"

    table = Table(title=title, show_lines=True, highlight=True)
    table.add_column("#", style="dim", justify="right", no_wrap=True)
    table.add_column("Play Time", style="cyan", min_width=16)
    table.add_column("Level", style="bold green", min_width=6, justify="center")
    table.add_column("Location", style="yellow", min_width=16)
    table.add_column("Need", style="magenta", min_width=8, justify="center")
    table.add_column("Fetched", style="dim", min_width=10, no_wrap=True)
    table.add_column("Link", justify="center", min_width=6, no_wrap=True)

    for i, post in enumerate(results, start=1):
        iso = post.play_datetime_iso
        play_time = iso.replace("T", " ") if iso else (post.play_datetime_raw or post.post_time or "?")
        fetched = (post.fetched_at or "")[:10]
        url = post.post_url or ""
        url_cell = Text("↗ open", style=f"cyan link {url}") if url else Text("?", style="dim")
        need_parts = [str(post.players_needed)] if post.players_needed else []
        if post.players_gender:
            need_parts.append(post.players_gender)
        need_cell = "\n".join(need_parts) if need_parts else "?"
        table.add_row(
            str(i),
            play_time,
            post.level or "?",
            post.location or "?",
            need_cell,
            fetched,
            url_cell,
        )

    console.print(table)
    console.print(f"[dim]{len(results)} post(s) shown (limit={limit}).[/dim]")


# ---------------------------------------------------------------------------
# seen
# ---------------------------------------------------------------------------

@app.command()
def seen(
    limit: int = typer.Option(
        100, "--limit", "-l", help="Maximum number of entries to display."
    ),
    group: Optional[str] = typer.Option(
        None, "--group", "-g", help="Filter by group ID or slug."
    ),
    remove: Optional[str] = typer.Option(
        None, "--remove", "-r", help="Remove a post ID from the scrape cache so it will be fetched again."
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Remove ALL entries from the scrape cache so every post will be fetched again."
    ),
) -> None:
    """Show post IDs that have already been scraped (will not be fetched again)."""
    from config import settings
    from storage.database import init_db, list_seen, remove_seen, clear_seen

    init_db(settings.db_path)

    if clear:
        count = clear_seen(settings.db_path)
        rprint(f"[green]Cleared [bold]{count}[/bold] entry/entries from scrape cache.[/green]")
        raise typer.Exit(0)

    if remove:
        deleted = remove_seen(settings.db_path, remove)
        if deleted:
            rprint(f"[green]Removed post ID [bold]{remove}[/bold] from scrape cache.[/green]")
        else:
            rprint(f"[yellow]Post ID [bold]{remove}[/bold] not found in scrape cache.[/yellow]")
        raise typer.Exit(0)

    rows = list_seen(settings.db_path, group_id=group, limit=limit)

    if not rows:
        rprint("[yellow]No seen posts recorded yet.[/yellow]")
        raise typer.Exit(0)

    title = "Seen Posts (scrape cache)"
    if group:
        title += f" — group: {group}"

    table = Table(title=title, show_lines=True, highlight=True)
    table.add_column("#", style="dim", justify="right", no_wrap=True)
    table.add_column("Post ID", style="cyan", no_wrap=True)
    table.add_column("Group ID", style="yellow", no_wrap=True)
    table.add_column("Scraped At", style="dim", no_wrap=True)

    for i, row in enumerate(rows, start=1):
        table.add_row(
            str(i),
            row["post_id"],
            row["group_id"],
            (row["scraped_at"] or "")[:19],
        )

    console.print(table)
    console.print(f"[dim]{len(rows)} entry/entries shown (limit={limit}).[/dim]")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------

@app.command()
def query(
    search_query: str = typer.Argument(
        ...,
        help="Natural language query, e.g. 'trình độ TBY lúc 7 giờ tối quận 1'.",
    ),
    limit: int = typer.Option(
        20, "--limit", "-l", help="Maximum number of results to display."
    ),
) -> None:
    """Search the local cache with a natural language query."""
    from config import settings
    from llm.provider import get_provider
    from query.query_engine import search
    from storage.database import init_db

    init_db(settings.db_path)
    llm = get_provider()

    with console.status("[bold green]Searching…"):
        results = search(search_query, settings.db_path, llm, limit)

    if not results:
        rprint(
            "[yellow]No matching posts found.[/yellow]  "
            "Try broadening your query or run [bold]fetch[/bold] to refresh the cache."
        )
        raise typer.Exit(0)

    table = Table(
        title=f'Results for: "{search_query}"',
        show_lines=True,
        highlight=True,
    )
    table.add_column("#", style="dim", justify="right", no_wrap=True)
    table.add_column("Play Time", style="cyan", min_width=16)
    table.add_column("Level", style="bold green", min_width=6, justify="center")
    table.add_column("Location", style="yellow", min_width=16)
    table.add_column("Need", style="magenta", min_width=8, justify="center")
    table.add_column("Fetched", style="dim", min_width=10, no_wrap=True)
    table.add_column("Link", justify="center", min_width=6, no_wrap=True)

    for i, post in enumerate(results, start=1):
        iso = post.play_datetime_iso
        play_time = iso.replace("T", " ") if iso else (post.play_datetime_raw or post.post_time or "?")
        fetched = (post.fetched_at or "")[:10]
        url = post.post_url or ""
        url_cell = Text("↗ open", style=f"cyan link {url}") if url else Text("?", style="dim")
        need_parts = [str(post.players_needed)] if post.players_needed else []
        if post.players_gender:
            need_parts.append(post.players_gender)
        need_cell = "\n".join(need_parts) if need_parts else "?"
        table.add_row(
            str(i),
            play_time,
            post.level or "?",
            post.location or "?",
            need_cell,
            fetched,
            url_cell,
        )

    console.print(table)
    console.print(f"[dim]{len(results)} result(s).[/dim]")


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

@groups_app.command("add")
def groups_add(
    url: str = typer.Argument(..., help="Facebook group URL."),
    name: str = typer.Option("", "--name", "-n", help="Optional display name."),
) -> None:
    """Add a Facebook group to the monitored list."""
    from config import settings
    from scraper.facebook import extract_group_id
    from storage.database import init_db, upsert_group

    init_db(settings.db_path)
    gid = extract_group_id(url)
    upsert_group(settings.db_path, gid, url, name)
    rprint(f"[green]Added group:[/green] {gid}  ({url})")


@groups_app.command("list")
def groups_list() -> None:
    """List all monitored Facebook groups."""
    from config import settings
    from storage.database import init_db, list_groups

    init_db(settings.db_path)
    groups = list_groups(settings.db_path)

    if not groups:
        rprint(
            "[yellow]No groups saved.[/yellow]  "
            "Use [bold]groups add <URL>[/bold] to add one."
        )
        return

    table = Table(title="Monitored Groups")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("URL", style="dim")
    table.add_column("Added", style="dim")

    for g in groups:
        table.add_row(
            g["id"],
            g.get("name") or "-",
            g["url"],
            (g.get("added_at") or "")[:10],
        )
    console.print(table)


# ---------------------------------------------------------------------------
# keywords
# ---------------------------------------------------------------------------

@keywords_app.command("add")
def keywords_add(
    keyword: str = typer.Argument(..., help="Keyword to exclude (case-insensitive substring match)."),
) -> None:
    """Add a keyword filter — posts containing it will be skipped during fetch."""
    from config import settings
    from storage.database import init_db, add_keyword

    init_db(settings.db_path)
    add_keyword(settings.db_path, keyword)
    rprint(f"[green]Keyword added:[/green] \"{keyword}\"")


@keywords_app.command("remove")
def keywords_remove(
    keyword: str = typer.Argument(..., help="Keyword to remove from filters."),
) -> None:
    """Remove a keyword filter."""
    from config import settings
    from storage.database import init_db, remove_keyword

    init_db(settings.db_path)
    removed = remove_keyword(settings.db_path, keyword)
    if removed:
        rprint(f"[green]Keyword removed:[/green] \"{keyword}\"")
    else:
        rprint(f"[yellow]Keyword not found:[/yellow] \"{keyword}\"")


@keywords_app.command("list")
def keywords_list() -> None:
    """List all active keyword filters."""
    from config import settings
    from storage.database import init_db, list_keywords

    init_db(settings.db_path)
    rows = list_keywords(settings.db_path)

    if not rows:
        rprint("[yellow]No keyword filters set.[/yellow]  Use [bold]keywords add <keyword>[/bold].")
        return

    table = Table(title="Keyword Filters (posts containing these are skipped)")
    table.add_column("Keyword", style="cyan")
    table.add_column("Added", style="dim")

    for row in rows:
        table.add_row(row["keyword"], (row.get("added_at") or "")[:10])
    console.print(table)


# ---------------------------------------------------------------------------
# clear-posts
# ---------------------------------------------------------------------------

@app.command("clear-posts")
def clear_posts_cmd(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt."
    ),
) -> None:
    """Delete ALL posts from the local database."""
    from config import settings
    from storage.database import clear_posts, init_db

    init_db(settings.db_path)

    if not yes:
        typer.confirm(
            "This will delete ALL posts from the database. Continue?",
            abort=True,
        )

    deleted = clear_posts(settings.db_path)
    rprint(f"[green]Deleted {deleted} post(s) from the database.[/green]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_config(settings) -> None:
    missing: list = []
    if not settings.fb_email:
        missing.append("FB_EMAIL")
    if not settings.fb_password:
        missing.append("FB_PASSWORD")
    if settings.llm_provider == "openai" and not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    if missing:
        rprint(
            f"[red]Missing required environment variables: {', '.join(missing)}[/red]\n"
            "Copy [bold].env.example[/bold] to [bold].env[/bold] and fill in the values."
        )
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
