import random
import re
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class RawPost:
    id: str
    group_id: str
    post_url: str
    raw_text: str
    post_time: Optional[str]


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def extract_group_id(group_url: str) -> str:
    """Extract the group ID or slug from a Facebook group URL."""
    match = re.search(r"/groups/([^/?#]+)", group_url)
    return match.group(1) if match else group_url


def _extract_post_id(href: str) -> Optional[str]:
    """Pull the numeric post ID out of a Facebook permalink."""
    for pattern in (
        r"/posts/(\d+)",
        r"/permalink/(\d+)",
        r"story_fbid=(\d+)",
        r"fbid=(\d+)",
    ):
        m = re.search(pattern, href)
        if m:
            return m.group(1)
    return None


def _normalize_url(href: str) -> str:
    if not href.startswith("http"):
        href = "https://www.facebook.com" + href
    # Drop query-string noise but keep path
    return href.split("?")[0].rstrip("/")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_group_posts(
    group_url: str,
    hours_back: int,
    max_posts: int,
    context,
    stop_at_ids: Optional[set] = None,
    on_progress=None,
) -> list:
    """
    Scrape up to *max_posts* posts from the Facebook group at *group_url*.
    Already-cached post IDs in *stop_at_ids* are skipped.
    Calls on_progress(collected, max_posts) after each new post is found.
    Returns a list of RawPost objects.
    """
    if stop_at_ids is None:
        stop_at_ids = set()

    group_id = extract_group_id(group_url)

    # Request newest-first sort
    if "sorting_setting" not in group_url:
        sep = "&" if "?" in group_url else "?"
        feed_url = group_url.rstrip("/") + sep + "sorting_setting=RECENT_ACTIVITY"
    else:
        feed_url = group_url

    page = context.new_page()
    try:
        page.goto(feed_url, wait_until="domcontentloaded", timeout=30_000)
        _dismiss_popups(page)
        time.sleep(3)

        raw_posts = _scroll_and_collect(page, max_posts, stop_at_ids, on_progress)
    finally:
        page.close()

    return [
        RawPost(
            id=p["id"],
            group_id=group_id,
            post_url=p["post_url"],
            raw_text=p["raw_text"],
            post_time=p.get("post_time"),
        )
        for p in raw_posts
    ]


# ---------------------------------------------------------------------------
# Scraping internals
# ---------------------------------------------------------------------------

def _dismiss_popups(page) -> None:
    """Close common Facebook overlay dialogs that block scrolling."""
    close_selectors = [
        "[aria-label='Close']",
        "[aria-label='Đóng']",
        "div[role='dialog'] [role='button']",
    ]
    for sel in close_selectors:
        try:
            btn = page.query_selector(sel)
            if btn:
                btn.click()
                time.sleep(0.5)
        except Exception:
            pass


def _expand_see_more(article) -> None:
    """Click 'See more' / 'Xem thêm' inside an article to reveal full text."""
    try:
        buttons = article.query_selector_all(
            "div[role='button'], span[role='button']"
        )
        for btn in buttons:
            label = (btn.inner_text() or "").strip().lower()
            if label in ("see more", "xem thêm", "xem thêm..."):
                btn.click()
                time.sleep(0.4)
                break
    except Exception:
        pass


def _extract_post_text(article) -> str:
    """
    Try several selectors in order of preference to get the post body text.
    Returns the longest non-empty match.
    """
    selectors = [
        "div[data-ad-comet-preview='message']",
        "div[data-ad-preview='message']",
        "div[dir='auto']",
    ]
    best = ""
    for sel in selectors:
        try:
            el = article.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if len(text) > len(best):
                    best = text
        except Exception:
            pass

    if not best:
        try:
            best = article.inner_text().strip()[:3000]
        except Exception:
            pass

    return best


def _extract_timestamp(article) -> Optional[str]:
    """
    Try to get a human-readable timestamp string from the article.
    Prefers aria-label (often contains absolute date) over visible relative text.
    """
    try:
        # Timestamp links for group posts
        for sel in (
            "a[href*='/posts/'] span[aria-label]",
            "a[href*='/permalink/'] span[aria-label]",
            "abbr[data-utime]",
            "a[role='link'] span[aria-label]",
        ):
            el = article.query_selector(sel)
            if el:
                label = el.get_attribute("aria-label")
                if label:
                    return label
                utime = el.get_attribute("data-utime")
                if utime:
                    return utime
    except Exception:
        pass
    return None


def _scroll_and_collect(page, max_posts: int, stop_at_ids: set, on_progress=None) -> list:
    """
    Scroll the group feed, extract posts, and return up to *max_posts* items
    that are not in *stop_at_ids*.
    """
    collected: list = []
    seen_ids: set = set()
    # Allow scrolling roughly 3× the requested post count before giving up
    max_scroll_rounds = max(max_posts * 3, 30)
    scroll_round = 0
    no_new_streak = 0  # consecutive scrolls without new posts

    while len(collected) < max_posts and scroll_round < max_scroll_rounds:
        articles = page.query_selector_all("div[role='article']")
        found_new = False

        for article in articles:
            if len(collected) >= max_posts:
                break
            try:
                # Find permalink inside this article
                post_id = None
                post_url = None
                for link_sel in (
                    "a[href*='/posts/']",
                    "a[href*='/permalink/']",
                ):
                    links = article.query_selector_all(link_sel)
                    for link in links:
                        href = link.get_attribute("href") or ""
                        pid = _extract_post_id(href)
                        if pid:
                            post_id = pid
                            post_url = _normalize_url(href)
                            break
                    if post_id:
                        break

                if not post_id:
                    continue
                if post_id in seen_ids or post_id in stop_at_ids:
                    continue

                seen_ids.add(post_id)

                _expand_see_more(article)
                raw_text = _extract_post_text(article)
                post_time = _extract_timestamp(article)

                if not raw_text:
                    continue

                collected.append(
                    {
                        "id": post_id,
                        "post_url": post_url or "",
                        "raw_text": raw_text,
                        "post_time": post_time,
                    }
                )
                if on_progress:
                    on_progress(len(collected), max_posts)
                found_new = True

            except Exception:
                continue

        if not found_new:
            no_new_streak += 1
            if no_new_streak >= 5:
                # 5 consecutive scrolls with no new posts → probably hit end of feed
                break
        else:
            no_new_streak = 0

        # Scroll down and wait
        page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        time.sleep(random.uniform(1.5, 3.0))
        scroll_round += 1

    return collected
