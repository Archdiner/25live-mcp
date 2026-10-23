"""How an agent should drive cu25. Printed by `cu25 guide`, sent as the MCP server's
instructions, and mirrored in skills/book-room/SKILL.md (a test keeps them in sync)."""

GUIDE = """\
# Booking a Cornell room with cu25

Every command prints one JSON object. Exit 0 = ok, 1 = error (`error`, `error_type`),
2 = needs attention (not ready, or a preview that must be confirmed again).
`cu25 schema` prints the JSON Schema of every input and output.

## First: library space or 25Live?

Two systems, two sets of commands. Work out which one with the user before anything else:

- **Library study space** (`libspaces`, `libbook`): studying or working in a small group in
  Olin, Uris, Catherwood, Mann or Carpenter Hall. Group study rooms, tables and classrooms,
  mostly 1-10 people. A few hours at most. Booked at once under the person's own name and
  @cornell.edu email. No login, no organization, no approval; it can be today.
- **25Live** (`rooms` ... `submit`): an event for an organization (club meeting, talk, info
  session, anything with food or guests) in a campus classroom or event space. Needs
  `cu25 login`, an org, the event form's questions, and 2+ days' notice; it stays Tentative
  until a scheduler approves.

If the message doesn't settle it, ask one question: "Is this for studying or a small group
in a library, or an event for an organization?" A library space for an org event, or a
25Live room to study in, is the wrong tool; say so.

## 25Live

1. **When.** Get the date, start and end (local Ithaca time). Requests for today or tomorrow
   won't be processed in time; the day after tomorrow is fine. Say so before anything else.
2. **Where.** `cu25 rooms YYYY-MM-DD HH:MM HH:MM [--people N] [--name building]`. Show the free
   rooms (name, capacity) and let the user pick, unless they already named the room. Say so
   when a room has `fully_scheduled: false` (its availability may be wrong).
3. **Start a request.** Write request JSON (shape: `cu25 schema request`). Use
   `cu25 orgs "<name>"` for `org_id` and `cu25 types` for `type_id`. If the user says "same as
   last time", `cu25 copy <event_id>` gives a starting request; fill in `start`/`end`.
4. **Check.** `cu25 check request.json`. Ask the user every item in `missing`, using its
   `question` and `options`, grouped into one message. Never invent answers to activity
   questions (food, alcohol, minors, outdoors, speakers...). Put answers in `answers` by `id`,
   then check again: answers can unlock follow-up questions. Repeat until `ready` is true.
5. **Respect what check says.** `problems` and `blocked` mean it can't be submitted; explain why.
   Pass every `warnings` item on to the user. `approvals_needed` means use the web form for now.
6. **Agree.** Set `agree: true` only after the user has read and accepted the policy statement
   (the `agree` item's `help` text).
7. **Preview.** `cu25 submit request.json` sends nothing: it returns `preview` and a `confirm`
   code. Show the preview to the user and get an explicit yes.
8. **Submit.** `cu25 submit request.json --confirm <code>`. If `status` is `changed`, show the
   new preview and confirm again. Report the `reference` (e.g. 2026-ACWQJD) and that it stays
   Tentative until a scheduler approves (up to 5 business days).

If `error_type` is `session_expired`, ask the user to run `cu25 login` (a browser opens for
NetID + Duo). If it is `upstream`, 25Live changed: stop and tell the user.

## Library study space

1. **When and how many.** The date, start, end (Ithaca time) and how many people.
2. **Where.** `cu25 libspaces YYYY-MM-DD HH:MM HH:MM [--people N] [--name olin]` lists spaces,
   free ones first, with each space's `free_times` that day. Without the times it shows the
   day's free times, to help pick a time. Show the free ones (name, library, capacity) and
   let the user pick. Times start on the space's slot boundaries (often :00/:15/:30/:45).
3. **Who.** The booking is in the user's own name: ask for their first name, last name and
   @cornell.edu email unless you already know them. Never book under someone else's name.
4. **Preview.** Write booking JSON (`cu25 schema librequest`) and run `cu25 libbook
   booking.json`. Nothing is held or booked. Ask for every item in `missing`; explain every
   item in `problems` (taken, too long: it names the latest end allowed). Show `preview`
   and get an explicit yes.
5. **Book.** `cu25 libbook booking.json --confirm <code>`. This is when the library's own form
   is read: if it comes back `not_ready` with `missing` items named `answers.<field>`, ask
   them, put the answers in `answers` under that field, and preview again. Report
   `booking_id` and `messages`: the library emails a confirmation to that address.
   Cancelling is done from that email.

If `error_type` is `upstream`, the library's site changed: stop and tell the user.
"""
