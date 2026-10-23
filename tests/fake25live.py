"""A fake 25Live and a fake library LibCal, answering with responses captured from
the real ones (HARs, Oct 2026).

`route` and `libcal_route` are shared by the in-process mock transports (unit tests)
and a real HTTP server (`serve`, e2e tests that run the installed cu25 / cu25-mcp
binaries). On that server, paths under /25live/ are 25Live and the rest are LibCal.
"""
from __future__ import annotations

import email.parser
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlparse

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


def form_fields(content_type: str, body: bytes) -> list[tuple[str, str]]:
    """A LibCal POST body (urlencoded or multipart) as ordered (name, value) pairs."""
    if content_type.startswith("multipart/"):
        msg = email.parser.BytesParser().parsebytes(
            f"content-type: {content_type}\r\n\r\n".encode() + body)
        return [(m.get_param("name", header="content-disposition"), m.get_payload(decode=True).decode())
                for m in msg.get_payload()]
    return parse_qsl(body.decode(), keep_blank_values=True)


def libcal_route(method: str, path: str, q: dict, form: list, referer: str) -> tuple[int, str, str]:
    """(status, content type, body) as LibCal answers."""
    if method == "POST" and "/space" not in referer:
        return 403, "text/plain", "Invalid Referrer."
    html = lambda name: (200, "text/html", (FX / name).read_text())  # noqa: E731
    js = lambda out: (200, "application/json", json.dumps(out))  # noqa: E731
    f = dict(form)
    if path == "/spaces":
        if q.get("gid"):
            return html(f"lib_group_{q['gid']}.html")
        return html(f"lib_location_{q['lid']}.html" if "lid" in q else "lib_spaces.html")
    if path == "/spaces/availability/grid":
        return js(load(f"lib_grid_{f['gid']}.json"))
    if path == "/spaces/availability/booking/add":
        out = load("lib_add.json")
        if "update[end]" in f:  # picking another end time: not captured yet, shaped like the add
            b = out["bookings"][0]
            b["optionSelected"] = b["options"].index(f["update[end]"])
            b["end"], b["checksum"] = f["update[end]"], f["update[checksum]"]
        return js(out)
    if path == "/ajax/space/times":
        return js(load("lib_times.json"))
    if path == "/ajax/space/book":
        return js(load("lib_book.json"))
    return 404, "text/plain", "not found"


class _Handler(BaseHTTPRequestHandler):
    calls: list

    def _libcal(self, u):
        n = int(self.headers.get("content-length") or 0)
        form = form_fields(self.headers.get("content-type", ""), self.rfile.read(n)) if n else []
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.calls.append((self.command, u.path, q, form))
        status, ctype, text = libcal_route(self.command, u.path, q, form, self.headers.get("referer", ""))
        data = text.encode()
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _go(self):
        u = urlparse(self.path)
        if not u.path.startswith(("/25live/", "/pro/")):
            return self._libcal(u)
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
    """Start the fake on a free port. Returns (25Live base url, calls, stop); LibCal is at the root."""
    calls: list = []
    handler = type("H", (_Handler,), {"calls": calls})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}/25live/data/cornell/run", calls, srv.shutdown
