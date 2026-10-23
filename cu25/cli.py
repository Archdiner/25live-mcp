"""cu25: request Cornell rooms in 25Live. Every command prints one JSON object.

  cu25 login                                        sign in once (NetID + Duo)
  cu25 whoami
  cu25 rooms 2026-10-29 17:00 18:00 [--people 20] [--name stimson]
  cu25 orgs "generative ai"                         find your org_id
  cu25 types                                        event types you may request
  cu25 check request.json                           what's still missing, in the form's words
  cu25 submit request.json                          preview + confirm code (sends nothing)
  cu25 submit request.json --confirm CODE           submit exactly what was previewed
  cu25 copy 561543                                  an old event as a starting request
  cu25 status 561543
  cu25 doctor                                       does cu25 understand the live form?
  cu25 schema [request|check|submit|...]            JSON Schemas of inputs and outputs
  cu25 guide                                        the workflow, for agents

A request is a file path, - for stdin, or inline JSON. Exit 0 = ok, 1 = error,
2 = needs attention (not ready, or a stale preview).
"""
from __future__ import annotations

import argparse
import json
import sys

from pydantic import BaseModel, ValidationError

from .models import OUTPUTS, Error, EventTypes, Orgs, Request, Rooms


def read_request(arg: str) -> Request:
    if arg == "-":
        text = sys.stdin.read()
    elif arg.lstrip().startswith("{"):
        text = arg
    else:
        try:
            text = open(arg).read()
        except OSError as e:
            raise ValueError(f"request: can't read {arg!r} ({e.strerror}). "
                             "Pass a file path, - for stdin, or inline JSON.")
    return Request.model_validate_json(text)


def schemas(name: str | None) -> dict:
    allm = {"request": Request, **OUTPUTS}
    if name:
        if name not in allm:
            raise ValueError(f"no schema {name!r}; one of {sorted(allm)}")
        return allm[name].model_json_schema()
    return {k: m.model_json_schema() for k, m in allm.items()}


def exit_code(cmd: str, out: BaseModel | dict) -> int:
    if isinstance(out, Error): return 1
    if cmd == "check" and not out.ready: return 2
    if cmd == "submit" and out.status in ("not_ready", "changed"): return 2
    if cmd == "doctor" and not out.ok: return 2
    return 0


def run(argv=None) -> tuple[str, BaseModel | dict | str]:
    ap = argparse.ArgumentParser(prog="cu25", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("login", "whoami", "types", "doctor", "guide"):
        sub.add_parser(c)
    r = sub.add_parser("rooms")
    for a in ("date", "start", "end"):
        r.add_argument(a)
    r.add_argument("--people", type=int)
    r.add_argument("--name", help="building or room, e.g. 'stimson' or 'MVR1151'")
    sub.add_parser("orgs").add_argument("name")
    sub.add_parser("check").add_argument("request")
    s = sub.add_parser("submit"); s.add_argument("request"); s.add_argument("--confirm")
    sub.add_parser("copy").add_argument("event_id", type=int)
    sub.add_parser("status").add_argument("event_id", type=int)
    sub.add_parser("schema").add_argument("name", nargs="?")
    args = ap.parse_args(argv)

    from .client import Client, CU25Error, parse_slot
    try:
        if args.cmd == "guide":
            from .guide import GUIDE
            return args.cmd, GUIDE
        if args.cmd == "schema":
            return args.cmd, schemas(args.name)
        if args.cmd == "login":
            from .login import login
            return args.cmd, {"logged_in_as": login()}
        req = read_request(args.request) if args.cmd in ("check", "submit") else None
        if args.cmd == "rooms":
            parse_slot(args.date, args.start, args.end)
        cl = Client()
        return args.cmd, {
            "whoami": lambda: cl.whoami(),
            "types": lambda: EventTypes(types=cl.event_types()),
            "rooms": lambda: Rooms(rooms=cl.rooms(*parse_slot(args.date, args.start, args.end),
                                                  name=args.name, people=args.people)),
            "orgs": lambda: Orgs(orgs=cl.search_orgs(args.name)),
            "check": lambda: cl.check(req),
            "submit": lambda: cl.submit(req, confirm=args.confirm),
            "copy": lambda: cl.copy_request(args.event_id),
            "status": lambda: cl.status(args.event_id),
            "doctor": lambda: cl.doctor(),
        }[args.cmd]()
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(map(str, x['loc'])) or 'request'}: {x['msg']}" for x in e.errors())
        return args.cmd, Error(error=f"Invalid request: {msg}", error_type="invalid_input")
    except (ValueError, OSError) as e:
        return args.cmd, Error(error=str(e), error_type="invalid_input")
    except CU25Error as e:
        return args.cmd, Error(error=str(e), error_type=e.error_type)


def main(argv=None) -> int:
    cmd, out = run(argv)
    if isinstance(out, str):
        print(out)
        return 0
    print(json.dumps(out.model_dump(mode="json") if isinstance(out, BaseModel) else out,
                     indent=2, ensure_ascii=False))
    return exit_code(cmd, out)


if __name__ == "__main__":
    sys.exit(main())
