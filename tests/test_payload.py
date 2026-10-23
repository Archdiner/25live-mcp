"""Checks the builders against what the real 25Live web app sent (sanitized HAR)."""
import json
from datetime import datetime
from pathlib import Path

from cu25 import payload as P

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text())


def normalize(body):
    ev = body["events"]["event"]
    ev["custom_attribute"] = sorted(ev["custom_attribute"], key=lambda a: a["attribute_id"])
    ev["role"] = sorted(ev["role"], key=lambda r: r["role_id"])
    return body


def test_submit_body_matches_real_browser_request():
    tpl = P.template_from_detail(load("event_detail.json"))
    built = P.submit_body(
        tpl, event_id=561543, profile_id=636747, reservation_id=9482714,
        start=datetime(2026, 10, 25, 14), end=datetime(2026, 10, 25, 15),
    )
    assert normalize(built) == normalize(load("submit_body.json"))


def test_clone_to_new_date_changes_only_times():
    tpl = P.template_from_detail(load("event_detail.json"))
    body = P.submit_body(
        tpl, event_id=1, profile_id=2, reservation_id=3,
        start=datetime(2026, 10, 29, 17), end=datetime(2026, 10, 29, 18),
    )
    ev = body["events"]["event"]
    assert ev["profile"][0]["profile_code"] == "W1 TH 20261029T235900"
    assert ev["start_date"] == "2026-10-29T17:00:00"
    assert ev["profile"][0]["reservation"][0]["space_reservation"][0]["space_id"] == 165
    assert ev["organization"][0]["organization_id"] == 12499


def test_availability_body_shape():
    real = load("space_avail_body.json")["space_availability"]
    built = P.availability_body(
        [s["space_id"] for s in real["requirements"]["space"]],
        datetime(2026, 10, 25, 14), datetime(2026, 10, 25, 15),
    )["space_availability"]
    assert built["requirements"] == real["requirements"]
    assert built["dates"] == real["dates"]


def test_parse_spaces_and_availability():
    spaces = P.parse_spaces(load("spaces_search.json"))
    assert any(s["code"] == "STMG01" and s["layout_id"] == 23 for s in spaces)
    avail = P.parse_availability(load("space_avail.json"))
    assert avail[976]["free"] is True
    assert avail[164]["free"] is False  # STM206 had a 2-4pm conflict


def test_parse_scheduler_and_submit():
    assert P.parse_scheduler(load("space_detail.json"))[0] == 690
    out = P.parse_submit(load("submit_response.json"))
    assert out["reference"] == "2026-ACWQJD"
    assert out["state"] == "Tentative"


def test_parse_default_layout():
    assert P.parse_default_layout(load("space_detail.json")) == 23
