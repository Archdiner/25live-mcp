"""Evaluate 25Live's form rules (rule/trees.json) offline.

Nothing here is Cornell-specific: the rules, question ids and options all come
from 25Live at runtime. Given the answers so far, `evaluate` returns which
questions are now active (and which are unanswered), plus the approvals,
resources, warnings and save blockers the rules trigger.

Rule nodes are {type:"bool", operator:"and"|"or", children:{item:[...]}} or
{type:"comp", sourceItem:{itemTypeId, itemId}, operator, val_obj:[{value}]}.
Source types (sourceItem.itemTypeId), verified against Cornell's rules:
  11 question (custom attribute)   19 event type         4 room (space)
  1410 headcount                   2 organization        205 org is in a saved search
  1001 time of day (decimal hours) 1003 start date (validation)  -3 form mode
Anything else is "unsupported": it never matches, and `unsupported_rules` names
the rule so `doctor` and `check` can say so instead of silently skipping it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

SOURCES = {11, 19, 4, 1410, 2, 205, 1001, 1003, -3}
OPERATORS = {"=", "is", "in", "≠", "!=", "not in", "contains", "not contains",
             ">=", ">", "<=", "<", "between", "After"}
ACTIONS = {"addCustAtrb", "addRequirement", "addResource", "alertUser", "preventSave"}


@dataclass
class Context:
    event_type_id: int | None = None
    space_ids: list[int] = field(default_factory=list)
    headcount: int | None = None
    organization_id: int | None = None
    org_search_ids: list[int] = field(default_factory=list)  # saved searches the org belongs to
    start: datetime | None = None
    end: datetime | None = None
    today: datetime | None = None


def _norm(v):
    """Booleans become 'T'/'F'; strings compare case-insensitively (Cornell's rules
    and option lists don't always agree on capitalisation)."""
    if v is True: return "T"
    if v is False: return "F"
    if isinstance(v, str):
        if v.lower() in ("true", "false"):
            return "T" if v.lower() == "true" else "F"
        return v if v in ("T", "F") else v.strip().lower()
    return v


def _as_values(v) -> list:
    if v is None: return []
    return [_norm(x) for x in v] if isinstance(v, list) else [_norm(v)]


def _overlaps_hours(start: datetime, end: datetime, lo: float, hi: float) -> bool:
    """Does [start, end] touch the daily window lo..hi (decimal hours) on any day?
    The form doesn't say whether 'time of day' means start or end, so we take the
    widest reading: a rule that adds approvals is never skipped by our guess."""
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        w0, w1 = day + timedelta(hours=lo), day + timedelta(hours=hi)
        if start <= w1 and end >= w0:
            return True
        day += timedelta(days=1)
    return False


def _walk(node: dict):
    yield node
    kids = node.get("children")
    for k in (kids.get("item") or []) if isinstance(kids, dict) else []:
        yield from _walk(k)


def unsupported(rule: dict) -> list[str]:
    """What in this rule cu25 can't evaluate (empty = fully supported)."""
    out = []
    for n in _walk(rule["item"]):
        if n.get("type") == "comp":
            if n["sourceItem"].get("itemTypeId") not in SOURCES:
                out.append(f"source type {n['sourceItem'].get('itemTypeId')}")
            if n.get("operator") not in OPERATORS:
                out.append(f"operator {n.get('operator')!r}")
        elif n.get("type") != "bool" or n.get("operator", "and") not in ("and", "or"):
            out.append(f"node {n.get('type')}/{n.get('operator')}")
    out += [f"action {a['action']}" for a in rule["item"].get("actions", []) if a["action"] not in ACTIONS]
    return out


class Engine:
    def __init__(self, form_rules: list[dict], validation_rules: list[dict] | None = None):
        self.form = [r for r in form_rules if r.get("active")]
        self.validation = [r for r in (validation_rules or []) if r.get("active")]

    def unsupported_rules(self) -> list[str]:
        return [f"{r['rule_name']}: {', '.join(u)}"
                for r in self.form + self.validation if (u := unsupported(r))]

    # ---- condition evaluation ------------------------------------------
    def _source(self, src: dict, answers: dict, ctx: Context) -> list:
        t, i = src.get("itemTypeId"), src.get("itemId")
        if t == 11: return _as_values(answers.get(i))
        if t == 19: return [ctx.event_type_id]
        if t == 4: return list(ctx.space_ids)
        if t == 1410: return [ctx.headcount]
        if t == 2: return [ctx.organization_id]
        if t == 205: return list(ctx.org_search_ids)
        if t == -3: return ["create"]
        return []

    def _comp(self, node: dict, answers: dict, ctx: Context) -> bool:
        src, op = node["sourceItem"], node["operator"]
        t = src.get("itemTypeId")
        want = [_norm(v.get("value")) for v in node.get("val_obj", [])]
        if t not in SOURCES or op not in OPERATORS:
            return False
        if t == 1001:  # time of day
            if op != "between" or not (ctx.start and ctx.end): return False
            try:
                lo, hi = (float(x) for x in str(want[0]).split(","))
            except (IndexError, ValueError):
                return False
            return _overlaps_hours(ctx.start, ctx.end, lo, hi)
        if t == 1003:  # "After eventStart,-30": today is past (start - 30 days) => too late
            if op != "After" or not (ctx.start and ctx.today): return False
            try:
                days = int(str(want[0]).split(",")[1])
            except (IndexError, ValueError):
                return False
            return ctx.today > ctx.start + timedelta(days=days)

        have = [h for h in self._source(src, answers, ctx) if h is not None]
        # An unanswered question matches nothing, not even "not in" (the form only
        # evaluates answered questions). A saved-search list is always known: empty
        # means the org isn't in any, so "not in" is true.
        known = bool(have) or t == 205
        if op in ("=", "is", "in"):
            return any(h in want for h in have)
        if op in ("≠", "!=", "not in"):
            return known and not any(h in want for h in have)
        if op == "contains":
            return any(str(w) in str(h) for h in have for w in want)
        if op == "not contains":
            return known and not any(str(w) in str(h) for h in have for w in want)
        if op in (">=", ">", "<=", "<"):
            try:
                h, w = float(have[0]), float(want[0])
            except (IndexError, TypeError, ValueError):
                return False
            return {">=": h >= w, ">": h > w, "<=": h <= w, "<": h < w}[op]
        if op == "between":
            try:
                lo, hi = (float(x) for x in str(want[0]).split(","))
                return lo <= float(have[0]) <= hi
            except (IndexError, TypeError, ValueError):
                return False
        return False

    def _node(self, node: dict, answers: dict, ctx: Context) -> bool:
        if node.get("type") == "comp":
            return self._comp(node, answers, ctx)
        kids = node.get("children")
        results = [self._node(k, answers, ctx)
                   for k in ((kids.get("item") or []) if isinstance(kids, dict) else [])]
        if not results: return False
        return all(results) if node.get("operator", "and") == "and" else any(results)

    # ---- public ----------------------------------------------------------
    def evaluate(self, answers: dict[int, object], ctx: Context, root_questions: list[int]) -> dict:
        questions = list(dict.fromkeys(root_questions))
        out = {"requirements": {}, "resources": {}, "alerts": [], "blocked": []}
        fired: set[int] = set()
        changed = True
        while changed:  # answers unlock questions whose answers unlock more rules
            changed = False
            # Only answers to questions the form is actually asking count, as in the web form.
            live = {q: answers[q] for q in questions if q in answers}
            for idx, r in enumerate(self.form):
                if idx in fired or not self._node(r["item"], live, ctx):
                    continue
                fired.add(idx)
                for act in r["item"].get("actions", []):
                    for t in act.get("targets", []):
                        a = act["action"]
                        if a == "addCustAtrb" and t["itemId"] not in questions:
                            questions.append(t["itemId"]); changed = True
                        elif a == "addRequirement":
                            out["requirements"][t["itemId"]] = t.get("itemName")
                        elif a == "addResource":
                            out["resources"][t["itemId"]] = t.get("itemName")
                        elif a == "alertUser" and t.get("itemName") not in out["alerts"]:
                            out["alerts"].append(t.get("itemName"))
        live = {q: answers[q] for q in questions if q in answers}
        for r in self.validation:
            if self._node(r["item"], live, ctx):
                acts = {a["action"]: a for a in r["item"].get("actions", [])}
                msg = next((t.get("itemName") for t in acts.get("alertUser", {}).get("targets", [])),
                           r["rule_name"])
                (out["blocked"] if "preventSave" in acts else out["alerts"]).append(msg)
        out["questions"] = questions
        out["unanswered"] = [q for q in questions if answers.get(q) in (None, "", [])]
        return out
