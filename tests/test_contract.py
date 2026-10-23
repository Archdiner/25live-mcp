"""The contract: every input and output has a schema, every command honours it,
the CLI and MCP faces expose the same operations, and the docs agree with the code."""
import json
import re
from pathlib import Path

import pytest
from conftest import edit

from cu25 import cli
from cu25.guide import GUIDE
from cu25.models import OUTPUTS, Request

ROOT = Path(__file__).parent.parent


@pytest.fixture
def cli_env(fake, monkeypatch):
    c, _ = fake
    monkeypatch.setattr("cu25.client.Client", lambda *a, **k: c)
    return c


def run(argv, capsys):
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def commands(req_json):
    return {
        "whoami": ["whoami"], "types": ["types"], "orgs": ["orgs", "generative"],
        "rooms": ["rooms", "2026-10-25", "14:00", "15:00", "--name", "stimson"],
        "check": ["check", req_json], "submit": ["submit", req_json],
        "copy": ["copy", "561543"], "status": ["status", "561543"], "doctor": ["doctor"],
    }


def test_every_command_output_matches_its_schema_and_exit_code(cli_env, real_request, capsys):
    for name, argv in commands(real_request.model_dump_json()).items():
        code, out = run(argv, capsys)
        OUTPUTS[name].model_validate(out)  # raises if the output strays from the contract
        assert code == 0, (name, out)


def test_not_ready_exits_2_and_says_why(cli_env, real_request, capsys):
    code, out = run(["check", edit(real_request, agree=False).model_dump_json()], capsys)
    assert code == 2 and out["missing"][0]["field"] == "agree"
    code, out = run(["submit", edit(real_request, agree=False).model_dump_json()], capsys)
    assert code == 2 and out["status"] == "not_ready"


def test_preview_then_confirm_through_the_cli(cli_env, real_request, capsys):
    req = real_request.model_dump_json()
    _, prev = run(["submit", req], capsys)
    code, out = run(["submit", req, "--confirm", prev["confirm"]], capsys)
    assert code == 0 and out["status"] == "submitted" and out["reference"] == "2026-ACWQJD"
    code, out = run(["submit", req, "--confirm", "stale"], capsys)
    assert code == 2 and out["status"] == "changed"


@pytest.mark.parametrize("bad, where", [
    ('{"headcount": 0}', "headcount"),
    ('{"start": "next tuesday"}', "start"),
    ('{"start": "2026-10-25T14:00:00-04:00"}', "start"),
    ('{"answers": {"abc": true}}', "answers"),
    ('{"room": 165}', "room"),
    ('not json', "request"),
])
def test_bad_input_is_rejected_with_the_offending_field(cli_env, bad, where, capsys):
    code, out = run(["check", bad], capsys)
    assert code == 1 and out["error_type"] == "invalid_input" and where in out["error"]


def test_bad_dates_for_rooms_are_input_errors(cli_env, capsys):
    code, out = run(["rooms", "tomorrow", "5pm", "6pm"], capsys)
    assert code == 1 and out["error_type"] == "invalid_input"


def test_schema_command_covers_every_input_and_output(capsys):
    code, out = run(["schema"], capsys)
    assert code == 0 and set(out) == {"request", *OUTPUTS}
    assert out["request"]["additionalProperties"] is False
    code, out = run(["schema", "submit"], capsys)
    assert "confirm" in out["properties"]


def test_request_schema_round_trips():
    req = Request(name="x", answers={249: "Ithaca Campus", 176: False, 327: ["None of the Above"]})
    assert Request.model_validate_json(req.model_dump_json()) == req


def test_cli_and_mcp_expose_the_same_operations():
    import asyncio
    from cu25.mcp_server import mcp
    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    cli_cmds = set(re.findall(r"^  cu25 (\w+)", cli.__doc__, re.M)) - {"schema", "guide"}
    assert tools == cli_cmds


def test_mcp_tool_schemas_are_the_shared_models():
    import asyncio
    from cu25.mcp_server import mcp
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    req_schema = json.dumps(tools["check"].input_schema)
    assert all(k in req_schema for k in Request.model_fields)
    assert tools["submit"].annotations.read_only_hint is False
    assert tools["check"].annotations.read_only_hint is True
    assert set(tools["submit"].output_schema["properties"]) == set(OUTPUTS["submit"].model_fields)


def test_skill_file_is_the_guide():
    skill = (ROOT / "skills" / "book-room" / "SKILL.md").read_text()
    assert GUIDE.strip() in skill


def test_documented_form_fields_match_the_code():
    from cu25.request import FIELD_KEYS
    agents = (ROOT / "AGENTS.md").read_text()
    for name in FIELD_KEYS:
        assert f"| {name} |" in agents, name


def test_runtime_stays_lean():
    import tomllib
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert [d.split(">")[0] for d in meta["dependencies"]] == ["httpx", "pydantic"]
    core = "".join((ROOT / "cu25" / f).read_text() for f in
                   ("cli.py", "client.py", "models.py", "request.py", "rules.py", "payload.py", "login.py"))
    assert "import mcp" not in core and "from mcp" not in core


def test_no_unused_code():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "pyflakes", "cu25", "tests"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
