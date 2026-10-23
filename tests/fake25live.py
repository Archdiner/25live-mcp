"""A fake 25Live that answers with responses captured from the real one (HAR, Oct 2026).

`route` is shared by the in-process mock transport (unit tests) and a real HTTP
server (`serve`, e2e tests that run the installed cu25 / cu25-mcp binaries).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FX = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FX / name).read_text())


def route(method: str, path: str, p: dict, body) -> dict | None:
    if path == "sequence/id.json":
        return {"sequence": {"sequence_id": 561543 if p["itemName"] == "events" else 636747}}
    if path == "event.json":
        return {"events": {"event": {"event_id": 561543}}}
    if path == "micro/sequence/generate.json":
        return {"content": {"data": {"items": [{"idSet": [{"rsrvId": 9482714}]}]}}}
    if path.startswith("micro/event/"):
        return load("event_detail.json")
    if path == "space_avail.json":
        return load("space_avail.json")
    if path == "spaces.json":
        return load("space_detail.json") if method == "POST" else load("spaces_search.json")
    if path == "rose/event.json":
        return load("submit_response.json")
    if path == "login.json":
        return load("login.json")
    if path == "usergroup/wizardsecurity.json":
        return load("wizard_security.json")
    if path == "wizard/config.json":
        return load("form_config.json")
    if path == "rule/trees.json":
        return load("rules_form.json" if p["category_id"] == "form" else "rules_validation.json")
    if path == "search-criteria/customattributes.json":
        return load("attribute_roots_type16.json" if p["itemTypeId"] == "11" else "attribute_defs.json")
    if path == "custom/attribute/options.json":
        return {"item": load("attribute_options.json")["item"]}
    if path == "search-criteria/event/types.json":
        return load("event_types.json")
    if path == "search-criteria/event/organizations.json":
        return load("org_search.json")
    if path == "organizations.json":
        return load("org_in_query.json") if "query_id" in p else load("org_detail.json")
    return None


class _Handler(BaseHTTPRequestHandler):
    calls: list

    def _go(self):
        u = urlparse(self.path)
        if u.path.startswith("/pro/"):  # the 25Live app page: signing in sets the session cookie
            self.send_response(200)
            self.send_header("set-cookie", "JSESSIONID=signed-in; Path=/")
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>25Live</html>")
            return
        path = u.path.split("/run/", 1)[-1]
        params = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        self.calls.append((self.command, path, params, body))
        out = route(self.command, path, params, body)
        if path == "login.json" and "JSESSIONID=" not in (self.headers.get("cookie") or ""):
            out = load("login_logged_out.json")  # real 25Live: no cookie, no user
        data = json.dumps(out).encode() if out is not None else b"not found"
        self.send_response(200 if out is not None else 404)
        self.send_header("content-type", "application/json" if out is not None else "text/plain")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = _go

    def log_message(self, *a):
        pass


def serve():
    """Start the fake on a free port. Returns (base_url, calls, stop)."""
    calls: list = []
    handler = type("H", (_Handler,), {"calls": calls})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}/25live/data/cornell/run", calls, srv.shutdown
