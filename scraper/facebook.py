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
    """Pull the post ID out of a Facebook permalink (numeric or pfbid format)."""
    for pattern in (
        r"/posts/([\w-]+)",   # numeric IDs and pfbid0… alphanumeric IDs
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
    # Photo URLs encode the identifier in the fbid query param — keep it.
    if "/photo" in href and "fbid=" in href:
        m = re.search(r"[?&]fbid=(\d+)", href)
        if m:
            return f"https://www.facebook.com/photo?fbid={m.group(1)}"
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
):
    """
    Scrape up to *max_posts* new posts from the Facebook group at *group_url*.
    Already-seen post IDs in *stop_at_ids* are skipped.
    Calls on_progress(collected, max_posts) after each new post is found.
    Yields RawPost objects one at a time as they are scraped.
    """
    if stop_at_ids is None:
        stop_at_ids = set()

    group_id = extract_group_id(group_url)

    # Request newest-first sort via the CHRONOLOGICAL sorting parameter.
    # We also try to click the UI's "Newest" sort option after the feed
    # loads so the ordering is explicit.
    feed_url = group_url.rstrip("/") + "?sorting_setting=CHRONOLOGICAL"

    page = context.new_page()
    try:
        page.goto(feed_url, wait_until="load", timeout=30_000)
        _dismiss_popups(page)
        # Give the React app time to bootstrap and load initial articles.
        # Do NOT scroll here — the warm-up scroll disturbs the initial article
        # loading cycle and causes the first articles to be virtualized before
        # the main loop can process them.
        time.sleep(5)
        # Wait until the feed container is present (faster than waiting for
        # /posts/ links which only appear after React renders the articles).
        try:
            page.wait_for_selector("[role='feed']", timeout=15_000)
        except Exception:
            pass  # proceed with whatever is rendered

        for p in _scroll_and_collect(page, max_posts, stop_at_ids, on_progress):
            yield RawPost(
                id=p["id"],
                group_id=group_id,
                post_url=p["post_url"],
                raw_text=p["raw_text"],
                post_time=p.get("post_time"),
            )
    finally:
        page.close()


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
        # Use JS TreeWalker to find the exact text node and dispatch a bubbling
        # click event on its parent. dispatchEvent(bubbles=true) is more reliable
        # than .click() for React-managed DOM nodes.
        result = article.evaluate("""el => {
            const targets = ['See more', 'Xem thêm', 'Xem thêm...'];
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                if (targets.includes(node.textContent.trim())) {
                    const parent = node.parentElement;
                    if (parent) {
                        parent.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        return true;
                    }
                }
            }
            return false;
        }""")
        if result:
            time.sleep(1.0)
    except Exception:
        pass


def _extract_post_text(article) -> str:
    """
    Extract the post body text, excluding text inside nested comment articles.
    Works for both div[role='article'] and plain feed-item div containers.
    """
    try:
        text = article.evaluate("""el => {
            // A div is 'in a comment' if it's inside a nested div[role='article']
            // that is NOT el itself (for the old role=article case).
            function inComment(node) {
                const a = node.closest('div[role="article"]');
                return a !== null && a !== el;
            }
            // Priority 1: specific post-body containers.
            for (const sel of ["[data-ad-comet-preview='message']", "[data-ad-preview='message']"]) {
                for (const div of el.querySelectorAll(sel)) {
                    if (inComment(div)) continue;
                    const t = (div.innerText || '').trim();
                    if (t) return t;
                }
            }
            // Priority 2: largest div[dir='auto'] not inside a comment article.
            let best = '';
            for (const div of el.querySelectorAll('div[dir="auto"]')) {
                if (inComment(div)) continue;
                const t = (div.innerText || '').trim();
                if (t.length > best.length) best = t;
            }
            return best || null;
        }""")
        if text:
            return text
    except Exception:
        pass
    # Last resort: first 150 words of the article's visible text.
    try:
        return " ".join(article.inner_text().split()[:150])
    except Exception:
        return ""


def _extract_timestamp(article) -> Optional[str]:
    """
    Try to get an ISO-format datetime string from the article.
    Priority: time[datetime] (ISO) > data-utime (epoch→ISO) > aria-label (text fallback).
    """
    try:
        result = article.evaluate("""el => {
            // Priority 1: <time datetime="..."> inside a post permalink link
            for (const sel of [
                "a[href*='/posts/'] time[datetime]",
                "a[href*='/permalink/'] time[datetime]",
                "a[role='link'] time[datetime]",
                "time[datetime]",
            ]) {
                const t = el.querySelector(sel);
                if (t) {
                    const dt = t.getAttribute('datetime');
                    if (dt) return {type: 'iso', value: dt};
                }
            }
            // Priority 2: abbr[data-utime] (legacy Facebook)
            const abbr = el.querySelector('abbr[data-utime]');
            if (abbr) {
                const u = abbr.getAttribute('data-utime');
                if (u) return {type: 'utime', value: u};
            }
            // Priority 3: aria-label on timestamp span (localized text)
            for (const sel of [
                "a[href*='/posts/'] span[aria-label]",
                "a[href*='/permalink/'] span[aria-label]",
                "a[role='link'] span[aria-label]",
            ]) {
                const s = el.querySelector(sel);
                if (s) {
                    const label = s.getAttribute('aria-label');
                    if (label) return {type: 'label', value: label};
                }
            }
            return null;
        }""")
        if not result:
            return None
        if result["type"] == "iso":
            return result["value"]
        if result["type"] == "utime":
            from datetime import datetime, timezone
            try:
                ts = int(result["value"])
                return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S+00:00"
                )
            except (ValueError, OSError):
                return result["value"]
        return result.get("value")  # aria-label text
    except Exception:
        pass
    return None


def _scroll_and_collect(page, max_posts: int, stop_at_ids: set, on_progress=None):
    """
    Scroll the group feed and yield up to *max_posts* new post dicts,
    skipping any IDs in *stop_at_ids*.
    """
    seen_ids: set = set()          # IDs we've yielded (new posts only)
    seen_all_ids: set = set()      # ALL article IDs encountered including cached
    count = 0
    # Allow scrolling roughly 3× the requested post count before giving up
    max_scroll_rounds = max(max_posts * 3, 60)
    scroll_round = 0
    # Stop only when the feed stops loading entirely new article IDs.
    # Using seen_all_ids (not just new ones) handles Facebook's DOM virtualization:
    # articles are removed from the top as new ones load at the bottom, so the
    # visible count oscillates rather than growing monotonically.
    no_discovery_streak = 0
    no_discovery_limit = 8  # 8 consecutive scrolls with zero new article IDs → end of feed

    while count < max_posts and scroll_round < max_scroll_rounds:
        # Query direct children of [role='feed'] — those are post cards.
        # Fall back to div[role='article'] if no feed container is found.
        feed_items = page.query_selector_all("[role='feed'] > div")
        articles = feed_items if feed_items else page.query_selector_all("div[role='article']")
        found_any_new_article_id = False
        no_id_skipped = 0

        for article in articles:
            if count >= max_posts:
                break
            try:
                post_id = None
                post_url = None
                # getId() rejects links with comment_id/reply_comment_id so comment
                # threads nested inside post cards are never used as the post ID source.
                try:
                    post_info = article.evaluate("""el => {
                        const re = /\\/posts\\/([\\w-]+)|\\/permalink\\/(\\d+)|[?&](?:story_fbid|fbid)=(\\d+)/;
                        function getId(href) {
                            if (/[?&](?:comment_id|reply_comment_id)=/.test(href)) return null;
                            const m = re.exec(href);
                            return m ? (m[1]||m[2]||m[3]) : null;
                        }
                        // Pass 1: link wrapping a timestamp element (most reliable).
                        for (const ts of el.querySelectorAll('span[aria-label], abbr[data-utime]')) {
                            const a = ts.closest('a[href]');
                            if (a) {
                                const id = getId(a.getAttribute('href') || '');
                                if (id) return {id, url: a.getAttribute('href')};
                            }
                        }
                        // Pass 2: any link with a recognisable post-ID pattern.
                        for (const a of el.querySelectorAll('a[href]')) {
                            const id = getId(a.getAttribute('href') || '');
                            if (id) return {id, url: a.getAttribute('href')};
                        }
                        return null;
                    }""")
                    if post_info:
                        post_id = post_info["id"]
                        post_url = _normalize_url(post_info["url"])
                except Exception:
                    pass

                if not post_id:
                    no_id_skipped += 1
                    continue

                if post_id in seen_all_ids:
                    continue  # already processed this article this session

                # Mark as discovered (even if cached) to track end-of-feed
                seen_all_ids.add(post_id)
                found_any_new_article_id = True

                if post_id in seen_ids or post_id in stop_at_ids:
                    continue  # already-seen post; don't yield but do count as discovery

                seen_ids.add(post_id)

                _expand_see_more(article)
                raw_text = _extract_post_text(article)
                post_time = _extract_timestamp(article)

                if not raw_text:
                    continue

                count += 1
                if on_progress:
                    on_progress(count, max_posts)
                yield {
                    "id": post_id,
                    "post_url": post_url or "",
                    "raw_text": raw_text,
                    "post_time": post_time,
                }

            except Exception:
                continue

        import sys as _sys
        _sys.stderr.write(
            f"[scroll {scroll_round:02d}] visible={len(articles)}"
            f" no_id={no_id_skipped} unique_ids={len(seen_all_ids)}"
            f" new_posts={count} streak={no_discovery_streak}\n"
        )
        _sys.stderr.flush()

        if found_any_new_article_id:
            no_discovery_streak = 0
        else:
            no_discovery_streak += 1
            if no_discovery_streak >= no_discovery_limit:
                break  # feed exhausted — no new article IDs for several scrolls

        # Scroll one viewport height (smaller step avoids jumping past lazily-loaded posts)
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(random.uniform(1.5, 3.0))
        scroll_round += 1
