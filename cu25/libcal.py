"""Pure functions that read LibCal pages and build LibCal request bodies.

LibCal (Springshare) runs the library's space bookings at spaces.library.cornell.edu.
Shapes match what its public booking page sent when a space was booked by hand
(captured from a HAR, Oct 2026). Nothing here touches the network.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from html import unescape
from html.parser import HTMLParser

PAGE_SIZE = 18  # what the booking page asks the grid for
KNOWN = {"fname": "first_name", "lname": "last_name", "email": "email"}  # form field -> request key


def fmt(dt: datetime) -> str:
    """LibCal's own format for booking times, e.g. 2026-10-26 17:00."""
    return dt.strftime("%Y-%m-%d %H:%M")


def parse_options(page: str, select: str) -> list[tuple[int, str]]:
    """(value, label) of a <select name=...> on a LibCal page, minus 'Show All' (0)."""
    m = re.search(r'<select[^>]*name="%s".*?</select>' % select, page, re.S)
    if not m:
        raise ValueError(f"no <select name={select}>")
    return [(int(v), unescape(t).strip()) for v, t in
            re.findall(r'<option value="(\d+)"[^>]*>([^<]*)</option>', m.group(0)) if v != "0"]


def _js_value(v: str):
    v = v.strip()
    return json.loads(v) if v.startswith('"') else v


def parse_resources(page: str) -> list[dict]:
    """The spaces on a category page: the `resources.push({...})` blocks its script builds."""
    out = []
    for block in re.findall(r"resources\.push\(\{(.*?)\}\);", page, re.S):
        f = {k: _js_value(v) for k, v in re.findall(r"^\s*(\w+): (.*?),\s*$", block, re.M)}
        out.append({"space_id": int(f["eid"]), "lid": int(f["lid"]), "gid": int(f["gid"]),
                    "name": re.sub(r" \(Capacity \d+\)$", "", f["title"]), "category": f["grouping"].strip(),
                    "capacity": int(f["capacity"]) or None if f.get("capacity", "").isdigit() else None})
    return out


def grid_body(lid: int, gid: int, eid: int, day: date, page: int = 0) -> list[tuple[str, str]]:
    return [("lid", str(lid)), ("gid", str(gid)), ("eid", str(eid)), ("seat", "0"), ("seatId", "0"),
            ("zone", "0"), ("start", day.isoformat()), ("end", (day + timedelta(days=1)).isoformat()),
            ("pageIndex", str(page)), ("pageSize", str(PAGE_SIZE))]


def parse_grid(resp: dict) -> dict[int, list[dict]]:
    """Space id -> its slots that day, in order. A slot with a className is taken."""
    out: dict[int, list[dict]] = {}
    for s in resp["slots"]:
        out.setdefault(int(s["itemId"]), []).append({
            "start": datetime.fromisoformat(s["start"]), "end": datetime.fromisoformat(s["end"]),
            "free": not s.get("className"), "checksum": s["checksum"]})
    for slots in out.values():
        slots.sort(key=lambda s: s["start"])
    return out


def is_free(slots: list[dict], start: datetime, end: datetime) -> bool:
    """Free slots cover [start, end) without a gap, starting exactly at `start`."""
    t = start
    for s in slots:
        if s["start"] == t and s["free"]:
            t = s["end"]
            if t >= end:
                return True
    return False


def free_ranges(slots: list[dict]) -> list[str]:
    """Free slots merged into ranges, e.g. ['08:00-12:30', '14:00-23:00']."""
    ranges: list[list[datetime]] = []
    for s in slots:
        if not s["free"]:
            continue
        if ranges and ranges[-1][1] == s["start"]:
            ranges[-1][1] = s["end"]
        else:
            ranges.append([s["start"], s["end"]])
    return [f"{a:%H:%M}-{b:%H:%M}" if b.date() == a.date() else f"{a:%H:%M}-24:00" for a, b in ranges]


def add_body(space: dict, start: datetime, checksum: str) -> list[tuple[str, str]]:
    """Pick a start slot (what clicking the grid sends). LibCal answers with the end times allowed."""
    day = start.date()
    return [("add[eid]", str(space["space_id"])), ("add[gid]", str(space["gid"])),
            ("add[lid]", str(space["lid"])), ("add[start]", fmt(start)), ("add[checksum]", checksum),
            ("lid", str(space["lid"])), ("gid", str(space["gid"])),
            ("start", day.isoformat()), ("end", (day + timedelta(days=1)).isoformat())]


def _pending(b: dict) -> dict:
    """A pending booking as the page sends it back (minutes, no seconds)."""
    return {"id": b["id"], "eid": b["eid"], "seat_id": b["seat_id"], "gid": b["gid"], "lid": b["lid"],
            "start": b["start"][:16], "end": b["end"][:16], "checksum": b["checksum"]}


def _bookings_fields(b: dict) -> list[tuple[str, str]]:
    return [(f"bookings[0][{k}]", str(v)) for k, v in _pending(b).items()]


def update_body(b: dict, end: datetime) -> list[tuple[str, str]]:
    """Change a pending booking's end (what picking another end time sends)."""
    i = [datetime.fromisoformat(o) for o in b["options"]].index(end)
    day = datetime.fromisoformat(b["start"]).date()
    return [("update[id]", str(b["id"])), ("update[checksum]", b["optionChecksums"][i]),
            ("update[end]", b["options"][i]), ("lid", str(b["lid"])), ("gid", str(b["gid"])),
            ("start", day.isoformat()), ("end", (day + timedelta(days=1)).isoformat()),
            *_bookings_fields(b)]


def times_body(b: dict, method: int) -> list[tuple[str, str]]:
    """Submit the chosen times. LibCal holds them for a few minutes and returns the booking form."""
    return [("patron", ""), ("patronHash", ""), ("returnUrl", f"/space/{b['eid']}"),
            *_bookings_fields(b), ("method", str(method))]


def book_fields(session: str, values: dict[str, str | list[str]], b: dict,
                method: int) -> list[tuple[str, str]]:
    """The booking form's multipart fields, in the order the page sends them."""
    out = [("session", session)]
    for k, v in values.items():
        out += [(k, x) for x in v] if isinstance(v, list) else [(k, v)]
    p = _pending(b)
    return out + [("bookings", json.dumps([p], separators=(",", ":"))), ("returnUrl", f"/space/{b['eid']}"),
                  ("pickupHolds", ""), ("method", str(method))]


class _Form(HTMLParser):
    """The fields of the booking form (#s-lc-eq-bform): name, label, required, options."""

    def __init__(self):
        super().__init__()
        self.fields: dict[str, dict] = {}
        self.labels: dict[str, str] = {}
        self.hidden: dict[str, str] = {}
        self._in_form = self._label_for = self._option = None
        self._select = None
        self._text = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "s-lc-eq-bform":
            self._in_form = True
        if not self._in_form:
            return
        if tag == "label":
            self._label_for, self._text = a.get("for"), ""
        elif tag in ("input", "select", "textarea") and a.get("name"):
            if a.get("type") == "hidden":
                self.hidden[a["name"]] = a.get("value", "")
                return
            if a.get("type") == "file":
                return
            f = self.fields.setdefault(a["name"], {"name": a["name"], "id": a.get("id"),
                                                   "required": False, "options": None,
                                                   "multi": tag == "select" and "multiple" in a})
            f["required"] |= "required" in a or a.get("aria-required") == "true"
            if a.get("type") in ("radio", "checkbox"):
                f["options"] = (f["options"] or []) + [a.get("value", "")]
                f["multi"] = a.get("type") == "checkbox"
            if tag == "select":
                self._select, f["options"] = f, []
        elif tag == "option" and self._select is not None:
            self._option, self._text = a.get("value"), ""

    def handle_data(self, data):
        self._text += data

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False
        elif tag == "label" and self._label_for:
            self.labels[self._label_for] = " ".join(self._text.replace("*", "").split())
            self._label_for = None
        elif tag == "option" and self._option is not None:
            if self._option:
                self._select["options"].append(self._option)
            self._option = None
        elif tag == "select":
            self._select = None


def parse_form(page: str) -> dict:
    """The booking form LibCal returns once times are held: session id, fields, email domains."""
    p = _Form()
    p.feed(page)
    if "session" not in p.hidden:
        raise ValueError("no booking form session")
    fields = [{**f, "label": p.labels.get(f["id"]) or f["name"]} for f in p.fields.values()]
    return {"session": p.hidden["session"], "fields": fields,
            "email_domains": re.findall(r"email\.search\(/(@[^/]+)/i\)", page)}


def _text_of(page: str, css: str) -> str | None:
    m = re.search(r'class="[^"]*\b%s\b[^"]*"[^>]*>(.*?)</(?:h\d|p|div)>' % css, page, re.S)
    return unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else None


def parse_booked(resp: dict) -> dict:
    """bookId plus what the confirmation page says, e.g. 'Booking Confirmed'."""
    page = resp["html"]
    msgs = [" ".join(t.split()) for t in (_text_of(page, "s-lc-eq-success-title"),
                                          _text_of(page, "s-lc-eq-booking-status-msg")) if t]
    return {"booking_id": resp["bookId"], "messages": msgs}
