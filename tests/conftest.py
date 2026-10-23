import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import fake25live  # noqa: E402
from fake25live import form_fields, route  # noqa: E402

from cu25 import client as C  # noqa: E402
from cu25 import library as LIB  # noqa: E402
from cu25.models import Request  # noqa: E402


@pytest.fixture
def home(tmp_path):
    (tmp_path / "session.json").write_text(json.dumps({"cookies": [
        {"name": "JSESSIONID", "value": "x", "domain": "25live.collegenet.com", "path": "/"},
        {"name": "JSESSIONID", "value": "x", "domain": "127.0.0.1", "path": "/"}]}))  # e2e fake
    return tmp_path


@pytest.fixture
def fake(home):
    """A Client wired to the fake 25Live. Returns (client, calls)."""
    calls = []

    def handler(req: httpx.Request):
        path = req.url.path.split("/run/")[1]
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, path, body))
        out = route(req.method, path, dict(req.url.params), body)
        return httpx.Response(200, json=out) if out is not None else httpx.Response(404)

    c = C.Client(home=home)
    c.http = httpx.Client(transport=httpx.MockTransport(handler), headers=c.http.headers)
    return c, calls


@pytest.fixture
def library(home):
    """A Library wired to the fake LibCal, the day the HAR was taken. Returns (library, calls)."""
    from datetime import datetime
    calls = []

    def handler(req: httpx.Request):
        form = form_fields(req.headers.get("content-type", ""), req.content) if req.content else []
        calls.append((req.method, req.url.path, form))
        status, ctype, text = fake25live.libcal_route(req.method, req.url.path, dict(req.url.params), form,
                                           req.headers.get("referer", ""))
        return httpx.Response(status, headers={"content-type": ctype}, text=text)

    lib = LIB.Library(home=home)
    lib.http = httpx.Client(transport=httpx.MockTransport(handler), headers=lib.http.headers)
    lib.now = lambda: datetime(2026, 10, 23, 2, 5)
    return lib, calls


@pytest.fixture
def real_request(fake):
    """The real event from the HAR, as copy produces it, with its real time and agreement."""
    c, _ = fake
    req = c.copy_request(561543).request
    return Request.model_validate({**req.model_dump(), "start": "2026-10-25T14:00",
                                   "end": "2026-10-25T15:00", "agree": True})


def edit(req: Request, answers: dict | None = None, **fields) -> Request:
    """A changed copy of a request, validated like any other input."""
    return Request.model_validate({**req.model_dump(), **fields,
                                   "answers": {**req.answers, **(answers or {})}})
