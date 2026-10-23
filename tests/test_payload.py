"""Payload details the end-to-end tests don't pin down on their own."""
from datetime import datetime

from fake25live import load

from cu25 import payload as P


def test_profile_code_names_the_weekday_of_the_start():
    assert P.profile_code(datetime(2026, 10, 25, 14)) == "W1 SU 20261025T235900"  # the real one
    assert P.profile_code(datetime(2026, 10, 29, 17)) == "W1 TH 20261029T235900"


def test_availability_body_matches_the_browser():
    real = load("space_avail_body.json")["space_availability"]
    built = P.availability_body([s["space_id"] for s in real["requirements"]["space"]],
                                datetime(2026, 10, 25, 14), datetime(2026, 10, 25, 15))
    assert built["space_availability"] == {k: real[k] for k in ("requirements", "dates")}


def test_availability_reads_one_conflict_or_several():
    def dates(conflict):
        return {"spaces": {"space": [{"space_id": 1, "has_conflicts": "T",
                                      "dates": {"can_schedule": "T", "conflict": conflict}}]}}

    one = dates({"conflict_end": "2026-10-29T18:30:00-04:00"})
    many = dates([{"conflict_end": "2026-10-29T18:30:00-04:00"},
                  {"conflict_end": "2026-10-29T20:00:00-04:00"}])
    assert P.parse_availability(one)[1]["conflict_until"] == "2026-10-29T18:30:00-04:00"
    assert P.parse_availability(many)[1]["conflict_until"] == "2026-10-29T20:00:00-04:00"


def test_description_is_escaped_into_paragraphs():
    assert P.description_html("") == ""
    assert P.description_html("Pizza <3 & games\nline two\n\nSecond") == \
        "<p>Pizza &lt;3 &amp; games<br>line two</p><p>Second</p>"


def test_copy_turns_stored_answers_back_into_request_answers():
    req = P.request_from_detail(load("event_detail.json"))
    assert req["answers"]["327"] == ["None of the Above"] and req["answers"]["176"] is False
    assert req["start"] is None and req["agree"] is False
