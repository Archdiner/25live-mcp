"""HTTP client for the library's study spaces (LibCal at spaces.library.cornell.edu).

Every call here was captured from LibCal's public booking page (HAR, Oct 2026). No
login: a booking is made under the name and @cornell.edu email the user gives, and
confirmed at once. The calls need a same-site Referer ("Invalid Referrer." otherwise).

Environment:
  CU25_LIBCAL  LibCal base URL (tests point this at a fake server)
  CU25_HOME, CU25_RECORD  as for 25Live
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import date, datetime
from urllib.parse import urlencode

import httpx

from . import libcal as L
from .client import HOME, CU25Error, Recorder, UpstreamChanged, parallel, parse_slot
from .models import (LibraryPreview, LibraryRequest, LibraryResult, LibrarySpace, LibrarySpaces,
                     MissingField)

BASE = os.environ.get("CU25_LIBCAL", "https://spaces.library.cornell.edu")
CATALOG_SECONDS = 3600  # spaces change rarely; availability is always live
METHOD = 12  # the booking page's springyPage.bookingMethod for a single space
ASK = {"space_id": "Which library space? (from libspaces)", "start": "Start time?",
       "end": "End time?", "first_name": "First name?", "last_name": "Last name?",
       "email": "Your @cornell.edu email? The confirmation goes there."}


def library_slot(day: str, start: str | None, end: str | None) -> tuple[date, datetime | None, datetime | None]:
    """'2026-10-29' [, '17:00', '18:00'] -> the day, and the time asked about if any."""
    if (start is None) != (end is None):
        raise ValueError("give both a start and an end time, or neither")
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise ValueError(f"expected YYYY-MM-DD, got {day!r}")
    return (d, *parse_slot(day, start, end)) if start else (d, None, None)


class Refused(CU25Error):
    """LibCal said no (HTTP 400 with a plain-text reason, e.g. 'Invalid Checksum.')."""


class Library:
    def __init__(self, home=HOME, base: str = BASE):
        self.home, self.base = home, base.rstrip("/")
        self.http = httpx.Client(timeout=30, follow_redirects=False, headers={
            "x-requested-with": "XMLHttpRequest", "origin": self.base})
        self._record = Recorder()
        self.now = datetime.now

    # ---- plumbing -------------------------------------------------------
    def _call(self, method: str, path: str, referer: str, form: list | None = None,
              multipart: list | None = None) -> httpx.Response:
        headers = {"referer": self.base + referer}
        if form is not None:
            headers["content-type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        r = self.http.request(method, self.base + path, headers=headers,
                              content=urlencode(form) if form is not None else None,
                              files=[(k, (None, v)) for k, v in multipart] if multipart else None)
        self._record(method, path, "", form or multipart, r)
        if r.status_code == 400:
            raise Refused(r.text.strip() or "The library refused the booking.")
        if r.status_code != 200:
            raise CU25Error(f"{method} {path} failed ({r.status_code}): {r.text[:300]}")
        return r

    def _json(self, what: str, path: str, referer: str, **kw) -> dict:
        r = self._call("POST", path, referer, **kw)
        try:
            return r.json()
        except ValueError:
            raise UpstreamChanged(f"LibCal's {what} didn't return JSON: {r.text[:200]}")

    def _parse(self, what: str, fn, *args):
        try:
            return fn(*args)
        except (KeyError, IndexError, TypeError, AttributeError, ValueError) as e:
            raise UpstreamChanged(f"LibCal's {what} has an unexpected shape ({e!r}). "
                                  "Run with CU25_RECORD=dir to capture it.")

    # ---- spaces ---------------------------------------------------------
    def catalog(self, fresh: bool = False) -> list[dict]:
        """Every bookable space: libraries -> their categories -> the spaces on each category page."""
        cache = self.home / "library-cache.json"
        if not fresh and cache.exists():
            c = json.loads(cache.read_text())
            if c.get("base") == self.base and time.time() - c.get("fetched_at", 0) < CATALOG_SECONDS:
                return c["spaces"]
        page = lambda path: self._call("GET", path, path).text  # noqa: E731
        libs = self._parse("library list", L.parse_options, page("/spaces"), "lid")
        cats = parallel(*[lambda lid=lid: self._parse("category list", L.parse_options,
                                                      page(f"/spaces?lid={lid}"), "gid")
                          for lid, _ in libs])
        groups = [(lid, lib, gid) for (lid, lib), gs in zip(libs, cats) for gid, _ in gs]
        pages = parallel(*[lambda lid=lid, gid=gid: page(f"/spaces?lid={lid}&gid={gid}")
                           for lid, _, gid in groups]) if groups else []
        spaces, seen = [], set()
        for (_, lib, _), p in zip(groups, pages):
            for s in self._parse("space list", L.parse_resources, p):
                if s["space_id"] not in seen:
                    seen.add(s["space_id"])
                    spaces.append({**s, "library": lib})
        if not spaces:
            raise UpstreamChanged("LibCal listed no spaces. Run with CU25_RECORD=dir to capture it.")
        self.home.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"base": self.base, "fetched_at": time.time(), "spaces": spaces}))
        return spaces

    def grid(self, lid: int, gid: int, day: date, eid: int = -1) -> dict[int, list[dict]]:
        """Slots that day for every space in a category (eid narrows nothing; the page sends it).
        The grid pages by space, so a category with more spaces than a page takes more calls."""
        size = sum(1 for s in self.catalog() if (s["lid"], s["gid"]) == (lid, gid))
        pages = max(1, math.ceil(size / L.PAGE_SIZE))
        ref = f"/space/{eid}" if eid > 0 else f"/spaces?lid={lid}&gid={gid}"
        resps = parallel(*[lambda p=p: self._json("availability", "/spaces/availability/grid", ref,
                                                  form=L.grid_body(lid, gid, eid, day, p))
                           for p in range(pages)])
        out: dict[int, list[dict]] = {}
        for r in resps:
            out.update(self._parse("availability", L.parse_grid, r))
        return out

    def spaces(self, day: date, start: datetime | None = None, end: datetime | None = None,
               people: int | None = None, name: str | None = None, limit: int = 50) -> LibrarySpaces:
        """Library spaces with their free times that day; with start/end, free ones first."""
        if start and end and end <= start:
            raise CU25Error("End time must be after start time.")
        found = [s for s in self.catalog()
                 if (not people or (s["capacity"] or 0) >= people)
                 and (not name or name.lower() in f"{s['library']} {s['category']} {s['name']}".lower())]
        groups = sorted({(s["lid"], s["gid"]) for s in found})
        grids = parallel(*[lambda g=g: self.grid(*g, day) for g in groups]) if groups else []
        slots = {k: v for g in grids for k, v in g.items()}
        out = [LibrarySpace(
            space_id=s["space_id"], name=s["name"], library=s["library"], category=s["category"],
            capacity=s["capacity"], url=f"{self.base}/space/{s['space_id']}",
            free=L.is_free(slots.get(s["space_id"], []), start, end) if start and end else None,
            free_times=L.free_ranges(slots.get(s["space_id"], []))) for s in found]
        out.sort(key=lambda s: (not s.free, s.capacity or 0))
        return LibrarySpaces(spaces=out if limit == 0 else out[:limit], total=len(out))

    # ---- booking --------------------------------------------------------
    def _pick(self, space: dict, req: LibraryRequest) -> tuple[dict | None, list[str]]:
        """Pick the times as the page does: grid, click the start slot, then pick the end."""
        mine = self.grid(space["lid"], space["gid"], req.start.date(), eid=space["space_id"]).get(
            space["space_id"], [])
        free = ", ".join(L.free_ranges(mine)) or "nothing"
        first = next((s for s in mine if s["start"] == req.start), None)
        if not first or not L.is_free(mine, req.start, req.end):
            step = f" Times start every {int((mine[0]['end'] - mine[0]['start']).seconds / 60)} " \
                   "minutes." if mine and not first else ""
            return None, [f"{space['name']} isn't free {req.start:%H:%M}-{req.end:%H:%M}.{step} "
                          f"Free that day: {free}."]
        ref = f"/space/{space['space_id']}"
        r = self._json("booking", "/spaces/availability/booking/add", ref,
                       form=L.add_body(space, req.start, first["checksum"]))
        b = self._parse("booking", lambda: r["bookings"][0])
        if r.get("limitIssues"):
            return None, [f"The library's limits: {json.dumps(r['limitIssues'])}"]
        ends = self._parse("booking", lambda: [datetime.fromisoformat(o) for o in b["options"]])
        if req.end not in ends:
            return None, [f"From {req.start:%H:%M} you can book {space['name']} until "
                          f"{', '.join(f'{e:%H:%M}' for e in ends)} (the library's limit)."]
        if datetime.fromisoformat(b["end"]) != req.end:
            r = self._json("booking", "/spaces/availability/booking/add", ref, form=L.update_body(b, req.end))
            b = self._parse("booking", lambda: r["bookings"][0])
        return b, []

    def book(self, req: LibraryRequest, confirm: str | None = None) -> LibraryResult:
        """Without confirm: check the times and preview (nothing held or booked).
        With the code from that preview: hold the times, fill the form and book exactly that."""
        missing = [MissingField(field=k, question=q) for k, q in ASK.items() if getattr(req, k) in (None, "")]
        space = next((s for s in self.catalog() if s["space_id"] == req.space_id), None)
        problems = []
        if req.space_id and not space:
            problems.append(f"No library space {req.space_id}. Find one with libspaces.")
        if req.start and req.end:
            if req.end <= req.start:
                problems.append("End time must be after start time.")
            elif req.end.date() != req.start.date() and req.end.time() != datetime.min.time():
                problems.append("A library booking starts and ends on the same day.")
            if req.start < self.now():
                problems.append("That start time has passed.")
        if req.email and "@" not in req.email:
            problems.append(f"{req.email!r} isn't an email address.")
        if missing or problems:
            return LibraryResult(status="not_ready", booked=False, missing=missing, problems=problems)
        try:
            b, problems = self._pick(space, req)
            if problems:
                return LibraryResult(status="not_ready", booked=False, problems=problems)
            preview = LibraryPreview(
                space=space["name"], library=f"{space['library']} ({space['category']})",
                start=L.fmt(req.start), end=L.fmt(req.end), name=f"{req.first_name} {req.last_name}",
                email=req.email, answers=req.answers)
            code = hashlib.sha256(json.dumps([preview.model_dump(), space], sort_keys=True).encode()
                                  ).hexdigest()[:10]
            if confirm is None:
                return LibraryResult(status="preview", booked=False, preview=preview, confirm=code)
            if confirm != code:
                return LibraryResult(status="changed", booked=False, preview=preview, confirm=code, messages=[
                    "The booking changed since that preview. Show this preview to the user and "
                    "confirm again with the new code."])
            return self._book(req, b)
        except Refused as e:
            return LibraryResult(status="not_ready", booked=False, problems=[f"The library says: {e}"])

    def _book(self, req: LibraryRequest, b: dict) -> LibraryResult:
        ref = f"/space/{b['eid']}"
        r = self._json("booking form", "/ajax/space/times", ref, form=L.times_body(b, METHOD))
        form = self._parse("booking form", lambda: L.parse_form(r["html"]))
        values: dict = {k: getattr(req, key) for k, key in L.KNOWN.items()}
        names = {f["name"] for f in form["fields"]}
        missing, problems = [], [f"The booking form has no question {k!r}." for k in req.answers if k not in names]
        for f in form["fields"]:
            if f["name"] in L.KNOWN:
                continue
            if f["name"] in req.answers:
                values[f["name"]] = req.answers[f["name"]]
            elif f["required"]:
                missing.append(MissingField(field=f"answers.{f['name']}", question=f["label"],
                                            options=[{"value": o} for o in f["options"]] if f["options"] else None))
        if form["email_domains"] and not any(req.email.lower().endswith(d.lower()) for d in form["email_domains"]):
            problems.append(f"The library only takes {' or '.join(form['email_domains'])} addresses.")
        if missing or problems:
            return LibraryResult(status="not_ready", booked=False, missing=missing, problems=problems, messages=[
                "Nothing was booked; the held times lapse by themselves. Fix these and preview again."])
        values = {k: v for k, v in values.items() if k in names}
        r = self._json("booking", "/ajax/space/book", ref,
                       multipart=L.book_fields(form["session"], values, b, METHOD))
        out = self._parse("booking", L.parse_booked, r)
        return LibraryResult(status="booked", booked=True, **out)
