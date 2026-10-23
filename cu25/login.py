"""One-time interactive login: you do Cornell SSO + Duo in a real browser window."""
from __future__ import annotations

import json
import os
import time

from .client import HOME

STATE_FILE = HOME / "session.json"

APP = "https://25live.collegenet.com/pro/cornell"
LOGIN_JSON = "https://25live.collegenet.com/25live/data/cornell/run/login.json?caller=pro"


def login(timeout_s: int = 300) -> str:
    from playwright.sync_api import sync_playwright

    HOME.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(APP)
        print("Sign in with your NetID + Duo in the browser window (click Sign In if needed)...")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                r = ctx.request.get(LOGIN_JSON)
                info = json.loads(r.text()).get("login_response", {}).get("login", {})
                if info.get("success") == "T" and info.get("username"):
                    ctx.storage_state(path=str(STATE_FILE))
                    os.chmod(STATE_FILE, 0o600)
                    browser.close()
                    return info["contact_name"]
            except Exception:
                pass
            time.sleep(2)
        browser.close()
    raise TimeoutError("Login not completed in time.")
