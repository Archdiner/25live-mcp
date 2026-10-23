"""The full flow against the fake 25Live, through the same Client the CLI and MCP use."""
from datetime import datetime

import httpx
import pytest
from conftest import edit
from fake25live import load

from cu25 import client as C
from cu25.models import MissingField, MissingQuestion, Request


def normalize(body):
    ev = body["events"]["event"]
    ev["custom_attribute"] = sorted(ev["custom_attribute"], key=lambda a: a["attribute_id"])
    ev["role"] = sorted(ev["role"], key=lambda r: r["role_id"])
    return body


def test_copy_check_preview_confirm_reproduces_the_real_browser_submission(fake, real_request):
    c, calls = fake
    assert c.check(real_request).ready is True
    preview = c.submit(real_request)
    assert preview.status == "preview" and not preview.submitted and preview.confirm
    assert not any(p in ("event.json", "rose/event.json") for _, p, _ in calls)

    out = c.submit(real_request, confirm=preview.confirm)
    assert (out.status, out.event_id, out.reference, out.state) == \
        ("submitted", 561543, "2026-ACWQJD", "Tentative")
    assert out.messages == ["Location STMG01 requested for Sun Oct 25 2026."]
    writes = [p for m, p, _ in calls if m == "PUT" or p.startswith(("sequence", "micro/sequence"))]
    assert writes == ["sequence/id.json", "sequence/id.json", "event.json",
                      "micro/sequence/generate.json", "rose/event.json"]
    sent = next(b for m, p, b in calls if p == "rose/event.json")
    real = load("submit_body.json")
    # the scheduler comes from the room record (the fixture is a different room)
    sent["events"]["event"]["role"] = real["events"]["event"]["role"]
    assert normalize(sent) == normalize(real)


def test_preview_shows_what_will_be_sent_in_plain_words(fake, real_request):
    c, _ = fake
    p = c.submit(real_request).preview
    assert p.name == "Generative AI at Cornell - NME" and p.type == "Meeting / Info Session"
    assert p.start == "2026-10-25T14:00:00" and p.headcount == 20
    assert p.answers["Which Campus?"] == "Ithaca Campus"
    assert p.answers["Is your event having food of any kind?"] == "no"


def test_wrong_or_stale_confirm_code_sends_nothing(fake, real_request):
    c, calls = fake
    code = c.submit(real_request).confirm
    changed = edit(real_request, headcount=25)
    out = c.submit(changed, confirm=code)
    assert out.status == "changed" and out.confirm != code
    assert not any(p in ("event.json", "rose/event.json") for _, p, _ in calls)


def test_blank_request_asks_for_everything_starting_with_the_basics(fake):
    c, _ = fake
    out = c.check(Request())
    assert out.ready is False
    fields = [m for m in out.missing if isinstance(m, MissingField)]
    assert [m.field for m in fields] == ["name", "type_id", "org_id", "headcount", "start", "end",
                                         "space_id", "agree"]
    types = next(m for m in fields if m.field == "type_id").options
    assert {"type_id": 16, "name": "Meeting / Info Session"} in types
    assert "policies" in next(m for m in fields if m.field == "agree").help


def test_partial_request_lists_questions_in_the_forms_words(fake):
    c, _ = fake
    out = c.check(Request(type_id=16, org_id=12499))
    qs = {m.id: m for m in out.missing if isinstance(m, MissingQuestion)}
    assert qs[249].question == "Which Campus?" and qs[249].answer_type == "choice"
    assert "Ithaca Campus" in qs[249].options
    assert {324, 325} <= set(qs)  # student org -> advisor questions
    assert qs[325].answer_type == "yes_no"


def test_food_yes_asks_follow_ups_with_options(fake, real_request):
    c, _ = fake
    out = c.check(edit(real_request, answers={176: True}))
    follow = {m.id: m for m in out.missing if isinstance(m, MissingQuestion)}
    assert set(follow) == {177, 271}
    assert any("Delivery/Takeout" in o for o in follow[177].options)


def test_answers_that_trigger_approvals_are_refused_not_dropped(fake, real_request):
    c, calls = fake
    req = edit(real_request, answers={
        176: True, 177: "2. Delivery/Takeout from restaurant/business (Pizza, Chinese, etc.)",
        271: "pizza"}, headcount=30)
    out = c.check(req)
    assert out.approvals_needed == ["UET-DIN-Approver", "UET-ERF-Approver"]
    assert out.ready is False and any("approvers" in p for p in out.problems)
    assert c.submit(req, confirm="x").status == "not_ready"
    assert not any(p == "rose/event.json" for _, p, _ in calls)


def test_invalid_option_and_wrong_type_are_reported(fake, real_request):
    c, _ = fake
    out = c.check(edit(real_request, answers={249: "Mars Campus", 176: "maybe"}))
    assert out.ready is False
    assert any("Mars Campus" in p for p in out.problems)
    assert any("176" in p and "true or false" in p for p in out.problems)


def test_options_are_matched_case_insensitively_and_stored_canonically(fake, real_request):
    c, _ = fake
    req = edit(real_request, answers={249: "ithaca campus", 327: ["none of the above"]})
    assert c.check(req).ready
    p = c.submit(req).preview
    assert p.answers["Which Campus?"] == "Ithaca Campus"
    assert p.answers[next(k for k in p.answers if k.startswith("Which of these"))] == "None of the Above"


def test_answers_to_questions_not_asked_are_reported_and_not_sent(fake, real_request):
    c, _ = fake
    req = edit(real_request, answers={177: "7. Other"})  # food follow-up, but food is "no"
    out = c.check(req)
    assert out.ready and out.ignored_answers == [177]
    assert not any("foodservice" in k for k in c.submit(req).preview.answers)


def test_unknown_question_id_is_reported(fake, real_request):
    c, _ = fake
    assert any("424242" in p for p in c.check(edit(real_request, answers={424242: True})).problems)


def test_names_with_characters_25live_rejects(fake, real_request):
    c, _ = fake
    out = c.check(edit(real_request, name="Pizza night!"))
    assert not out.ready and any("!" in p for p in out.problems)


def test_live_date_limits_from_25live(fake, real_request):
    c, _ = fake
    early = c.check(edit(real_request, start="2026-10-23T14:00", end="2026-10-23T15:00"))
    assert any("earliest allowed date" in p for p in early.problems)
    late = c.check(edit(real_request, start="2028-01-10T14:00", end="2028-01-10T15:00"))
    assert any("latest allowed date" in p for p in late.problems)


def test_end_before_start(fake, real_request):
    c, _ = fake
    out = c.check(edit(real_request, start="2026-10-25T15:00", end="2026-10-25T14:00"))
    assert any("End time" in p for p in out.problems)


def test_layout_must_belong_to_the_room_and_headcount_vs_capacity(fake, real_request):
    c, _ = fake
    out = c.check(edit(real_request, layout_id=999, headcount=500))
    assert any("Layout 999" in p for p in out.problems)
    assert any("500 people" in w for w in out.warnings)


def test_taken_room_is_refused(fake, real_request):
    c, _ = fake
    # 164 (STM206) has a 2-4pm conflict in the captured availability
    out = c.check(edit(real_request, space_id=164))
    assert not out.ready and any("isn't free" in p for p in out.problems)


def test_not_ready_request_is_never_submitted(fake, real_request):
    c, calls = fake
    out = c.submit(edit(real_request, agree=False), confirm="anything")
    assert out.status == "not_ready" and out.check.missing[0].field == "agree"
    assert not any(p in ("event.json", "rose/event.json") for _, p, _ in calls)


def test_rooms_lists_free_first(fake):
    c, _ = fake
    out = c.rooms(datetime(2026, 10, 25, 14), datetime(2026, 10, 25, 15), name="stimson")
    assert out.rooms[0].free and not out.rooms[-1].free
    assert {r.code: r.fully_scheduled for r in out.rooms}["STMG01"] is True
    assert {r.code: r.fully_scheduled for r in out.rooms}["STM102**"] is False


def test_rooms_caps_the_list_but_reports_the_total(fake):
    c, _ = fake
    out = c.rooms(datetime(2026, 10, 25, 14), datetime(2026, 10, 25, 15), name="stimson", limit=1)
    assert len(out.rooms) == 1 and out.total > 1


def test_room_search_reads_past_the_first_page(home):
    pages = []

    def handle(req):
        page, size = int(req.url.params["page"]), int(req.url.params["page_size"])
        pages.append(page)
        return httpx.Response(200, json={"spaces": {"space": [
            {"space_id": page * 100 + i, "space_name": f"S{page}{i}", "formal_name": f"Room {page}-{i}"}
            for i in range(size if page == 1 else 1)]}})

    c = C.Client(home=home)
    c.http = httpx.Client(transport=httpx.MockTransport(handle))
    found = c.search_spaces(datetime(2026, 10, 29, 18), datetime(2026, 10, 29, 19), page_size=2)
    assert pages == [1, 2] and len(found) == 3


def test_form_definition_is_fetched_once_then_cached(fake, home, real_request):
    c, calls = fake
    c.check(real_request)
    first = sum(p == "rule/trees.json" for _, p, _ in calls)
    c2 = C.Client(home=home)
    c2.http = c.http
    c2.check(real_request)
    assert first == 2 and sum(p == "rule/trees.json" for _, p, _ in calls) == 2
    c2.submit(real_request, confirm="x")  # confirming always refetches the live form
    assert sum(p == "rule/trees.json" for _, p, _ in calls) == 4


def test_unexpected_25live_shape_is_an_upstream_error(home):
    c = C.Client(home=home)
    c.http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"odd": 1})))
    with pytest.raises(C.UpstreamChanged, match="unexpected shape"):
        c.status(1)


def test_expired_session_is_clear(home):
    c = C.Client(home=home)
    c.http = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(302, headers={"location": "https://shibidp.cit.cornell.edu"})))
    with pytest.raises(C.SessionExpired, match="cu25 login"):
        c.whoami()


def test_signed_out_login_json_is_expired_not_a_crash(home):
    c = C.Client(home=home)
    c.http = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=load("login_logged_out.json"))))
    with pytest.raises(C.SessionExpired, match="cu25 login"):
        c.whoami()


def test_record_mode_saves_every_call(fake, real_request, tmp_path, monkeypatch):
    c, _ = fake
    c._record = tmp_path / "rec"
    c.check(real_request)
    saved = sorted((tmp_path / "rec").glob("*.json"))
    assert saved and any("wizard_config" in f.name for f in saved)


def test_doctor_understands_the_captured_form(fake):
    c, _ = fake
    d = c.doctor()
    assert d.ok and d.session.netid == "netid"
    assert d.counts["questions"] == 149 and d.counts["form_rules"] == 144
