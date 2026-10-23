"""End to end: the installed `cu25` and `cu25-mcp` programs, as separate processes,
talking HTTP to a fake 25Live that replays captured responses. This is what an
agent actually runs, minus the real server."""
import json
import os
import shutil
import subprocess
import sys

import anyio
import pytest
from fake25live import serve

pytestmark = pytest.mark.skipif(not shutil.which("cu25"), reason="pip install -e . first")


@pytest.fixture
def env(home):
    base, calls, stop = serve()
    yield {**os.environ, "CU25_BASE": base, "CU25_HOME": str(home)}, calls
    stop()


def cu25(env, *args, stdin=None):
    p = subprocess.run(["cu25", *args], env=env, input=stdin, capture_output=True, text=True, timeout=60)
    assert p.stderr == "", p.stderr
    return p.returncode, (json.loads(p.stdout) if p.stdout.lstrip().startswith("{") else p.stdout)


def test_agent_flow_from_nothing_to_submitted(env, tmp_path):
    env, calls = env
    req_file = tmp_path / "request.json"

    code, rooms = cu25(env, "rooms", "2026-10-25", "14:00", "15:00", "--people", "20", "--name", "stimson")
    assert code == 0 and rooms["rooms"][0]["free"]
    code, types = cu25(env, "types")
    assert {"type_id": 16, "name": "Meeting / Info Session"} in types["types"]
    code, orgs = cu25(env, "orgs", "generative")
    org_id = orgs["orgs"][0]["org_id"]

    # 1. what the agent knows from the user's message
    req = {"name": "Generative AI at Cornell - NME", "type_id": 16, "org_id": org_id,
           "headcount": 20, "start": "2026-10-25T14:00", "end": "2026-10-25T15:00",
           "space_id": 165, "description": "New member education for Generative AI at Cornell new members!"}
    req_file.write_text(json.dumps(req))
    code, out = cu25(env, "check", str(req_file))
    assert code == 2
    asked = {m.get("id") or m["field"] for m in out["missing"]}
    assert asked == {249, 324, 325, "agree"}

    # 2. the user answers the campus + advisor questions; follow-ups appear
    req["answers"] = {"249": "Ithaca Campus", "324": "Advisor Name", "325": False}
    code, out = cu25(env, "check", "-", stdin=json.dumps(req))
    follow = {m["id"] for m in out["missing"] if m["kind"] == "question"}
    assert code == 2 and follow == {327, 169, 176, 239, 232, 227, 193, 188}

    # 3. the user answers those and agrees to the policy
    req["answers"].update({"327": ["None of the Above"], "169": False, "176": False, "239": False,
                           "232": False, "227": False, "193": False,
                           "188": "Test Student, netid@cornell.edu"})
    req["agree"] = True
    req_file.write_text(json.dumps(req))
    code, out = cu25(env, "check", str(req_file))
    assert code == 0 and out["ready"], out

    # 4. preview (nothing sent), then confirm
    code, prev = cu25(env, "submit", str(req_file))
    assert code == 0 and prev["status"] == "preview"
    assert not any(p == "rose/event.json" for _, p, _, _ in calls)
    code, done = cu25(env, "submit", str(req_file), "--confirm", prev["confirm"])
    assert code == 0 and done["status"] == "submitted" and done["reference"] == "2026-ACWQJD"

    sent = next(b for m, p, _, b in calls if p == "rose/event.json")["events"]["event"]
    assert sent["state"] == 1 and sent["event_type_id"] == 16 and len(sent["custom_attribute"]) == 11

    code, st = cu25(env, "status", "561543")
    assert code == 0 and st["state"] == "Tentative"


def test_expired_session_tells_the_agent_what_to_do(tmp_path):
    env = {**os.environ, "CU25_HOME": str(tmp_path)}
    code, out = cu25(env, "whoami")
    assert code == 1 and out["error_type"] == "session_expired" and "cu25 login" in out["error"]


def test_guide_and_doctor(env):
    env, _ = env
    code, text = cu25(env, "guide")
    assert code == 0 and "--confirm" in text
    code, d = cu25(env, "doctor")
    assert code == 0 and d["ok"]


def test_mcp_server_over_stdio(env):
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    env, calls = env
    req = {"name": "Generative AI at Cornell - NME", "type_id": 16, "org_id": 12499, "headcount": 20,
           "start": "2026-10-25T14:00", "end": "2026-10-25T15:00", "space_id": 165}

    async def go():
        params = StdioServerParameters(command=shutil.which("cu25-mcp"), env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                init = await s.initialize()
                assert "--confirm" in (init.instructions or "") or "confirm" in (init.instructions or "")
                tools = {t.name for t in (await s.list_tools()).tools}
                assert {"rooms", "check", "submit"} <= tools
                r = await s.call_tool("check", {"request": req})
                assert not r.is_error
                assert r.structured_content["ready"] is False
                assert {m.get("id") for m in r.structured_content["missing"]} >= {249, 324, 325}
                bad = await s.call_tool("check", {"request": {**req, "headcount": 0}})
                assert bad.is_error
    anyio.run(go)


CHROMIUM = next((p for p in ["/opt/pw-browsers/chromium/chrome-linux/chrome",
                             "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"]
                 if os.path.exists(p)), shutil.which("chromium") or shutil.which("google-chrome"))


@pytest.mark.skipif(not CHROMIUM, reason="no Chromium to drive")
def test_login_reads_the_session_cookie_from_a_real_browser(env, home, tmp_path):
    env, _ = env
    wrapper = tmp_path / "chrome-wrapper"  # containers run as root, which needs --no-sandbox
    wrapper.write_text(f'#!/bin/sh\nexec "{CHROMIUM}" --no-sandbox "$@"\n')
    wrapper.chmod(0o755)
    (home / "session.json").unlink()
    p = subprocess.run([sys.executable, "-c", "from cu25.login import login; print(login(90, headless=True))"],
                       env={**env, "CU25_BROWSER": str(wrapper)}, capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "Student, Test"
    cookies = json.loads((home / "session.json").read_text())["cookies"]
    assert {"name": "JSESSIONID", "value": "signed-in", "domain": "127.0.0.1", "path": "/"} in cookies
    assert oct((home / "session.json").stat().st_mode)[-3:] == "600"
