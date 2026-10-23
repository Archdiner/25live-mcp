"""Library study spaces (LibCal) against the fake, through the same Library the CLI and MCP use."""
import json

import fake25live
from fake25live import load

from cu25.models import LibraryRequest

REQ = {"space_id": 110724, "start": "2026-10-26T17:00", "end": "2026-10-26T19:00",
       "first_name": "Test", "last_name": "Student", "email": "netid@cornell.edu"}


def booking(**kw) -> LibraryRequest:
    return LibraryRequest.model_validate({**REQ, **kw})


def posts(calls):
    return [(p, form) for m, p, form in calls if m == "POST"]


def test_preview_then_confirm_reproduces_the_real_browser_booking(library):
    lib, calls = library
    prev = lib.book(booking())
    assert prev.status == "preview" and not prev.booked and prev.confirm
    assert prev.preview.space == "136 Table A" and prev.preview.end == "2026-10-26 19:00"
    assert [p for p, _ in posts(calls)] == ["/spaces/availability/grid", "/spaces/availability/booking/add"]

    calls.clear()
    out = lib.book(booking(), confirm=prev.confirm)
    assert (out.status, out.booking_id) == ("booked", "cab39b94d155")
    assert out.messages[0] == "Booking Confirmed"
    sent = dict(posts(calls))
    assert list(sent) == ["/spaces/availability/grid", "/spaces/availability/booking/add",
                          "/ajax/space/times", "/ajax/space/book"]  # the browser's order
    real = load("lib_bodies.json")
    assert [list(x) for x in sent["/spaces/availability/booking/add"]] == real["add"]
    assert [list(x) for x in sent["/ajax/space/times"]] == real["times"]
    assert [list(x) for x in sent["/ajax/space/book"]] == real["book"]


def test_a_shorter_booking_picks_the_end_time_like_the_page(library):
    lib, calls = library
    req = booking(end="2026-10-26T18:00")
    out = lib.book(req, confirm=lib.book(req).confirm)
    assert out.status == "booked"
    update = [dict(f) for p, f in posts(calls) if dict(f).get("update[end]")][-1]
    add = load("lib_add.json")["bookings"][0]
    assert update["update[end]"] == "2026-10-26 18:00:00"
    assert update["update[checksum]"] == add["optionChecksums"][3]
    book = dict(posts(calls)[-1][1])
    assert json.loads(book["bookings"])[0]["end"] == "2026-10-26 18:00"


def test_spaces_come_from_every_library_and_category_and_are_cached(library):
    lib, calls = library
    spaces = lib.catalog()
    assert {s["library"] for s in spaces} == {"Olin & Uris Libraries", "Catherwood Library"}
    assert len(spaces) == 18
    assert {"space_id": 110724, "lid": 527, "gid": 1016, "name": "136 Table A", "capacity": 6,
            "category": "Catherwood Room Reservations", "library": "Catherwood Library"} in spaces
    calls.clear()
    lib.catalog()
    assert calls == []


def test_spaces_at_a_time_free_first_with_the_days_free_times(library):
    from datetime import date, datetime
    lib, _ = library
    out = lib.spaces(date(2026, 10, 26), datetime(2026, 10, 26, 17), datetime(2026, 10, 26, 19),
                     name="catherwood", limit=0)
    by_id = {s.space_id: s for s in out.spaces}
    assert out.total == 9 and by_id[110724].free and not by_id[222370].free
    assert by_id[222370].free_times == ["06:00-08:00", "14:45-16:45", "18:45-23:00"]
    assert out.spaces[0].free
    day = lib.spaces(date(2026, 10, 26), people=5)
    assert all(s.free is None and s.capacity >= 5 for s in day.spaces)


def test_a_taken_time_is_a_problem_with_the_free_times_and_nothing_is_held(library):
    lib, calls = library
    out = lib.book(booking(space_id=222370, end="2026-10-26T18:00"))
    assert out.status == "not_ready" and "14:45-16:45, 18:45-23:00" in out.problems[0]
    assert [p for p, _ in posts(calls)] == ["/spaces/availability/grid"]


def test_longer_than_the_library_allows_says_the_latest_end(library):
    lib, _ = library
    out = lib.book(booking(end="2026-10-26T20:00"))
    assert out.status == "not_ready" and out.problems[0].endswith("until 17:15, 17:30, 17:45, 18:00, "
                                                                  "18:15, 18:30, 18:45, 19:00 (the library's limit).")


def test_blank_booking_asks_for_everything(library):
    lib, calls = library
    out = lib.book(LibraryRequest())
    assert [m.field for m in out.missing] == ["space_id", "start", "end", "first_name", "last_name", "email"]
    assert calls == [] or all(m == "GET" for m, _, _ in calls)


def test_past_time_and_unknown_space(library):
    lib, _ = library
    out = lib.book(booking(space_id=1, start="2026-10-20T17:00", end="2026-10-20T18:00"))
    assert out.status == "not_ready" and len(out.problems) == 2


def test_stale_confirm_code_books_nothing(library):
    lib, calls = library
    out = lib.book(booking(), confirm="stale")
    assert out.status == "changed" and out.confirm
    assert "/ajax/space/times" not in [p for p, _ in posts(calls)]


def test_the_forms_own_rules_and_questions_apply_before_booking(library, monkeypatch):
    lib, calls = library
    real = fake25live.load

    def with_question(name):
        out = real(name)
        if name == "lib_times.json":
            out["html"] = out["html"].replace('<div class="form-group " id="btn-form-submit_container">', (
                '<label for="q123">Purpose of booking *</label><select name="q123" id="q123" required>'
                '<option value="">Select</option><option value="Study">Study</option>'
                '<option value="Meeting">Meeting</option></select>'
                '<div class="form-group " id="btn-form-submit_container">'))
        return out
    monkeypatch.setattr(fake25live, "load", with_question)

    out = lib.book(booking(), confirm=lib.book(booking()).confirm)
    assert out.status == "not_ready" and not out.booked
    assert out.missing[0].model_dump() == {"kind": "field", "field": "answers.q123", "question": "Purpose of booking",
                                           "help": "", "options": [{"value": "Study"}, {"value": "Meeting"}]}
    req = booking(answers={"q123": "Study"})
    out = lib.book(req, confirm=lib.book(req).confirm)
    assert out.status == "booked"
    assert ("q123", "Study") in posts(calls)[-1][1]

    req = booking(email="someone@gmail.com")
    out = lib.book(req, confirm=lib.book(req).confirm)
    assert out.status == "not_ready" and "@cornell.edu" in out.problems[0]


def test_the_librarys_refusal_is_passed_on(library, monkeypatch):
    lib, _ = library
    real = fake25live.libcal_route
    monkeypatch.setattr(fake25live, "libcal_route", lambda m, path, *a: (
        (400, "text/plain", "Invalid Checksum.") if path.endswith("/add") else real(m, path, *a)))
    out = lib.book(booking())
    assert out.status == "not_ready" and out.problems == ["The library says: Invalid Checksum."]
