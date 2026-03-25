#!/usr/bin/env python3
"""
Refresh Facebook and Instagram cookies via Playwright headless browser.

Logs into FB and IG with supplied credentials, exports cookies in Netscape
format (required by yt-dlp), base64-encodes them, and pushes to Railway env
vars (FB_COOKIES_B64 / IG_COOKIES_B64) via the Railway GraphQL API.

Required env vars:
  FB_EMAIL            Facebook login email
  FB_PASSWORD         Facebook login password
  IG_USERNAME         Instagram login username
  IG_PASSWORD         Instagram login password
  RAILWAY_TOKEN       Railway personal token
  RAILWAY_PROJECT_ID  Railway project ID
  RAILWAY_ENV_ID      Railway environment ID
  RAILWAY_SERVICE_ID  Railway service ID

Optional:
  SKIP_FB=1           Skip Facebook cookie refresh
  SKIP_IG=1           Skip Instagram cookie refresh
"""

import base64
import os
import sys
import time

import requests
from playwright.sync_api import sync_playwright

RAILWAY_API = "https://backboard.railway.app/graphql/v2"

RAILWAY_TOKEN      = os.environ["RAILWAY_TOKEN"]
RAILWAY_PROJECT_ID = os.environ["RAILWAY_PROJECT_ID"]
RAILWAY_ENV_ID     = os.environ["RAILWAY_ENV_ID"]
RAILWAY_SERVICE_ID = os.environ["RAILWAY_SERVICE_ID"]

SKIP_FB = os.environ.get("SKIP_FB") == "1"
SKIP_IG = os.environ.get("SKIP_IG") == "1"


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def cookies_to_netscape(cookies: list[dict]) -> str:
    """Convert Playwright cookie dicts to Netscape cookie-jar format."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c.get("domain", "")
        # Netscape format requires leading dot for include-subdomains
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path     = c.get("path", "/")
        secure   = "TRUE" if c.get("secure", False) else "FALSE"
        expires  = int(c.get("expires", 0))
        if expires < 0:
            expires = int(time.time()) + 365 * 24 * 3600  # 1 year for session cookies
        name  = c.get("name", "")
        value = c.get("value", "")
        lines.append(f"{domain}\t{include_sub}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def encode(netscape_text: str) -> str:
    return base64.b64encode(netscape_text.encode()).decode()


# ---------------------------------------------------------------------------
# Railway API
# ---------------------------------------------------------------------------

def railway_upsert_var(name: str, value: str) -> None:
    mutation = """
    mutation variableUpsert($input: VariableUpsertInput!) {
      variableUpsert(input: $input)
    }
    """
    variables = {
        "input": {
            "projectId":     RAILWAY_PROJECT_ID,
            "environmentId": RAILWAY_ENV_ID,
            "serviceId":     RAILWAY_SERVICE_ID,
            "name":          name,
            "value":         value,
        }
    }
    resp = requests.post(
        RAILWAY_API,
        json={"query": mutation, "variables": variables},
        headers={"Authorization": f"Bearer {RAILWAY_TOKEN}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Railway API error for {name}: {data['errors']}")
    print(f"  ✓ {name} updated in Railway")


# ---------------------------------------------------------------------------
# Facebook login
# ---------------------------------------------------------------------------

def refresh_fb(pw) -> str:
    email    = os.environ["FB_EMAIL"]
    password = os.environ["FB_PASSWORD"]

    print("Launching browser for Facebook...")
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )
    page = ctx.new_page()

    try:
        page.goto("https://www.facebook.com/login", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)

        # Fill credentials
        page.fill("#email", email)
        page.fill("#pass", password)
        page.click('[name="login"]')
        page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(3)

        # Verify login succeeded — look for home feed indicators
        url = page.url
        if "login" in url or "checkpoint" in url:
            # Checkpoint = suspicious login / CAPTCHA — capture screenshot for debugging
            page.screenshot(path="fb_login_failed.png")
            raise RuntimeError(
                f"Facebook login failed or hit checkpoint. URL: {url}\n"
                "Screenshot saved to fb_login_failed.png\n"
                "Ensure 2FA is disabled and the account has no pending security checks."
            )

        cookies = ctx.cookies()
        fb_cookies = [c for c in cookies if "facebook.com" in c.get("domain", "")]
        if not fb_cookies:
            raise RuntimeError("Facebook login appeared to succeed but no FB cookies found.")

        netscape = cookies_to_netscape(fb_cookies)
        print(f"  ✓ Facebook: {len(fb_cookies)} cookies extracted")
        return encode(netscape)

    finally:
        browser.close()


# ---------------------------------------------------------------------------
# Instagram login
# ---------------------------------------------------------------------------

def refresh_ig(pw) -> str:
    username = os.environ["IG_USERNAME"]
    password = os.environ["IG_PASSWORD"]

    print("Launching browser for Instagram...")
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        viewport={"width": 390, "height": 844},
        locale="en-US",
    )
    page = ctx.new_page()

    try:
        page.goto("https://www.instagram.com/accounts/login/", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        time.sleep(2)

        # Dismiss cookie banner if present
        try:
            page.click('text="Allow all cookies"', timeout=4000)
            time.sleep(1)
        except Exception:
            pass

        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(4)

        url = page.url
        if "login" in url or "challenge" in url or "checkpoint" in url:
            page.screenshot(path="ig_login_failed.png")
            raise RuntimeError(
                f"Instagram login failed or hit challenge. URL: {url}\n"
                "Screenshot saved to ig_login_failed.png\n"
                "Ensure 2FA is disabled and the account has no pending security checks."
            )

        # Dismiss "Save your login info?" prompt if shown
        try:
            page.click('text="Not now"', timeout=4000)
            time.sleep(1)
        except Exception:
            pass

        cookies = ctx.cookies()
        ig_cookies = [c for c in cookies if "instagram.com" in c.get("domain", "")]
        if not ig_cookies:
            raise RuntimeError("Instagram login appeared to succeed but no IG cookies found.")

        netscape = cookies_to_netscape(ig_cookies)
        print(f"  ✓ Instagram: {len(ig_cookies)} cookies extracted")
        return encode(netscape)

    finally:
        browser.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    errors = []

    with sync_playwright() as pw:
        if not SKIP_FB:
            try:
                fb_b64 = refresh_fb(pw)
                railway_upsert_var("FB_COOKIES_B64", fb_b64)
            except Exception as e:
                print(f"ERROR (Facebook): {e}", file=sys.stderr)
                errors.append(f"Facebook: {e}")

        if not SKIP_IG:
            try:
                ig_b64 = refresh_ig(pw)
                railway_upsert_var("IG_COOKIES_B64", ig_b64)
            except Exception as e:
                print(f"ERROR (Instagram): {e}", file=sys.stderr)
                errors.append(f"Instagram: {e}")

    if errors:
        print(f"\n{len(errors)} error(s) occurred:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    print("\nAll cookies refreshed and pushed to Railway successfully.")


if __name__ == "__main__":
    main()
