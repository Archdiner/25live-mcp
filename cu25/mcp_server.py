"""MCP face over the same Client and models as the CLI, for chat apps without a shell.

Tool inputs and outputs are the pydantic models in models.py, so the MCP schemas
are the same contract `cu25 schema` prints. Errors are raised, so the client sees
an error result whose text starts with the error type (e.g. "session_expired: ...").
"""
from __future__ import annotations

import sys

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations
except ImportError:
    sys.exit("cu25-mcp needs the MCP extra: pip install 'cu25[mcp]'")

from .client import Client, CU25Error, parse_slot
from .guide import GUIDE
from .library import Library, library_slot
from .models import (CheckResult, Copied, Doctor, EventTypes, LibraryRequest, LibraryResult,
                     LibrarySpaces, Orgs, Request, Rooms, Status, SubmitResult, Whoami)

mcp = MCPServer("cu25", instructions=GUIDE)
READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)


def _run(fn, make=Client):
    try:
        return fn(make())
    except CU25Error as e:
        raise RuntimeError(f"{e.error_type}: {e}") from None


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, open_world_hint=True))
def login() -> dict:
    """Open a browser so the user can sign in with NetID + Duo. Only when a tool says session_expired."""
    from .login import login as do_login
    return {"logged_in_as": do_login()}


@mcp.tool(annotations=READ)
def whoami() -> Whoami:
    """The signed-in 25Live user."""
    return _run(lambda c: c.whoami())


@mcp.tool(annotations=READ)
def types() -> EventTypes:
    """Event types the user may request, with their type_id."""
    return _run(lambda c: EventTypes(types=c.event_types()))


@mcp.tool(annotations=READ)
def orgs(name: str) -> Orgs:
    """Find sponsoring organization ids by (part of a) name."""
    return _run(lambda c: Orgs(orgs=c.search_orgs(name)))


@mcp.tool(annotations=READ)
def rooms(date: str, start: str, end: str, people: int | None = None, name: str | None = None,
          limit: int = 50) -> Rooms:
    """Rooms the user may request at a time (YYYY-MM-DD, HH:MM 24h Ithaca time), free first.

    `total` counts every room free at that time; `rooms` shows at most `limit` (0 = all).
    """
    return _run(lambda c: c.rooms(*parse_slot(date, start, end), name=name, people=people,
                                  limit=limit))


@mcp.tool(annotations=READ)
def check(request: Request) -> CheckResult:
    """What's missing from a request, in the form's own words. Ask the user; never guess."""
    return _run(lambda c: c.check(request))


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                      open_world_hint=True))
def submit(request: Request, confirm: str | None = None) -> SubmitResult:
    """Without confirm: a preview and a confirm code, nothing sent. Show the preview, get the
    user's yes, then call again with that confirm code to submit exactly that request."""
    return _run(lambda c: c.submit(request, confirm=confirm))


@mcp.tool(annotations=READ)
def copy(event_id: int) -> Copied:
    """An existing event as a starting request (dates blank)."""
    return _run(lambda c: c.copy_request(event_id))


@mcp.tool(annotations=READ)
def status(event_id: int) -> Status:
    """Tentative / Confirmed / Denied / Cancelled for an event."""
    return _run(lambda c: c.status(event_id))


@mcp.tool(annotations=READ)
def doctor() -> Doctor:
    """Is the session alive, and does cu25 understand everything in the live form?"""
    return _run(lambda c: c.doctor())


@mcp.tool(annotations=READ)
def libspaces(date: str, start: str | None = None, end: str | None = None, people: int | None = None,
              name: str | None = None, limit: int = 50) -> LibrarySpaces:
    """Library study spaces (no login needed) with their free times on a day (YYYY-MM-DD). With
    start and end (HH:MM 24h Ithaca time), free ones first. `name` matches library, category or space."""
    try:
        slot = library_slot(date, start, end)
    except ValueError as e:
        raise RuntimeError(f"invalid_input: {e}") from None
    return _run(lambda c: c.spaces(*slot, people=people, name=name, limit=limit), Library)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                      open_world_hint=True))
def libbook(booking: LibraryRequest, confirm: str | None = None) -> LibraryResult:
    """Book a library space. Without confirm: a preview and a confirm code, nothing booked. Show
    the preview, get the user's yes, then call again with that code. Booked at once, no approval."""
    return _run(lambda c: c.book(booking, confirm=confirm), Library)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
