# cu25: agent-friendly room requests for Cornell's 25Live

A CLI and an MCP server over one client, so an agent can find rooms, work out what the event request form needs, ask the user for anything missing, and submit the request. 25Live calls are replays of what the 25Live Pro web app sends. Requests always land as **Tentative** until a human scheduler approves.

It also books **library study spaces** (LibCal at spaces.library.cornell.edu: Olin, Uris, Catherwood, Mann, Carpenter Hall) with `libspaces` and `libbook`. Those are booked at once under the user's own name and email, with no login and no approval. The guide's first step is working out with the user which system fits: studying or a small group goes to the library, an organization's event goes to 25Live.

## Principles

- **Nothing about the form is hard-coded.** Fields, questions, options, conditional rules, save blockers, allowed event types and date limits come from 25Live (cached for an hour, always refetched before a real submit). A required field or rule cu25 doesn't understand fails `check` loudly, never silently.
- **The agent asks, it doesn't guess.** `check` returns what's missing in the form's own words and options.
- **Nothing is sent without an explicit confirmation of exactly what will be sent.** `submit` without `--confirm` only previews and returns a code (a hash of the exact body). `submit --confirm CODE` refetches the live form, rebuilds the body and sends it only if the hash still matches.
- **Every input and output is a pydantic model** (`cu25/models.py`). The CLI prints them, the MCP tools are typed with them, `cu25 schema` prints their JSON Schemas, and the tests validate every command's output against them.
- No server, no stored passwords. It rides the user's own session cookie from `cu25 login`.
- **Small and fast.** Runtime dependencies are httpx and pydantic only (~13 MB). MCP is an extra. Login drives the user's own browser over the DevTools protocol, with no automation library and no browser download. Independent 25Live calls run in parallel.

## CLI or MCP?

Both, over one `Client`; the CLI is primary. A shell agent gets composability (files, pipes, exit codes) and pays no context for tool schemas; a chat app with no shell needs MCP. Each face is a thin layer over the clients, and `test_cli_and_mcp_expose_the_same_operations` fails if they drift.

## Layout

| File | Job |
| --- | --- |
| `cu25/models.py` | The contract: every input and output, and `OUTPUTS` (command -> model) |
| `cu25/client.py` | HTTP transport, caching, room search, live form schema, check/submit orchestration, `doctor` |
| `cu25/request.py` | `Schema` and `check()`: fields, answers, limits (pure, no network) |
| `cu25/rules.py` | Evaluates 25Live's rule trees; declares the supported rule vocabulary (pure) |
| `cu25/payload.py` | Builds and parses 25Live request bodies (pure) |
| `cu25/libcal.py` | Reads LibCal pages and builds LibCal request bodies (pure) |
| `cu25/library.py` | LibCal HTTP: space catalog (cached an hour), availability grid, the booking flow |
| `cu25/guide.py` | The agent workflow (`cu25 guide`, MCP instructions, mirrored in the skill) |
| `cu25/cli.py`, `cu25/mcp_server.py` | Thin faces over `Client` |
| `cu25/login.py` | One-time login in the user's own browser (SSO + Duo), cookies read over DevTools |
| `tests/fake25live.py` | A fake 25Live and a fake LibCal replaying captured responses, in-process or as a real HTTP server |
| `tests/fixtures/` | Sanitized real responses from captured sessions (Oct 2026); `lib_*` are LibCal |

## How it's verified

| Layer | How |
| --- | --- |
| Inputs | `Request` forbids unknown keys, checks types, rejects datetimes with a UTC offset and headcount < 1. Bad input exits 1 with `error_type: invalid_input` naming the field. |
| Outputs | Every CLI command's JSON validates against its model (`test_every_command_output_matches_its_schema_and_exit_code`). MCP output schemas are generated from the same models. |
| Request body | `test_copy_check_preview_confirm_reproduces_the_real_browser_submission`: copy a real event, check, preview, confirm, and the body sent equals what Chrome sent, field for field. The call order equals the browser's. |
| Form logic | The engine derives exactly the 11 questions the real form showed, food = yes unlocks 177 and 271, lead-time blockers fire, every live rule is in the supported vocabulary. |
| 25Live responses | Parsers run through `Client._parse`: a changed shape raises `UpstreamChanged` (`error_type: upstream`) instead of a stack trace. |
| The live form today | `cu25 doctor` checks the session and that every required field, question type and rule in the live form is understood. Exit 2 if not. |
| End to end | `tests/test_e2e.py` runs the installed `cu25` and `cu25-mcp` as separate processes against the fake over HTTP: rooms -> check -> answer -> check -> preview -> confirm -> status. Login drives a real headless Chromium against the fake and checks the saved cookie. |
| Library booking | `test_preview_then_confirm_reproduces_the_real_browser_booking`: the add, times and book bodies equal what Chrome sent, field for field, in the browser's order. `test_a_shorter_booking_picks_the_end_time_as_the_live_run_did` pins the calls of a real booking made with cu25, including the end-time update. Preview sends only the grid and `booking/add` (nothing held). The fake LibCal rejects POSTs without a same-site Referer, as the real one does. |
| Real traffic | `CU25_RECORD=dir cu25 ...` saves every request and response, to turn a live run into fixtures. |

Exit codes: 0 ok, 1 error (`{"error", "error_type"}`), 2 needs attention (`check` not ready, `submit` status `not_ready` or `changed`, `doctor` not ok).

## Performance

| Step | 25Live calls | Round-trip waves |
| --- | --- | --- |
| `check`, form cache cold | 11 | 3 (7 form parts in parallel, root questions, then org/room/availability in parallel) |
| `check`, form cache warm (1 hour) | 3 | 1 |
| `submit` preview | 5 | 2 |
| `submit --confirm` | 18 | 5 (refetches the form on purpose, then the 5-step submit) |
| `rooms` | 2 | 2 |
| `libspaces`, catalog cold | 4 libraries -> ~19 pages + 1 grid per category | 4 |
| `libspaces`, catalog warm (1 hour) | 1 grid per category | 1 |
| `libbook` preview | 2-3 (grid, add, update if the end isn't LibCal's default) | 2-3 |
| `libbook --confirm` | 4-5 (the above, then times, book) | 4-5 |

CLI startup is about 180 ms, almost all of it pydantic; models use `defer_build`, so each command builds only the models it touches.

## 25Live API (verified from a HAR)

Base `https://25live.collegenet.com/25live/data/cornell/run/` (override with `CU25_BASE`). Every call adds `caller=pro`. Auth is cookies only (no CSRF header). An expired session returns a redirect or HTML, which surfaces as `session_expired`.

| Purpose | Call |
| --- | --- |
| Who am I | `GET login.json` (`success`, `username`, `user_id` = contact id) |
| Allowed types and dates | `GET usergroup/wizardsecurity.json` (`event_types_allowed`, `event_create_min_date`, `event_create_max_date`) |
| Room search | `GET spaces.json?scope=extended&can_schedule=T&perm_start_dt&perm_end_dt&include=layouts&min_default_capacity&name&page&page_size&paginate` |
| Room availability | `POST space_avail.json` `{space_availability:{requirements:{space:[{space_id}]...},dates:[{start_dt,end_dt}]}}` |
| Room scheduler + layouts | `POST spaces.json?request_method=get` `{mapxml:{space_id,scope:"extended",include:"layouts+contacts+direct_schedule"}}` |
| Form fields | `GET wizard/config.json?mode=create`: `config.item[]` = `item_name`, `required`, `visible`, `item_display`, `item_message` (help HTML) |
| Form rules | `GET rule/trees.json?category_id=form` (164 rules, 144 active) |
| Save blockers | `GET rule/trees.json?category_id=formValidation` |
| All questions | `GET search-criteria/customattributes.json?itemTypeId=1&all_types=F`: id, text, type `B`/`S`/`X`/`R`, `multi_val` |
| Root questions | same with `itemTypeId=11&event_type_id=X` (Cornell: just "Which Campus?") |
| Option lists | `GET custom/attribute/options.json` |
| Event types | `GET search-criteria/event/types.json?active=T&all_types=F&form_config=T` |
| Org search | `GET search-criteria/event/organizations.json?name=...` |
| Org in saved search | `GET organizations.json?scope=list&query_id=Q&organization_id=O` (non-empty = yes) |
| Event detail | `GET micro/event/{id}/detail.json?expand=T&include=all` |
| **Submit, in order** | `GET sequence/id.json?itemName=events` -> `GET sequence/id.json?itemName=date_profile` -> `PUT event.json?prime` (presave, state -1) -> `POST micro/sequence/generate.json?element=reservation&quantity=1` -> `PUT rose/event.json?event_id=X&return_doc=T` (state 1) |

## LibCal API (library spaces, verified from a HAR)

Base `https://spaces.library.cornell.edu` (override with `CU25_LIBCAL`). No login and no cookies needed. Every POST needs a same-site `Referer` (else 403 "Invalid Referrer.") and sends `x-requested-with: XMLHttpRequest`. A refusal is HTTP 400 with a plain-text reason (e.g. "Invalid Checksum."), which `libbook` reports as a problem.

| Purpose | Call |
| --- | --- |
| Libraries | `GET /spaces`: `<select name="lid">` options |
| Categories of a library | `GET /spaces?lid=L`: `<select name="gid">` options (0 = Show All, skipped) |
| Spaces in a category | `GET /spaces?lid=L&gid=G`: `resources.push({eid, title, capacity, grouping, ...})` in the page script |
| Availability | `POST /spaces/availability/grid` `lid, gid, eid, seat=0, seatId=0, zone=0, start=day, end=day+1, pageIndex, pageSize=18`: slots of every space in the category; a slot with a `className` is taken, each has a `checksum` |
| Pick the start | `POST /spaces/availability/booking/add` `add[eid,gid,lid,start,checksum], lid, gid, start, end`: `bookings[0]` with the end times allowed (`options`, `optionChecksums`, default `end`, `checksum`). Nothing held. |
| Pick another end | same, with `update[id,checksum,end]` (the option's own checksum) + `bookings[0][...]`: the booking with that end and a new checksum. Verified in a live booking. |
| Hold the times, get the form | `POST /ajax/space/times` `patron, patronHash, returnUrl, bookings[0][...], method=12`: `{html}` with the form (`session` id, `fname`, `lname`, `email`, any custom questions) and the email domain rule. Holds the slot a few minutes. |
| **Book** | `POST /ajax/space/book` multipart: `session, fname, lname, email, [answers], bookings (JSON), returnUrl, pickupHolds, method`: `{bookId, html}` ("Booking Confirmed") |

The form is read at confirm time, from `times`. Required fields cu25 doesn't know come back as `missing` items named `answers.<field>`, with their label and options, and nothing is booked. Seat bookings (e.g. Mann's Graduate Study Area) have no `resources` on their page and aren't listed.

## The form (Cornell config "Student Users - General Creation")

| `item_name` | Required | Request key |
| --- | --- | --- |
| name | yes | `name` (help text: avoid `! < > % ~ @ \ ^`, enforced) |
| type | yes | `type_id` (must be in `event_types_allowed`) |
| description_html | no | `description` (plain text, escaped into `<p>`s) |
| sponsor | yes | `org_id` |
| headcount_expected | yes | `headcount` (max at once, not total) |
| datetime | yes | `start`, `end` (within 25Live's live min/max dates) |
| space | yes | `space_id` (+ optional `layout_id`, must be one of the room's) |
| file_attach | no | not supported yet |
| ev_custom_attr | yes | `answers` (rule engine) |
| resource | no | added by rules |
| affirmation | yes | `agree` (the user must actually agree) |

The mapping lives in `request.FIELD_KEYS`; a test keeps this table in sync.

## Questions and rules

Source types: `11` question, `19` event type, `4` room, `1410` headcount, `2` org, `205` org in saved search, `1001` time of day (decimal hours), `1003` start date (validation, `After "eventStart,-N"`), `-3` form mode. Actions: `addCustAtrb`, `addRequirement`, `addResource`, `alertUser`, `preventSave`. Anything else is reported by `doctor` and makes `check` not ready.

The engine runs to a fixed point (answers unlock questions that unlock more). Only answers to questions the form is currently asking count, as in the web form; other answers are listed in `ignored_answers` and not sent.

Semantics chosen where 25Live doesn't say:
- Unanswered questions match nothing, not even `not in`. An org in no saved search does match `not in` (the list is known to be empty).
- Time of day (`1001`) matches if any part of the event falls in the window. The safe reading: it can only add approvals, never skip them.
- Options match case-insensitively (Cornell's rules and option lists disagree on capitalisation) and are stored in the option list's spelling.

Answer encoding: yes/no -> `"T"`/`"F"`. Multi-select -> JSON string `[{"item":"..."}]`. Text as-is.

## Not done yet (in priority order)

1. **First live run.** Nothing has hit real 25Live yet. In order: `cu25 login`, `cu25 doctor`, `rooms`, `copy <id>`, `check`, `submit` (preview), then one real `submit --confirm`. Run with `CU25_RECORD=~/cu25-rec` and turn anything that differs into a fixture and a test.
2. **Approvals.** When rules add requirements or resources, `check` is not ready and says to use the web form. On the real rules that's: events touching 11pm-midnight, takeout food for 25+, homemade food, alcohol, athletic activities, decorations, recordings. Plain meetings, store-bought food and small takeout orders go through. Fix: one HAR of such a request, then add the shape to `payload.submit_body`.
3. **Availability without an event id.** The web app sent `event` + `profile` in `space_avail.json`; cu25 omits them. If the live server rejects that, check availability after the presave step.
4. **Additional time** (setup/teardown) and **repeating events** (only `rec_type_id: 0`). One HAR each.
5. **Presave leftovers.** A failure after `PUT event.json?prime` leaves a "Presave: N" draft at state -1; the error says so. Find out whether 25Live cleans these up.
6. "Organization / Location Not Found in List", file upload, edit/cancel.
7. **Library: other forms and refusals.** A real booking through cu25 worked (Oct 23 2026: Catherwood 231A, 15:00-16:00 with a non-default end; the slot then showed taken and a second attempt was refused). Its calls are the `lib_*_3837` / `lib_bodies_live.json` fixtures. Still unseen: a category with custom form questions (Mann conference rooms?) and a refusal at `times`/`book`.
8. **Library: cancel and seat bookings.** Cancelling is done from the confirmation email; seat-based categories aren't listed. One HAR each.

## Conventions

- Keep `payload`, `rules`, `request` and `libcal` free of I/O. Network code lives only in `client.py` and `library.py` (and the browser handshake in `login.py`).
- No new runtime dependency unless the standard library, httpx or pydantic can't do it in a few lines. Optional faces go behind extras.
- Independent 25Live calls go through `parallel()`. Anything fetched twice in one command goes through `Client._get`.
- Every new 25Live or LibCal call gets a sanitized fixture, a route in `tests/fake25live.py` and a test. Every new output gets a model in `models.py` and an entry in `OUTPUTS`.
- One test per behaviour, at the lowest layer that proves it. Don't re-prove at a higher layer what a lower one already pins down.
- Strip NetIDs, names and emails from fixtures. Run `pyflakes cu25 tests` before committing: no unused code.
