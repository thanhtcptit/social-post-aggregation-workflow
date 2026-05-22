"""
Facebook Badminton Post Aggregation Agent
CLI entry point.

Usage
-----
  python main.py fetch --group-url URL [--latest 50]
  python main.py fetch --group-url URL [--hours 24] [--max-posts 100]
  python main.py query "trình độ TBY lúc 7 giờ tối"
  python main.py groups add URL [--name "Group name"]
  python main.py groups list
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
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
        24, "--hours", "-H", help="How many hours back to look for posts."
    ),
    max_posts: int = typer.Option(
        100, "--max-posts", "-n", help="Maximum posts to fetch per group."
    ),
    headed: bool = typer.Option(
        False,
        "--headed",
        help="Show browser window (useful for first-run login / debugging).",
    ),
) -> None:
    """Scrape the latest posts from Facebook group(s) and cache them locally.

    Use --latest N to simply grab the N most recent posts regardless of age.
    Use --hours / --max-posts together for time-windowed fetching.
    """
    from playwright.sync_api import sync_playwright

    from config import settings
    from filters.keywords import filter_posts
    from llm.provider import get_provider
    from parser.post_parser import parse_posts_batch
    from scraper.auth import ensure_logged_in, get_profile_dir
    from scraper.facebook import extract_group_id, fetch_group_posts
    from storage.database import (
        Post,
        get_cached_ids,
        init_db,
        list_keywords,
        upsert_group,
        upsert_post,
        list_groups,
    )

    _validate_config(settings)
    init_db(settings.db_path)

    # --latest N overrides both --hours and --max-posts
    if latest is not None:
        hours = 0        # no time constraint
        max_posts = latest

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
            pw, profile_dir, settings.fb_email, settings.fb_password, headless=headless
        )
        try:
            for url in urls:
                gid = extract_group_id(url)
                cached_ids = get_cached_ids(settings.db_path, gid)

                console.print(f"\n[cyan]Group:[/cyan] {gid}")
                mode_label = f"latest={max_posts}" if latest is not None else f"hours_back={hours}  max_posts={max_posts}"
                console.print(
                    f"  [dim]{mode_label}  "
                    f"already_cached={len(cached_ids)}[/dim]"
                )

                with console.status("[bold green]Scraping…"):
                    raw_posts = fetch_group_posts(
                        url, hours, max_posts, context, cached_ids
                    )

                new_posts = [p for p in raw_posts if p.id not in cached_ids]

                # Apply keyword exclusion filter before LLM parsing
                if exclude_keywords:
                    new_posts, skipped = filter_posts(new_posts, exclude_keywords)
                    if skipped:
                        console.print(
                            f"  [dim]Skipped {len(skipped)} post(s) matching keyword filters.[/dim]"
                        )

                console.print(
                    f"  Fetched [bold]{len(raw_posts)}[/bold] posts "
                    f"([bold]{len(new_posts)}[/bold] new after filters)"
                )

                if not new_posts:
                    console.print("  [dim]Nothing new to parse.[/dim]")
                    continue

                batch_input = [{"text": p.raw_text, "post_time": p.post_time} for p in new_posts]
                with console.status(
                    f"[bold green]Parsing {len(new_posts)} posts with LLM…"
                ):
                    parsed = parse_posts_batch(batch_input, llm, today)

                now_iso = datetime.now().isoformat()
                for raw, p in zip(new_posts, parsed):
                    upsert_post(
                        settings.db_path,
                        Post(
                            id=raw.id,
                            group_id=raw.group_id,
                            post_url=raw.post_url,
                            raw_text=raw.raw_text,
                            fetched_at=now_iso,
                            post_time=raw.post_time,
                            is_badminton_post=1 if p.is_badminton_post else 0,
                            players_needed=p.players_needed,
                            play_datetime_raw=p.play_datetime_raw,
                            play_datetime_iso=p.play_datetime_iso,
                            location=p.location,
                            level=p.level,
                            shuttlecock=p.shuttlecock,
                            notes=p.notes,
                        ),
                    )

                badminton_count = sum(1 for p in parsed if p.is_badminton_post)
                console.print(
                    f"  [green]✓ Saved {len(new_posts)} posts "
                    f"({badminton_count} badminton-related).[/green]"
                )
                total_new += len(new_posts)
        finally:
            context.close()

    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"{total_new} new post(s) fetched and cached in [dim]{settings.db_path}[/dim]."
    )


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
    table.add_column("Time", style="cyan", min_width=14, no_wrap=True)
    table.add_column("Level", style="bold green", min_width=6, justify="center")
    table.add_column("Location", style="yellow", min_width=16)
    table.add_column("Need", style="magenta", min_width=4, justify="center")
    table.add_column("Shuttle", style="blue", min_width=7, justify="center")
    table.add_column("Notes", style="dim", max_width=35)
    table.add_column("URL", style="dim", max_width=55)

    for post in results:
        time_str = post.play_datetime_raw or post.post_time or "?"
        table.add_row(
            (time_str or "")[:20],
            post.level or "?",
            (post.location or "?")[:20],
            str(post.players_needed) if post.players_needed else "?",
            post.shuttlecock or "?",
            (post.notes or "")[:35],
            post.post_url or "?",
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
