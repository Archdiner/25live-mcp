# cu25

Request Cornell rooms in 25Live (Scheduling@Cornell) or book library study spaces from the terminal, or let an agent do it. The agent works out with you which one you need.

```bash
pip install .                                      # add [mcp] for the MCP server
cu25 login                                         # your browser opens: NetID + Duo
cu25 doctor                                        # session ok, live form understood
cu25 rooms 2026-10-29 17:00 18:00 --people 20      # free rooms first
cu25 copy 561543                                   # optional: start from a past event
cu25 check request.json                            # what's still missing, in the form's words
cu25 submit request.json                           # preview + confirm code, sends nothing
cu25 submit request.json --confirm 3f9a1c0b2e      # submit exactly what was previewed
```

Library study spaces (Olin, Uris, Catherwood, Mann, Carpenter) need no login and are booked at once under your name and email:

```bash
cu25 libspaces 2026-10-29 17:00 19:00 --people 4   # free spaces first, with each one's free times
cu25 libbook booking.json                          # preview + confirm code, books nothing
cu25 libbook booking.json --confirm 8c1d2e3f4a     # book exactly what was previewed
```

The form's fields, questions, rules and date limits are read from 25Live, so `check` reflects the current form. Requests stay Tentative until a scheduler approves them.

**Agents.** With a shell, use the CLI: `cu25 guide` prints the workflow and `skills/book-room/SKILL.md` is the same guide as a skill. Without a shell, `pip install '.[mcp]'` and add `cu25-mcp` as an MCP server; its tools take and return the same models. `cu25 schema` prints the JSON Schema of every input and output.

`AGENTS.md` has the design, how each part is verified, the 25Live API map and what's left to do. `pip install -e '.[test]' && pytest` runs the tests, including end-to-end runs of both programs against a fake 25Live.
