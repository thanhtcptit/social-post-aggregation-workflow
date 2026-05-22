def should_exclude(text: str, keywords: list) -> bool:
    """
    Return True if *text* contains any of the given keywords.
    Matching is case-insensitive and works on raw Unicode (no normalisation),
    which handles Vietnamese diacritics correctly for exact-substring matching.
    """
    if not keywords:
        return False
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def filter_posts(posts: list, keywords: list) -> tuple:
    """
    Split *posts* (list of RawPost) into (kept, skipped) based on keyword
    exclusion.  Posts whose raw_text matches any keyword are placed in
    *skipped* and will not be parsed or stored.

    Returns (kept_posts, skipped_posts).
    """
    if not keywords:
        return posts, []

    kept = []
    skipped = []
    for post in posts:
        if should_exclude(post.raw_text, keywords):
            skipped.append(post)
        else:
            kept.append(post)
    return kept, skipped
