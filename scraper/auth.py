import hashlib
import shutil
import subprocess
import tempfile
import time
import urllib.request
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


def ensure_logged_in(playwright, profile_dir: str, email: str, password: str, headless: bool = True, chrome_profile: str = "Default"):
    """
    Launch a persistent browser context and ensure we are logged in to Facebook.

    Strategy:
      1. Launch with the saved profile.
      2. Check if the session is still valid.
      3. If credentials are provided, attempt automatic login.
      4. If not logged in, instruct the user to log in via their own Chrome
         browser and read the cookies from the specified chrome_profile.

    Returns (context, page) — callers must close context when done.
    """
    context = _launch_context(playwright, profile_dir, headless=headless)
    page = context.new_page()

    if _is_logged_in(page):
        return context, page

    # Try automatic login only when credentials are provided
    if email and password:
        if _auto_login(page, email, password):
            return context, page

    # Not logged in — open a stealth browser window for manual login
    context.close()

    print(
        "\n[Auth] Not logged in to Facebook.\n"
        "       A browser window will open — please log in and solve any CAPTCHA/2FA."
    )
    fb_cookies = _manual_login_via_chrome(playwright, chrome_profile)

    # Restore the session in the Playwright persistent context
    context = _launch_context(playwright, profile_dir, headless=headless)
    context.add_cookies(fb_cookies)
    page = context.new_page()
    if not _is_logged_in(page):
        context.close()
        raise RuntimeError(
            "Could not verify login after manual attempt. "
            "Please make sure you completed the login before pressing Enter."
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


def _manual_login_via_chrome(playwright, chrome_profile: str = "Default") -> list:
    """
    Launch Chrome as a completely normal (non-automated) browser so that
    Facebook's reCAPTCHA / Arkose Labs challenge renders and works correctly.

    Strategy:
      1. Start Chrome via subprocess WITHOUT any Playwright automation flags
         (no --enable-automation, no --remote-debugging-pipe).  Chrome runs
         exactly as if the user double-clicked it, so navigator.webdriver is
         undefined and all bot-detection checks pass.
      2. Add --remote-debugging-port so Playwright can connect passively via
         CDP only to read cookies — no scripts are injected, no automation
         signals are introduced.
      3. After the user logs in and presses Enter, read cookies via CDP and
         return them.
    """
    chrome_exe = _find_chrome_exe()
    if chrome_exe is None:
        raise RuntimeError(
            "Could not find Google Chrome. "
            "Install it from https://www.google.com/chrome and try again."
        )

    tmp_profile = tempfile.mkdtemp(prefix="chrome_fb_login_")

    print("\n[Auth] Opening Chrome for manual Facebook login...")

    proc = subprocess.Popen(
        [
            str(chrome_exe),
            f"--user-data-dir={tmp_profile}",
            "--remote-debugging-port=9222",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Poll until Chrome's DevTools HTTP endpoint is ready (up to 15 s)
    cdp_url = "http://127.0.0.1:9222"
    ready = False
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{cdp_url}/json/version", timeout=1)
            ready = True
            break
        except Exception:
            time.sleep(0.5)

    if not ready:
        proc.kill()
        shutil.rmtree(tmp_profile, ignore_errors=True)
        raise RuntimeError(
            "Chrome started but --remote-debugging-port=9222 never became available.\n"
            "A system Chrome policy may be blocking remote debugging.\n"
            "Workaround: log in to Facebook in your own Chrome, open DevTools "
            "→ Application → Cookies → facebook.com, copy the 'xs' and 'c_user' "
            "values, and set FB_COOKIES=<json> in your .env."
        )

    # connect_over_cdp attaches to the already-running Chrome.
    # It does NOT inject --enable-automation or any init scripts, so the
    # browser behaves exactly like a normal user-launched Chrome.
    browser = playwright.chromium.connect_over_cdp(cdp_url)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.new_page()
    page.goto(
        "https://www.facebook.com/login",
        wait_until="domcontentloaded",
        timeout=20_000,
    )

    input("[Auth] Log in to Facebook (solve any CAPTCHA/2FA), then press Enter... ")

    all_cookies = ctx.cookies()

    try:
        browser.close()
    except Exception:
        pass
    proc.terminate()
    shutil.rmtree(tmp_profile, ignore_errors=True)

    fb_cookies = [c for c in all_cookies if "facebook.com" in c.get("domain", "")]
    if not fb_cookies:
        raise RuntimeError(
            "No Facebook cookies found. "
            "Please make sure you completed the login before pressing Enter."
        )
    return fb_cookies



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
    # Positive check: the news feed element is only present when logged in
    if page.query_selector("div[role='feed']") is not None:
        return True
    # If no login form but also no feed, be conservative — treat as not logged in
    return False


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

        # Verify success with the same check used at startup
        return _is_logged_in(page)
    except Exception:
        return False


def _find_chrome_exe() -> str:
    """Locate the Google Chrome executable on Windows."""
    import shutil
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    found = shutil.which("chrome") or shutil.which("google-chrome")
    if found:
        return found
    raise RuntimeError(
        "Could not find Chrome. Please install Google Chrome or add it to PATH."
    )
