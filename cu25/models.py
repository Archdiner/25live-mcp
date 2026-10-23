"""Every input and output of cu25, as pydantic models.

These are the contract. The CLI prints `model.model_dump(mode="json")`, the MCP
server uses the same models as tool signatures, `cu25 schema` prints their JSON
Schemas, and the tests validate every response against them. If a field isn't
here, cu25 doesn't produce or accept it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

Answer = Union[bool, str, list[str]]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)


# ---- input ------------------------------------------------------------------

class Request(Model):
    """A room request as the agent writes it. Anything left out is reported by `check`."""
    name: str | None = Field(None, description="Event name. Lead with the org name, e.g. 'CCC Game Night'.")
    type_id: int | None = Field(None, description="Event type id, from `types`.")
    org_id: int | None = Field(None, description="Sponsoring organization id, from `orgs`.")
    headcount: int | None = Field(None, ge=1, description="Most people in the room at once.")
    start: datetime | None = Field(None, description="Local Ithaca time, no offset: 2026-10-29T17:00")
    end: datetime | None = Field(None, description="Local Ithaca time, no offset: 2026-10-29T18:00")
    space_id: int | None = Field(None, description="Room id, from `rooms`.")
    layout_id: int | None = Field(None, description="Room layout; default is the room's own.")
    description: str | None = Field(None, description="Plain text. Who, what, setup needs.")
    answers: dict[int, Answer] = Field(default_factory=dict, description=(
        "Question id -> answer. yes/no questions take true/false; choice questions take one "
        "of the listed options; 'choices' questions take a list of options."))
    agree: bool = Field(False, description="True only after the user accepted the policy statement.")

    @field_validator("start", "end")
    @classmethod
    def _local(cls, v: datetime | None):
        return _local(v)


def _local(v: datetime | None):
    if v is not None and v.tzinfo is not None:
        raise ValueError("use local Ithaca time without a UTC offset, e.g. 2026-10-29T17:00")
    return v


class LibraryRequest(Model):
    """A library space booking (LibCal). Booked at once, under the user's own name and email."""
    space_id: int | None = Field(None, description="Library space id, from `libspaces`.")
    start: datetime | None = Field(None, description="Local Ithaca time, no offset: 2026-10-29T17:00")
    end: datetime | None = Field(None, description="Same day as start.")
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = Field(None, description="The user's own address; the confirmation goes there.")
    answers: dict[str, str | list[str]] = Field(default_factory=dict, description=(
        "Extra questions on the library's booking form, by the field name `libbook` reports."))

    @field_validator("start", "end")
    @classmethod
    def _local(cls, v: datetime | None):
        return _local(v)


# ---- outputs ----------------------------------------------------------------

class MissingField(Model):
    kind: Literal["field"] = "field"
    field: str = Field(description="Request key to fill.")
    question: str
    help: str = ""
    options: list[dict] | None = None


class MissingQuestion(Model):
    kind: Literal["question"] = "question"
    id: int = Field(description="Put the answer in answers[id].")
    question: str
    answer_type: Literal["yes_no", "choice", "choices", "text", "unsupported"]
    options: list[str] | None = None


Missing = Annotated[Union[MissingField, MissingQuestion], Field(discriminator="kind")]


class CheckResult(Model):
    ready: bool = Field(description="True only if submit would be accepted.")
    missing: list[Missing] = Field(description="Ask the user these, in these words. Never guess.")
    problems: list[str] = Field(description="Things that are wrong and must be fixed.")
    blocked: list[str] = Field(description="25Live's own save blockers (e.g. lead time).")
    warnings: list[str] = Field(description="Notices the form shows. Pass them on to the user.")
    approvals_needed: list[str] = Field(description="Extra approvals the answers trigger.")
    ignored_answers: list[int] = Field(description="Answers to questions this request doesn't ask.")


class Preview(Model):
    """What submit will send, in plain words. Show it to the user before confirming."""
    name: str
    type: str
    organization: str
    room: str
    layout_id: int
    start: str
    end: str
    headcount: int
    description: str
    answers: dict[str, str] = Field(description="Question text -> answer.")
    requestor: str
    scheduler: str


class SubmitResult(Model):
    status: Literal["not_ready", "preview", "changed", "submitted"] = Field(description=(
        "not_ready: see check. preview: nothing sent; show preview, then submit with confirm. "
        "changed: the preview is stale; show the new one. submitted: done."))
    submitted: bool
    check: CheckResult | None = Field(None, description="Present when not ready.")
    preview: Preview | None = None
    confirm: str | None = Field(None, description="Pass back as --confirm to submit exactly this.")
    event_id: int | None = None
    reference: str | None = Field(None, description="e.g. 2026-ACWQJD")
    state: str | None = Field(None, description="Tentative until a scheduler approves.")
    messages: list[str] = Field(default_factory=list)


class Room(Model):
    space_id: int
    code: str
    name: str
    capacity: int | None
    layout_id: int | None
    free: bool
    can_request: bool
    conflict_until: str | None = None
    fully_scheduled: bool = Field(description=(
        "False for rooms whose code ends in **: not fully scheduled in 25Live, so availability "
        "may be wrong and a scheduler will follow up."))


class Rooms(Model):
    rooms: list[Room]
    total: int  # rooms at that time, before `limit`


class Org(Model):
    org_id: int
    name: str


class Orgs(Model):
    orgs: list[Org]


class EventType(Model):
    type_id: int
    name: str


class EventTypes(Model):
    types: list[EventType]


class Whoami(Model):
    netid: str
    name: str
    contact_id: int
    group: str | None = None


class Status(Model):
    event_id: int
    reference: str | None
    name: str
    state: str | int
    start: str
    end: str


class Copied(Model):
    request: Request
    note: str = "start and end are blank on purpose; agree is false until the user agrees."


class Doctor(Model):
    ok: bool = Field(description="True if cu25 understands everything in the live form.")
    session: Whoami
    unknown_required_fields: list[str]
    unsupported_rules: list[str] = Field(description="Active rules using conditions/actions cu25 can't evaluate.")
    unknown_question_types: list[str]
    counts: dict[str, int]


class LibrarySpace(Model):
    space_id: int
    name: str
    library: str
    category: str
    capacity: int | None
    url: str
    free: bool | None = Field(description="Free for the whole time asked; null when no time was given.")
    free_times: list[str] = Field(description="Free ranges that day, e.g. 14:00-16:30.")


class LibrarySpaces(Model):
    spaces: list[LibrarySpace]
    total: int  # spaces matching, before `limit`


class LibraryPreview(Model):
    """What libbook will book, in plain words. Show it to the user before confirming."""
    space: str
    library: str
    start: str
    end: str
    name: str
    email: str
    answers: dict[str, str | list[str]]


class LibraryResult(Model):
    status: Literal["not_ready", "preview", "changed", "booked"] = Field(description=(
        "not_ready: see missing/problems. preview: nothing booked; show preview, then libbook with "
        "confirm. changed: the preview is stale; show the new one. booked: done."))
    booked: bool
    missing: list[MissingField] = Field(default_factory=list, description="Ask the user these.")
    problems: list[str] = Field(default_factory=list)
    preview: LibraryPreview | None = None
    confirm: str | None = Field(None, description="Pass back as --confirm to book exactly this.")
    booking_id: str | None = None
    messages: list[str] = Field(default_factory=list)


class Error(Model):
    error: str
    error_type: Literal["session_expired", "invalid_input", "upstream", "error"]


# Command name -> output model. The CLI, MCP server, `cu25 schema` and tests all read this.
OUTPUTS: dict[str, type[Model]] = {
    "whoami": Whoami, "types": EventTypes, "orgs": Orgs, "rooms": Rooms,
    "check": CheckResult, "submit": SubmitResult, "copy": Copied, "status": Status,
    "doctor": Doctor, "libspaces": LibrarySpaces, "libbook": LibraryResult, "error": Error,
}
