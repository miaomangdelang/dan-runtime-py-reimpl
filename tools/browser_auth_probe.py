#!/usr/bin/env python3
import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright


def build_authorize_url(login_hint: str) -> str:
    params = {
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "redirect_uri": "http://localhost:1455/auth/callback",
        "response_type": "code",
        "scope": "openid profile email offline_access",
        "code_challenge": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "code_challenge_method": "S256",
        "screen_hint": "signup",
        "login_hint": login_hint,
    }
    return f"https://auth.openai.com/oauth/authorize?{urlencode(params)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login-hint", required=True)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--timeout-ms", type=int, default=45000)
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--output-dir", default="/tmp/browser_auth_probe")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    url = build_authorize_url(args.login_hint)

    launch_kwargs = {
        "headless": not args.headful,
        "executable_path": "/usr/bin/google-chrome",
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-default-browser-check",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    }
    if args.proxy:
        launch_kwargs["proxy"] = {"server": args.proxy}

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            viewport={"width": 1440, "height": 960},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="Asia/Ulaanbaatar",
        )
        context.add_init_script(
            """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'platform', {get: () => 'Linux x86_64'});
window.chrome = window.chrome || { runtime: {} };
"""
        )
        page = context.new_page()
        goto_error = None
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except Exception as exc:  # noqa: BLE001
            goto_error = repr(exc)
        end_at = time.time() + (args.timeout_ms / 1000.0)
        while time.time() < end_at:
            try:
                title = page.title().lower()
                cur = page.url
            except Exception:  # noqa: BLE001
                page.wait_for_timeout(1000)
                continue
            if "just a moment" not in title and "__cf_chl_" not in cur and "challenge" not in cur:
                break
            page.wait_for_timeout(1500)
        state = {
            "title": page.title(),
            "url": page.url,
            "goto_error": goto_error,
            "cookies": context.cookies(),
            "html_path": str(out_dir / "page.html"),
            "screenshot_path": str(out_dir / "page.png"),
        }
        (out_dir / "page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out_dir / "page.png"), full_page=True)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
