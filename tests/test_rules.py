"""The rule engine must reproduce the real form's behaviour from live rule data."""
from datetime import datetime

from fake25live import load

from cu25.rules import Context, Engine, unsupported


def setup():
    eng = Engine(load("rules_form.json")["rule"], load("rules_validation.json")["rule"])
    roots = [i["itemId"] for i in load("attribute_roots_type16.json")["searchCriteria"]["item"]]
    real = load("submit_body.json")["events"]["event"]["custom_attribute"]
    answers = {a["attribute_id"]: a["attribute_value"] for a in real}
    answers[327] = ["None of the Above"]
    ctx = Context(event_type_id=16, space_ids=[165], headcount=20, organization_id=12499,
                  org_search_ids=[367100], start=datetime(2026, 10, 25, 14),
                  end=datetime(2026, 10, 25, 15), today=datetime(2026, 10, 23))
    return eng, roots, answers, ctx


def test_engine_derives_exactly_the_questions_the_real_form_asked():
    eng, roots, answers, ctx = setup()
    r = eng.evaluate(answers, ctx, roots)
    assert sorted(r["questions"]) == sorted(answers)
    assert r["unanswered"] == [] and r["blocked"] == []
    assert r["requirements"] == {} and r["resources"] == {}


def test_first_questions_depend_on_context():
    eng, roots, _, ctx = setup()
    # campus is the root question; a student-org sponsor adds the advisor questions
    assert eng.evaluate({}, ctx, roots)["unanswered"] == [249, 324, 325]
    ctx.org_search_ids = []  # not a student org
    assert eng.evaluate({}, ctx, roots)["unanswered"] == [249]


def test_late_minors_request_is_blocked():
    eng, roots, answers, ctx = setup()
    answers[327] = ["Attendees Are Under 18 yrs Old and NOT Students at Cornell"]
    r = eng.evaluate(answers, ctx, roots)
    assert any("56 days" in b for b in r["blocked"])


def test_minors_far_enough_ahead_is_not_blocked():
    eng, roots, answers, ctx = setup()
    answers[327] = ["Attendees Are Under 18 yrs Old and NOT Students at Cornell"]
    ctx.today = datetime(2026, 8, 1)
    assert eng.evaluate(answers, ctx, roots)["blocked"] == []


def test_late_night_event_adds_approvers_even_if_only_the_end_is_late():
    eng, roots, answers, ctx = setup()
    ctx.start, ctx.end = datetime(2026, 10, 25, 22), datetime(2026, 10, 25, 23, 30)
    r = eng.evaluate(answers, ctx, roots)
    assert "UET-CUPD-Approver" in r["resources"].values()


def test_not_in_saved_search_is_true_for_an_org_in_no_search():
    rule = {"rule_name": "x", "active": 1, "item": {"type": "bool", "operator": "and", "actions": [
        {"action": "addCustAtrb", "targets": [{"itemId": 999}]}], "children": {"item": [
            {"type": "comp", "sourceItem": {"itemTypeId": 205}, "operator": "not in",
             "val_obj": [{"value": 367100}]}]}}}
    eng = Engine([rule])
    assert 999 in eng.evaluate({}, Context(org_search_ids=[]), [])["questions"]
    assert 999 not in eng.evaluate({}, Context(org_search_ids=[367100]), [])["questions"]


def test_unanswered_question_never_matches_not_in():
    rule = {"rule_name": "x", "active": 1, "item": {"type": "bool", "operator": "and", "actions": [
        {"action": "addCustAtrb", "targets": [{"itemId": 999}]}], "children": {"item": [
            {"type": "comp", "sourceItem": {"itemTypeId": 11, "itemId": 1}, "operator": "not in",
             "val_obj": [{"value": "a"}]}]}}}
    eng = Engine([rule])
    assert 999 not in eng.evaluate({}, Context(), [1])["questions"]
    assert 999 in eng.evaluate({1: "b"}, Context(), [1])["questions"]


def test_answers_to_questions_the_form_is_not_asking_fire_nothing():
    eng, roots, answers, ctx = setup()
    answers[177] = "2. Delivery/Takeout from restaurant/business (Pizza, Chinese, etc.)"  # food is "no"
    r = eng.evaluate(answers, ctx, roots)
    assert 177 not in r["questions"] and r["resources"] == {} and r["requirements"] == {}


def test_every_live_rule_is_supported():
    eng = Engine(load("rules_form.json")["rule"], load("rules_validation.json")["rule"])
    assert eng.unsupported_rules() == []


def test_unknown_rule_vocabulary_is_named():
    rule = {"rule_name": "new", "active": 1, "item": {"type": "bool", "operator": "and",
            "actions": [{"action": "teleport"}], "children": {"item": [
                {"type": "comp", "sourceItem": {"itemTypeId": 777}, "operator": "~", "val_obj": []}]}}}
    assert unsupported(rule) == ["source type 777", "operator '~'", "action teleport"]
    assert Engine([rule]).unsupported_rules() == ["new: source type 777, operator '~', action teleport"]
