"""HTTP client for Cornell's 25Live, riding the user's own logged-in session.

Every call here was captured from the real 25Live Pro web app (HAR, Oct 2026).
Base: https://25live.collegenet.com/25live/data/cornell/run/  Auth: session cookie only.

Environment:
  CU25_HOME    where the session and form cache live (default ~/.cu25)
  CU25_BASE    25Live base URL (tests point this at a fake server)
  CU25_RECORD  a folder: every request/response is saved there as JSON, to make fixtures
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from . import payload as P
from .models import (CheckResult, Copied, Doctor, EventType, Org, Preview, Request, Room,
                     Status, SubmitResult, Whoami)
from .request import (FIELD_KEYS, QUESTION_TYPES, Schema, check, display_answer, encode_answer,
                      evaluate)
from .rules import Context, Engine

HOME = Path(os.environ.get("CU25_HOME", Path.home() / ".cu25"))
BASE = os.environ.get("CU25_BASE", "https://25live.collegenet.com/25live/data/cornell/run")
FORM_CACHE_SECONDS = 3600  # the form definition changes rarely; submit always refetches


class CU25Error(RuntimeError):
    error_type = "error"


class SessionExpired(CU25Error):
    error_type = "session_expired"


class UpstreamChanged(CU25Error):
    """25Live answered with a shape cu25 doesn't recognise."""
    error_type = "upstream"


def _items(resp: dict) -> list[dict]:
    return P.as_list(resp.get("searchCriteria", {}).get("item"))


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s[:10]) if s else None


class Client:
    def __init__(self, home: Path = HOME, base: str = BASE):
        self.home, self.base = home, base.rstrip("/")
        state_file = home / "session.json"
        if not state_file.exists():
            raise SessionExpired("Not logged in. Run: cu25 login")
        state = json.loads(state_file.read_text())
        jar = httpx.Cookies()
        for c in state.get("cookies", []):
            if "collegenet.com" in c.get("domain", ""):
                jar.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
        self.http = httpx.Client(cookies=jar, timeout=30, follow_redirects=False, headers={
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "origin": "https://25live.collegenet.com",
            "referer": "https://25live.collegenet.com/pro/cornell",
        })
        self._memo: dict = {}
        self._record = Path(os.environ["CU25_RECORD"]) if os.environ.get("CU25_RECORD") else None

    # ---- plumbing -------------------------------------------------------
    def _call(self, method: str, path: str, query: str = "", body: dict | None = None) -> dict:
        url = f"{self.base}/{path}?{query + '&' if query else ''}caller=pro"
        r = self.http.request(method, url, content=json.dumps(body) if body is not None else None)
        if self._record:
            self._record.mkdir(parents=True, exist_ok=True)
            n = len(list(self._record.glob("*.json")))
            (self._record / f"{n:03d}_{method}_{path.replace('/', '_')}").with_suffix(".json").write_text(
                json.dumps({"method": method, "path": path, "query": query, "body": body,
                            "status": r.status_code, "response": r.text}, indent=1))
        if r.status_code in (301, 302, 303, 401, 403) or "text/html" in r.headers.get("content-type", ""):
            raise SessionExpired("25Live session expired. Run: cu25 login")
        if r.status_code >= 400:
            raise CU25Error(f"{method} {path} failed ({r.status_code}): {r.text[:300]}")
        try:
            return r.json() if r.content else {}
        except ValueError:
            raise UpstreamChanged(f"{method} {path} didn't return JSON: {r.text[:200]}")

    def _get(self, key, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def _parse(self, what: str, fn, *args):
        """Run a parser; a missing key means 25Live changed, so say that instead of crashing."""
        try:
            return fn(*args)
        except (KeyError, IndexError, TypeError, StopIteration, ValueError) as e:
            raise UpstreamChanged(f"25Live's {what} response has an unexpected shape ({e!r}). "
                                  "Run with CU25_RECORD=dir to capture it.")

    def _seq(self, item: str) -> int:
        r = self._call("GET", "sequence/id.json", f"itemName={item}")
        return self._parse("sequence", lambda: int(r["sequence"]["sequence_id"]))

    # ---- who / what -----------------------------------------------------
    def whoami(self) -> Whoami:
        def go():
            login = self._call("GET", "login.json")["login_response"]["login"]
            if login.get("success") != "T" or not login.get("username"):
                raise SessionExpired("25Live session expired. Run: cu25 login")
            return Whoami(netid=login["username"], name=login["contact_name"],
                          contact_id=int(login["user_id"]), group=login.get("security_group_name"))
        return self._get("whoami", go)

    def event_types(self) -> list[EventType]:
        return [EventType(type_id=k, name=v) for k, v in self.schema().event_types.items()]

    def search_orgs(self, name: str) -> list[Org]:
        r = self._call("GET", "search-criteria/event/organizations.json",
                       f"obj_cache_accl=0&page=0&page_size=100&name={quote(name)}")
        return self._parse("organization search",
                           lambda: [Org(org_id=i["itemId"], name=i["itemName"]) for i in _items(r)])

    def org_name(self, org_id: int) -> str:
        r = self._call("GET", "organizations.json", f"organization_id={org_id}&scope=minimal")
        return self._parse("organization", lambda: r["organizations"]["organization"]["organization_name"])

    def org_in_searches(self, org_id: int, query_ids: list[int]) -> list[int]:
        """Which saved org searches (used by rules, e.g. 'is a student org') contain this org."""
        def go():
            hits = []
            for q in query_ids:
                r = self._call("GET", "organizations.json", f"scope=list&query_id={q}&organization_id={org_id}")
                if P.as_list(r.get("list", {}).get("item")):
                    hits.append(q)
            return hits
        return self._get(("org_in", org_id, tuple(query_ids)), go)

    # ---- rooms ----------------------------------------------------------
    def search_spaces(self, start: datetime, end: datetime, name: str | None = None,
                      min_capacity: int | None = None, page_size: int = 25) -> list[dict]:
        q = (f"scope=extended&can_schedule=T&perm_start_dt={P.fmt_dt(start)}"
             f"&perm_end_dt={P.fmt_dt(end)}&include=layouts&order=asc&sort=name"
             f"&page=1&page_size={page_size}&paginate")
        if min_capacity:
            q += f"&min_default_capacity={min_capacity}"
        if name:
            q += f"&name={quote(name)}"
        r = self._call("GET", "spaces.json", q)
        return self._parse("room search", P.parse_spaces, r)

    def availability(self, space_ids: list[int], start: datetime, end: datetime) -> dict[int, dict]:
        if not space_ids:
            return {}
        resp = self._call("POST", "space_avail.json", body=P.availability_body(space_ids, start, end))
        return self._parse("availability", P.parse_availability, resp)

    def rooms(self, start: datetime, end: datetime, name: str | None = None,
              people: int | None = None) -> list[Room]:
        """Rooms you may request at that time, free ones first."""
        if end <= start:
            raise CU25Error("End time must be after start time.")
        found = self.search_spaces(start, end, name=name, min_capacity=people)
        avail = self.availability([r["space_id"] for r in found], start, end)
        out = [Room(**r, **avail.get(r["space_id"], {"free": False, "can_request": False}),
                    fully_scheduled=not r["code"].endswith("**")) for r in found]
        return sorted(out, key=lambda r: (not r.free, r.capacity or 0))

    def space_info(self, space_id: int) -> dict:
        def go():
            resp = self._call("POST", "spaces.json", "request_method=get", body={"mapxml": {
                "space_id": str(space_id), "scope": "extended",
                "include": "layouts+contacts+direct_schedule"}})
            return self._parse("room detail", P.parse_space_detail, resp)
        return self._get(("space", space_id), go)

    # ---- the form -------------------------------------------------------
    def _form_definition(self, fresh: bool) -> dict:
        """The raw form definition, cached on disk for an hour (fresh=True refetches)."""
        cache = self.home / "form-cache.json"
        if not fresh and cache.exists():
            c = json.loads(cache.read_text())
            if c.get("base") == self.base and time.time() - c.get("fetched_at", 0) < FORM_CACHE_SECONDS:
                return c
        c = {"base": self.base, "fetched_at": time.time(), "roots": {},
             "config": self._call("GET", "wizard/config.json", "mode=create"),
             "security": self._call("GET", "usergroup/wizardsecurity.json"),
             "rules": self._call("GET", "rule/trees.json", "category_id=form"),
             "validation": self._call("GET", "rule/trees.json", "category_id=formValidation"),
             "questions": self._call("GET", "search-criteria/customattributes.json", "itemTypeId=1&all_types=F"),
             "options": self._call("GET", "custom/attribute/options.json"),
             "types": self._call("GET", "search-criteria/event/types.json", "active=T&all_types=F&form_config=T")}
        cache.write_text(json.dumps(c))
        return c

    def _roots(self, c: dict, type_id: int) -> list[int]:
        key = str(type_id)
        if key not in c["roots"]:
            r = self._call("GET", "search-criteria/customattributes.json",
                           f"itemTypeId=11&all_types=F&event_type_id={type_id}")
            c["roots"][key] = [i["itemId"] for i in _items(r)]
            (self.home / "form-cache.json").write_text(json.dumps(c))
        return c["roots"][key]

    def schema(self, type_id: int | None = None, fresh: bool = False) -> Schema:
        """The live form: fields, questions, options, rules and date limits. Nothing hard-coded."""
        def go():
            c = self._form_definition(fresh)
            def build():
                sec = c["security"]["root"]
                allowed = {int(x) for x in str(sec.get("event_types_allowed", "")).split()}
                types = {i["itemId"]: i["itemName"] for i in _items(c["types"])
                         if not allowed or i["itemId"] in allowed}
                return Schema(
                    fields=c["config"]["config"]["item"],
                    form_rules=P.as_list(c["rules"].get("rule")),
                    validation_rules=P.as_list(c["validation"].get("rule")),
                    questions={i["itemId"]: i for i in _items(c["questions"])},
                    options={o["itemId"]: o["opt"] for o in P.as_list(c["options"].get("item"))},
                    root_questions=self._roots(c, type_id) if type_id else [],
                    event_types=types,
                    min_date=_date(sec.get("event_create_min_date")),
                    max_date=_date(sec.get("event_create_max_date")))
            return self._parse("form definition", build)
        return self._get(("schema", type_id, fresh), go)

    def context(self, req: Request, schema: Schema) -> Context:
        query_ids = sorted({int(v["value"]) for r in schema.form_rules if r.get("active")
                            for v in _org_query_values(r["item"])})
        return Context(
            event_type_id=req.type_id,
            space_ids=[req.space_id] if req.space_id else [],
            headcount=req.headcount,
            organization_id=req.org_id,
            org_search_ids=self.org_in_searches(req.org_id, query_ids) if req.org_id else [],
            start=req.start, end=req.end, today=datetime.now())

    def check(self, req: Request, fresh: bool = False) -> CheckResult:
        """What's still missing before this request can be submitted, in the form's own words."""
        schema = self.schema(req.type_id, fresh=fresh)
        out = check(req, schema, self.context(req, schema))
        if req.space_id:
            room = self.space_info(req.space_id)
            if req.layout_id and room["layouts"] and req.layout_id not in room["layouts"]:
                out.problems.append(f"Layout {req.layout_id} isn't one of this room's layouts "
                                    f"({room['layouts']}).")
            if req.headcount and room["capacity"] and req.headcount > room["capacity"]:
                out.warnings.append(f"{req.headcount} people is more than {room['code']} holds "
                                    f"({room['capacity']}).")
            if req.start and req.end and req.end > req.start:
                a = self.availability([req.space_id], req.start, req.end).get(req.space_id, {})
                if not a.get("can_request"):
                    out.problems.append("You can't request that room at that time.")
                elif not a.get("free"):
                    out.problems.append("That room isn't free then" + (
                        f" (booked until {a['conflict_until']})." if a.get("conflict_until") else "."))
        out.ready = not (out.missing or out.problems or out.blocked)
        return out

    # ---- submit ---------------------------------------------------------
    def _build(self, req: Request, schema: Schema, ctx: Context) -> tuple[dict, Preview]:
        me, room = self.whoami(), self.space_info(req.space_id)
        sched_id, sched_name = room["scheduler"] or (None, "")
        result, canon, _ = evaluate(req, schema, ctx)
        active = [q for q in result["questions"] if q in canon]
        attrs = [{"attribute_id": q,
                  "attribute_value": encode_answer(canon[q], schema.answer_type(q)),
                  "attribute_type": schema.questions[q]["itemTypeId"],
                  "attribute_name": schema.questions[q]["itemName"]} for q in active]
        tpl = {
            "event_name": req.name, "event_type_id": req.type_id,
            "event_type_name": schema.event_types.get(req.type_id, ""),
            "organization_id": req.org_id, "organization_name": self.org_name(req.org_id),
            "description": P.description_html(req.description),
            "expected_count": req.headcount, "space_id": req.space_id,
            "layout_id": req.layout_id or room["layout_id"],
            "requestor_id": me.contact_id, "requestor_name": me.name,
            "scheduler_id": sched_id, "scheduler_name": sched_name, "attributes": attrs,
        }
        preview = Preview(
            name=req.name, type=tpl["event_type_name"], organization=tpl["organization_name"],
            room=f"{room['code']} ({room['name']})", layout_id=tpl["layout_id"],
            start=P.fmt_dt(req.start), end=P.fmt_dt(req.end), headcount=req.headcount,
            description=req.description or "",
            answers={schema.questions[q]["itemName"]: display_answer(canon[q], schema.answer_type(q))
                     for q in active},
            requestor=me.name, scheduler=sched_name or "(none)")
        return tpl, preview

    def submit(self, req: Request, confirm: str | None = None) -> SubmitResult:
        """Without confirm: check, then preview + confirm code (sends nothing).
        With the code from that preview: re-check against a fresh form and submit exactly that."""
        fresh = confirm is not None
        result = self.check(req, fresh=fresh)
        if not result.ready:
            return SubmitResult(status="not_ready", submitted=False, check=result)
        schema = self.schema(req.type_id, fresh=fresh)
        tpl, preview = self._build(req, schema, self.context(req, schema))
        body0 = P.submit_body(tpl, event_id=0, profile_id=0, reservation_id=0, start=req.start, end=req.end)
        code = hashlib.sha256(json.dumps(body0, sort_keys=True).encode()).hexdigest()[:10]
        if confirm is None:
            return SubmitResult(status="preview", submitted=False, preview=preview, confirm=code,
                                messages=result.warnings)
        if confirm != code:
            return SubmitResult(status="changed", submitted=False, preview=preview, confirm=code, messages=[
                "The request or the live form changed since that preview. Show this preview "
                "to the user and confirm again with the new code."])

        event_id, profile_id = self._seq("events"), self._seq("date_profile")
        self._call("PUT", "event.json", "prime", body=P.presave_body(event_id, datetime.now()))
        try:
            gen = self._call("POST", "micro/sequence/generate.json", "element=reservation&quantity=1")
            rsrv_id = self._parse("reservation id", lambda: gen["content"]["data"]["items"][0]["idSet"][0]["rsrvId"])
            body = P.submit_body(tpl, event_id=event_id, profile_id=profile_id,
                                 reservation_id=rsrv_id, start=req.start, end=req.end)
            resp = self._call("PUT", "rose/event.json", f"event_id={event_id}&return_doc=T", body=body)
        except CU25Error as e:
            raise type(e)(f"{e} (a blank draft 'Presave: {event_id}' may be left in 25Live)")
        out = self._parse("submit", P.parse_submit, resp)
        return SubmitResult(status="submitted", submitted=True, **out)

    # ---- existing events -----------------------------------------------
    def _detail(self, event_id: int) -> dict:
        return self._get(("detail", event_id), lambda: self._call(
            "GET", f"micro/event/{event_id}/detail.json", "expand=T&include=all"))

    def copy_request(self, event_id: int) -> Copied:
        """An existing event as a Request, minus dates: a starting point, not a default."""
        return Copied(request=self._parse("event detail", lambda: Request(**P.request_from_detail(
            self._detail(event_id)))))

    def status(self, event_id: int) -> Status:
        def go():
            detail = self._detail(event_id)
            item = detail["content"]["data"]["items"][0]
            states = {s["state"]: s["stateName"] for s in detail["content"]["expandedInfo"].get("states", [])}
            return Status(event_id=item["id"], reference=item.get("eventLocator"), name=item["name"],
                          state=states.get(item["context"]["state"], item["context"]["state"]),
                          start=item["dates"]["startDate"], end=item["dates"]["endDate"])
        return self._parse("event detail", go)

    # ---- self-check -------------------------------------------------------
    def doctor(self) -> Doctor:
        """Is the session alive, and does cu25 understand everything in the live form?"""
        me = self.whoami()
        s = self.schema(fresh=True)
        unknown_fields = [f["item_name"] for f in s.fields
                          if f.get("required") and f.get("visible") and f["item_name"] not in FIELD_KEYS]
        types = sorted({str(q.get("itemTypeId")) for q in s.questions.values()} - QUESTION_TYPES)
        unsupported = Engine(s.form_rules, s.validation_rules).unsupported_rules()
        return Doctor(
            ok=not (unknown_fields or types or unsupported), session=me,
            unknown_required_fields=unknown_fields, unsupported_rules=unsupported,
            unknown_question_types=types,
            counts={"fields": len(s.fields), "questions": len(s.questions),
                    "options_lists": len(s.options), "event_types": len(s.event_types),
                    "form_rules": sum(1 for r in s.form_rules if r.get("active")),
                    "validation_rules": sum(1 for r in s.validation_rules if r.get("active"))})


def _org_query_values(node: dict):
    """Values of 'org is in saved search' (type 205) conditions anywhere in a rule tree."""
    if node.get("type") == "comp":
        if node["sourceItem"].get("itemTypeId") == 205:
            yield from node.get("val_obj", [])
        return
    kids = node.get("children")
    for k in (kids.get("item") or []) if isinstance(kids, dict) else []:
        yield from _org_query_values(k)
