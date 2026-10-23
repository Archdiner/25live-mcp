"""Pure functions that build and parse 25Live request bodies.

Shapes match what the 25Live Pro web app sent when a request was made by hand
(captured from a HAR, Oct 2026). Nothing here touches the network.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime

DAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def fmt_dt(dt: datetime) -> str:
    """25Live wants naive local times, e.g. 2026-10-25T14:00:00."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def profile_code(start: datetime) -> str:
    """Single-occurrence profile code, e.g. 'W1 SU 20261025T235900'."""
    return f"W1 {DAY_CODES[start.weekday()]} {start.strftime('%Y%m%d')}T235900"


def presave_body(event_id: int, now: datetime) -> dict:
    t = now.replace(second=0, microsecond=0)
    return {"events": {"event": {
        "status": "new", "event_id": event_id,
        "start_date": fmt_dt(t), "end_date": fmt_dt(t),
        "event_name": f"Presave: {event_id}", "state": -1, "event_type_id": -1,
    }}}


def _decode_answer(value: str, data_type: str):
    if data_type == "B":
        return value == "T"
    if isinstance(value, str) and value.startswith("[{"):
        try:
            return [x["item"] for x in json.loads(value)]
        except (ValueError, KeyError, TypeError):
            pass
    return value


def request_from_detail(detail: dict) -> dict:
    """An existing event (micro/event/{id}/detail.json) as Request fields, dates left blank."""
    item = detail["content"]["data"]["items"][0]
    types = {a["attributeId"]: a["dataType"] for a in detail["content"]["expandedInfo"].get("attributes", [])}
    prof = item["profiles"][0]
    space = prof["reservations"][0]["spaces"][0]["requested"][0]
    desc = next((t["description"] for t in item.get("text", []) if "description" in t), "")
    return {
        "name": item["name"],
        "type_id": item["context"]["typeId"],
        "org_id": item["organizations"]["primary"],
        "headcount": prof.get("expectedCount") or None,
        "start": None,
        "end": None,
        "space_id": space["spaceId"],
        "layout_id": space["layoutId"],
        "description": html.unescape(re.sub(r"<[^>]+>", "", desc)) or None,
        "answers": {str(a["attributeId"]): _decode_answer(a["value"], types[a["attributeId"]])
                    for a in item.get("attributes", [])},
        "agree": False,
    }


def submit_body(
    tpl: dict,
    *,
    event_id: int,
    profile_id: int,
    reservation_id: int,
    start: datetime,
    end: datetime,
) -> dict:
    """The PUT rose/event.json body that submits a request (state 1 = Tentative)."""
    s, e = fmt_dt(start), fmt_dt(end)
    roles = [{"status": "new", "role_id": -1, "role_name": "Requestor",
              "contact": {"status": "new", "contact_id": tpl["requestor_id"],
                          "contact_name": tpl["requestor_name"]}}]
    if tpl.get("scheduler_id"):
        roles.append({"status": "new", "role_id": -2, "role_name": "Scheduler",
                      "contact": {"status": "new", "contact_id": tpl["scheduler_id"],
                                  "contact_name": tpl["scheduler_name"]}})
    return {"events": {"event": {
        "state": 1,
        "event_id": str(event_id),
        "status": "new",
        "profile": [{
            "status": "new",
            "profile_id": str(profile_id),
            "rec_type_id": 0,
            "prof_use": 1,
            "reservation": [{
                "status": "new",
                "reservation_state": 1,
                "reservation_start_dt": s, "reservation_end_dt": e,
                "event_start_dt": s, "event_end_dt": e,
                "pre_event_dt": s, "post_event_dt": e,
                "rsrv_comments": "",
                "reservation_id": reservation_id,
                "space_reservation": [{
                    "status": "new", "space_id": tpl["space_id"],
                    "layout_id": tpl["layout_id"], "share": "F",
                    "space_instructions": "",
                }],
            }],
            "init_start_dt": s, "init_end_dt": e,
            "profile_code": profile_code(start),
            "expected_count": tpl["expected_count"],
        }],
        "event_name": tpl["event_name"],
        "event_text": [{"status": "new", "text_type_id": 1,
                        "text_type_name": "Description", "text": tpl["description"]}],
        "start_date": s,
        "end_date": e,
        "event_type_id": tpl["event_type_id"],
        "event_type_name": tpl["event_type_name"],
        "organization": [{"status": "new", "primary": "T",
                          "organization_id": tpl["organization_id"],
                          "organization_name": tpl["organization_name"]}],
        "custom_attribute": [{"status": "new", **a} for a in tpl["attributes"]],
        "role": roles,
    }}}


def availability_body(space_ids: list[int], start: datetime, end: datetime) -> dict:
    return {"space_availability": {
        "requirements": {
            "space": [{"space_id": i} for i in space_ids],
            "hide_conflicts": "F", "enable_override": "F", "allow_sharing": "F",
        },
        "dates": [{"start_dt": fmt_dt(start), "end_dt": fmt_dt(end)}],
    }}


def as_list(x) -> list:
    if x is None or x == "":
        return []
    return x if isinstance(x, list) else [x]


def _space(sp: dict) -> dict:
    layouts = as_list(sp.get("layout"))
    default = next((l for l in layouts if l.get("default_layout") == "T"), layouts[0] if layouts else {})
    return {"space_id": sp["space_id"], "code": sp["space_name"], "name": sp["formal_name"],
            "capacity": sp.get("max_capacity"), "layout_id": default.get("layout_id"),
            "layouts": [l["layout_id"] for l in layouts]}


def parse_spaces(resp: dict) -> list[dict]:
    return [_space(sp) for sp in as_list(resp.get("spaces", {}).get("space"))]


def parse_availability(resp: dict) -> dict[int, dict]:
    out = {}
    for sp in as_list(resp.get("spaces", {}).get("space")):
        d = sp.get("dates") or {}
        conflict = d.get("conflict") or {}
        out[sp["space_id"]] = {
            "free": sp.get("has_conflicts") == "F" and d.get("can_schedule") == "T",
            "can_request": d.get("can_schedule") == "T",
            "conflict_until": conflict.get("conflict_end"),
        }
    return out


def description_html(text: str | None) -> str:
    """Plain text -> the form's rich-text HTML, one <p> per paragraph, escaped."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras)


def parse_space_detail(resp: dict) -> dict:
    """Room, layouts and scheduler from POST spaces.json?request_method=get."""
    sp = resp["spaces"]["space"]
    sched = next(((c["contact_id"], c["contact_name"]) for c in as_list(sp.get("contact"))
                  if c.get("role_name") == "Scheduler"), None)
    return {**_space(sp), "scheduler": sched}


def parse_submit(resp: dict) -> dict:
    ev = resp["events"]["event"]
    msgs = as_list(resp["events"].get("messages"))
    return {
        "event_id": ev["event_id"],
        "reference": ev.get("event_locator"),
        "state": ev.get("state_name"),
        "messages": [m.get("msg_text") for m in msgs],
    }
