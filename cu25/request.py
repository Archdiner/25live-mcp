"""Check a Request against the live form. Pure: no network.

`check` compares a request with the form (fields, questions, options, rules,
date limits) and says exactly what's still missing, in the form's own words,
so the agent can ask the user instead of guessing.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property

from .models import CheckResult, MissingField, MissingQuestion, Request
from .rules import Context, Engine

# How each live form field (wizard/config.json item_name) is filled in a Request.
# A required field missing from this map makes check fail loudly, so a form
# change is never silently skipped.
FIELD_KEYS = {
    "name": ["name"],
    "type": ["type_id"],
    "description_html": ["description"],
    "sponsor": ["org_id"],
    "headcount_expected": ["headcount"],
    "datetime": ["start", "end"],
    "space": ["space_id"],
    "file_attach": [],
    "ev_custom_attr": [],  # questions: handled by the rule engine
    "resource": [],        # added by rules
    "affirmation": ["agree"],
}
# The name field's own help text: "Avoid these characters — they'll cause a save error".
BAD_NAME_CHARS = set("!<>%~@\\^")
QUESTION_TYPES = {"B", "S", "X", "R"}


def plain(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


@dataclass
class Schema:
    """Everything about the form, fetched live from 25Live."""
    fields: list[dict]                 # wizard/config.json -> config.item
    form_rules: list[dict]             # rule/trees.json?category_id=form
    validation_rules: list[dict]       # rule/trees.json?category_id=formValidation
    questions: dict[int, dict]         # customattributes.json?itemTypeId=1
    options: dict[int, list[str]]      # custom/attribute/options.json
    root_questions: list[int]          # customattributes.json?itemTypeId=11&event_type_id=X
    event_types: dict[int, str] = field(default_factory=dict)  # types this user may create
    min_date: date | None = None       # usergroup/wizardsecurity.json
    max_date: date | None = None

    @cached_property
    def engine(self) -> Engine:
        return Engine(self.form_rules, self.validation_rules)

    def answer_type(self, qid: int) -> str:
        d = self.questions.get(qid, {})
        t = d.get("itemTypeId")
        if t == "B": return "yes_no"
        if t in ("S", "X"):
            if self.options.get(qid):
                return "choices" if d.get("multi_val") else "choice"
            return "text"
        return "unsupported"

    def missing_question(self, qid: int) -> MissingQuestion:
        return MissingQuestion(
            id=qid, question=self.questions.get(qid, {}).get("itemName", f"question {qid}"),
            answer_type=self.answer_type(qid), options=self.options.get(qid) or None)


def canonical_answer(value, kind: str, options: list[str] | None):
    """Validate one answer. Returns (canonical value, error or None)."""
    if kind == "yes_no":
        if isinstance(value, bool): return value, None
        s = str(value).strip().lower()
        if s in ("t", "true", "yes", "y"): return True, None
        if s in ("f", "false", "no", "n"): return False, None
        return None, "expects true or false"
    if kind == "text":
        if isinstance(value, str) and value.strip(): return value.strip(), None
        return None, "expects text"
    if kind in ("choice", "choices"):
        by_lower = {o.lower(): o for o in options or []}
        vals = value if isinstance(value, list) else [value]
        if kind == "choice" and len(vals) != 1:
            return None, "expects exactly one option"
        bad = [v for v in vals if not isinstance(v, str) or v.strip().lower() not in by_lower]
        if bad or not vals:
            return None, f"{bad or vals} isn't one of the allowed options"
        canon = [by_lower[v.strip().lower()] for v in vals]
        return (canon if kind == "choices" else canon[0]), None
    return None, "is a question type cu25 can't answer yet (use the web form)"


def display_answer(value, kind: str) -> str:
    """A canonical answer as a person would read it."""
    if kind == "yes_no": return "yes" if value else "no"
    if kind == "choices": return "; ".join(value)
    return str(value)


def encode_answer(value, kind: str) -> str:
    """A canonical answer as 25Live stores it."""
    if kind == "yes_no": return "T" if value else "F"
    if kind == "choices": return json.dumps([{"item": v} for v in value], separators=(",", ":"),
                                            ensure_ascii=False)
    return str(value)


def evaluate(req: Request, schema: Schema, ctx: Context):
    """Run the rules. Returns (engine result, {qid: canonical answer}, problems)."""
    canon, problems = {}, []
    for qid, v in req.answers.items():
        if qid not in schema.questions:
            problems.append(f"Question {qid} doesn't exist in this form.")
            continue
        value, err = canonical_answer(v, schema.answer_type(qid), schema.options.get(qid))
        if err:
            problems.append(f"Question {qid} ({schema.questions[qid]['itemName']}) {err}.")
        else:
            canon[qid] = value
    result = schema.engine.evaluate(canon, ctx, schema.root_questions)
    return result, canon, problems


def check(req: Request, schema: Schema, ctx: Context) -> CheckResult:
    """What's missing or wrong. ready=True means submit will be accepted."""
    missing, problems = [], []

    for f in sorted(schema.fields, key=lambda f: f["sort_order"]):
        if not (f.get("required") and f.get("visible")):
            continue
        name = f["item_name"]
        if name not in FIELD_KEYS:
            problems.append(f"The form has a required field cu25 doesn't know yet: "
                            f"'{name}' ({plain(f.get('item_display'))}). Use the web form.")
            continue
        for key in FIELD_KEYS[name]:
            if getattr(req, key) in (None, "", False):
                opts = ([{"type_id": k, "name": v} for k, v in schema.event_types.items()]
                        if key == "type_id" else None)
                missing.append(MissingField(field=key, question=plain(f.get("item_display")),
                                            help=plain(f.get("item_message")), options=opts))

    result, canon, answer_problems = evaluate(req, schema, ctx)
    problems += answer_problems
    missing += [schema.missing_question(q) for q in result["unanswered"] if q not in canon
                and req.answers.get(q) is None]
    ignored = sorted(q for q in canon if q not in result["questions"])

    if req.name and BAD_NAME_CHARS & set(req.name):
        problems.append(f"The name can't contain {' '.join(sorted(BAD_NAME_CHARS & set(req.name)))} "
                        "(25Live rejects them).")
    if req.type_id and schema.event_types and req.type_id not in schema.event_types:
        problems.append(f"Event type {req.type_id} isn't one you can request. See `cu25 types`.")
    if req.start and req.end and req.end <= req.start:
        problems.append("End time must be after start time.")
    if req.start and schema.min_date and req.start.date() < schema.min_date:
        problems.append(f"25Live's earliest allowed date right now is {schema.min_date}.")
    if req.start and schema.max_date and req.start.date() > schema.max_date:
        problems.append(f"25Live's latest allowed date right now is {schema.max_date}.")
    if result["requirements"] or result["resources"]:
        problems.append("These answers route the request to extra approvers, which cu25 can't "
                        "attach yet. Submit this one in the 25Live web form.")
    unsupported = schema.engine.unsupported_rules()
    warnings = [plain(a) for a in result["alerts"]]
    if unsupported:
        problems.append("The form has rules cu25 can't evaluate, so it can't be sure what's "
                        "required: " + "; ".join(unsupported))

    return CheckResult(
        ready=not (missing or problems or result["blocked"]),
        missing=missing, problems=problems,
        blocked=[plain(b) for b in result["blocked"]], warnings=warnings,
        approvals_needed=sorted({*result["requirements"].values(), *result["resources"].values()}),
        ignored_answers=ignored,
    )
