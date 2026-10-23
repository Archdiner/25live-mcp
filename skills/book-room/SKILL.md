# Requesting a Cornell room with cu25

Every command prints one JSON object. Exit 0 = ok, 1 = error (`error`, `error_type`),
2 = needs attention (not ready, or a preview that must be confirmed again).
`cu25 schema` prints the JSON Schema of every input and output.

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
