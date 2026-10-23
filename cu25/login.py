"""One-time login: the user does Cornell SSO + Duo in their own Chrome/Edge/Chromium.

No browser automation library: we start the installed browser with a dedicated
profile (~/.cu25/browser, so Duo's "remember me" sticks) and remote debugging on a
random local port, then read its cookies over the DevTools protocol until
25Live says we're signed in. Set CU25_BROWSER to use a specific browser binary.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from .client import BASE, HOME, Client, SessionExpired

CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser",
]


def find_browser() -> str:
    for c in [os.environ.get("CU25_BROWSER"), *CANDIDATES]:
        if c and (Path(c).is_file() or shutil.which(c)):
            return shutil.which(c) or c
    raise SessionExpired("No Chrome, Edge, Brave or Chromium found. Install one, or set "
                         "CU25_BROWSER to its path, then run: cu25 login")


class DevTools:
    """The smallest websocket client that speaks Chrome's DevTools protocol (text frames only)."""

    def __init__(self, ws_url: str):
        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        if b" 101 " not in self._until(b"\r\n\r\n"):
            raise SessionExpired("The browser refused the DevTools connection.")
        self.n = 0

    def _until(self, end: bytes) -> bytes:
        buf = b""
        while not buf.endswith(end):
            buf += self.sock.recv(1)
        return buf

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("browser closed")
            buf += chunk
        return buf

    def call(self, method: str, **params) -> dict:
        self.n += 1
        data = json.dumps({"id": self.n, "method": method, "params": params}).encode()
        mask = os.urandom(4)
        size = (bytes([0x80 | len(data)]) if len(data) < 126 else
                bytes([0xFE]) + len(data).to_bytes(2, "big") if len(data) < 65536 else
                bytes([0xFF]) + len(data).to_bytes(8, "big"))
        self.sock.sendall(b"\x81" + size + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        while True:  # skip events until our reply arrives
            _, b1 = self._read(2)
            n = b1 & 0x7F
            n = int.from_bytes(self._read(2 if n == 126 else 8), "big") if n >= 126 else n
            msg = json.loads(self._read(n))
            if msg.get("id") == self.n:
                return msg.get("result", {})


def login(timeout_s: int = 300, headless: bool = False) -> str:
    """Open the browser at 25Live, wait for sign-in, save the cookies. Returns the user's name."""
    HOME.mkdir(exist_ok=True)
    profile = HOME / "browser"
    (profile / "DevToolsActivePort").unlink(missing_ok=True)
    u = urlparse(BASE)
    app = f"{u.scheme}://{u.netloc}/pro/{u.path.split('/data/')[1].split('/')[0]}"
    proc = subprocess.Popen(
        [find_browser(), f"--user-data-dir={profile}", "--remote-debugging-port=0",
         "--no-first-run", "--no-default-browser-check", *(["--headless=new"] if headless else []), app],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + timeout_s
        port_file = profile / "DevToolsActivePort"
        while not port_file.exists():
            if time.time() > deadline or proc.poll() is not None:
                raise SessionExpired("The browser closed before sign-in finished. Run: cu25 login")
            time.sleep(0.2)
        port, path = port_file.read_text().split()[:2]
        dt = DevTools(f"ws://127.0.0.1:{port}{path}")
        state = HOME / "session.json"
        while time.time() < deadline:
            cookies = [{k: c[k] for k in ("name", "value", "domain", "path")}
                       for c in dt.call("Storage.getCookies").get("cookies", [])]
            state.write_text(json.dumps({"cookies": cookies}))
            os.chmod(state, 0o600)
            try:
                me = Client().whoami()
                dt.call("Browser.close")
                return me.name
            except SessionExpired:
                time.sleep(2)
        raise SessionExpired("Sign-in wasn't finished in time. Run: cu25 login")
    finally:
        if proc.poll() is None:
            proc.terminate()
