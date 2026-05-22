import hashlib
import time
from pathlib import Path


def get_profile_dir(email: str, base_dir: str) -> str:
    """
    Return the persistent browser profile directory for a given FB account.
    Uses a hash of the email so the path doesn't contain PII.
    """
    h = hashlib.sha256(email.encode()).hexdigest()[:16]
    path = Path(base_dir) / f"profile_{h}"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def ensure_logged_in(playwright, profile_dir: str, email: str, password: str, headless: bool = True):
    """
    Launch a persistent browser context and ensure we are logged in to Facebook.

    Strategy:
      1. Launch with the saved profile (headless).
      2. Check if the session is still valid.
      3. If not, attempt automatic login.
      4. If automatic login fails (2FA, CAPTCHA, checkpoint), relaunch in
         headed mode and wait for the user to log in manually.

    Returns (context, page) — callers must close context when done.
    """
    context = _launch_context(playwright, profile_dir, headless=headless)
    page = context.new_page()

    if _is_logged_in(page):
        return context, page

    # Try automatic login
    if _auto_login(page, email, password):
        return context, page

    # Automatic login failed — need user interaction (manual login in headed browser)
    context.close()

    print(
        "\n[Auth] Automatic login failed (2FA/CAPTCHA/checkpoint, or missing credentials).\n"
        "       A browser window will open. Please log in manually, then press Enter here."
    )
    context = _launch_context(playwright, profile_dir, headless=False)
    page = context.new_page()
    page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=20_000)
    input("[Auth] Press Enter after you have logged in successfully... ")
    if not _is_logged_in(page):
        context.close()
        raise RuntimeError(
            "Could not verify login after manual attempt. "
            "Please check your credentials and try again."
        )
    return context, page


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _launch_context(playwright, profile_dir: str, headless: bool):
    return playwright.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=headless,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="vi-VN",
        timezone_id="Asia/Ho_Chi_Minh",
    )


def _is_logged_in(page) -> bool:
    """Navigate to Facebook home and check if we land on the feed (not login)."""
    try:
        page.goto(
            "https://www.facebook.com/",
            wait_until="domcontentloaded",
            timeout=20_000,
        )
        time.sleep(2)
    except Exception:
        return False

    url = page.url
    # Redirect to login page = not logged in
    if "login" in url or "checkpoint" in url:
        return False
    # Login form still present = not logged in
    if page.query_selector("#email") is not None:
        return False
    return True


def _auto_login(page, email: str, password: str) -> bool:
    """
    Attempt to fill and submit the FB login form automatically.
    Returns True if login appears successful.
    """
    try:
        page.goto(
            "https://www.facebook.com/login",
            wait_until="domcontentloaded",
            timeout=20_000,
        )
        time.sleep(2)

        email_field = page.query_selector("#email")
        pass_field = page.query_selector("#pass")
        if not email_field or not pass_field:
            return False

        email_field.fill(email)
        pass_field.fill(password)
        pass_field.press("Enter")

        page.wait_for_load_state("domcontentloaded", timeout=20_000)
        time.sleep(3)

        url = page.url
        if "checkpoint" in url or "two_step" in url or "login" in url:
            return False

        return page.query_selector("#email") is None
    except Exception:
        return False
