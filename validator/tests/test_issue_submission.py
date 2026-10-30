import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "extract_record.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("extract_record", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


GAME = {
    "schemaVersion": 1,
    "id": "test-game",
    "status": "active",
    "name": "Test Game",
    "stores": [{
        "store": "steam",
        "primary": True,
        "url": "https://store.steampowered.com/app/12345/Test_Game/",
    }],
    "accessibility": {
        "accessVia": "mod",
        "primaryModId": "test-mod",
    },
}

MOD = {
    "schemaVersion": 1,
    "id": "test-mod",
    "status": "active",
    "gameIds": ["test-game"],
    "name": "Test Mod",
    "homeUrl": "https://example.invalid/test-mod",
    "links": [],
}


def _body(*blocks):
    return "\n\n".join(
        f"### {label}\n\n```json\n{json.dumps(record)}\n```"
        for label, record in blocks
    )


def test_extracts_primary_and_companion_record_blocks():
    script = _load_script()

    records = script._extract_records(_body(
        ("Game record JSON", GAME),
        ("Companion mod record JSON", MOD),
    ))

    assert records == [("games", GAME), ("mods", MOD)]


@pytest.mark.parametrize("whitespace", ["", "\n", "\n\n", " \n\t\n"])
def test_empty_primary_block_does_not_swallow_companion(whitespace):
    script = _load_script()
    body = (
        f"### Game record JSON\n\n```json\n{whitespace}\n```\n\n"
        + _body(("Companion mod record JSON", MOD))
    )

    assert script._extract_records(body) == [("mods", MOD)]


@pytest.mark.parametrize("fence", ["```", "````", "~~~", "~~~~"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_extracts_standard_fences_with_blank_lines(fence, newline):
    script = _load_script()
    body = (
        f"### Game record JSON\n\n{fence}json\n\n"
        f"{json.dumps(GAME)}\n\n{fence}  \n"
    ).replace("\n", newline)

    assert script._extract_records(body) == [("games", GAME)]


@pytest.mark.parametrize("indent", [" ", "  ", "   "])
def test_extracts_records_under_indented_markdown_headings(indent):
    script = _load_script()
    body = indent + _body(("Game record JSON", GAME))

    assert script._extract_records(body) == [("games", GAME)]


@pytest.mark.parametrize("indent", [" ", "  ", "   "])
def test_indented_notes_heading_terminates_record_section(indent, capsys):
    script = _load_script()
    body = (
        f"### Game record JSON\n\n```json\n{json.dumps(GAME)}\n\n"
        f"{indent}### Supporting links or notes\n\n```json\n{{}}\n```\n"
    )

    with pytest.raises(SystemExit) as error:
        script._extract_records(body)

    assert error.value.code == 1
    assert "missing its closing code fence" in capsys.readouterr().out


@pytest.mark.parametrize("following", [
    "",
    "\n### Supporting links or notes\n\n```json\n{}\n```\n",
    "\n```not-a-closing-fence\n",
    "\n``\n",
    "\n~~~\n",
])
def test_unclosed_record_fence_fails_in_its_own_section(following, capsys):
    script = _load_script()
    body = f"### Game record JSON\n\n```json\n{json.dumps(GAME)}\n{following}"

    with pytest.raises(SystemExit) as error:
        script._extract_records(body)

    assert error.value.code == 1
    diagnostic = capsys.readouterr().out
    assert "Game record JSON" in diagnostic
    assert "missing its closing code fence" in diagnostic


def test_prose_and_unrelated_json_blocks_are_left_for_curator():
    script = _load_script()
    body = (
        "### Game record JSON\n\n_No response_\n\n"
        "### Supporting links or notes\n\n```json\n{}\n```\n"
    )

    assert script._extract_records(body) == []


@pytest.mark.parametrize("raw", [
    '{"id": "test-game",}',
    '[]',
    '{"id": "../test-game"}',
    '{"id": 3}',
])
def test_invalid_records_are_rejected(raw):
    script = _load_script()
    body = f"### Game record JSON\n\n```json\n{raw}\n```"

    with pytest.raises(SystemExit) as error:
        script._extract_records(body)

    assert error.value.code == 1


def test_issue_submission_stages_every_record_before_validation(
        tmp_path, monkeypatch):
    script = _load_script()
    (tmp_path / "registry" / "games").mkdir(parents=True)
    (tmp_path / "registry" / "mods").mkdir()
    root = tmp_path / "root"
    base = tmp_path / "base"
    created = iter((str(root), str(base)))
    seen = {}

    def validate(arguments, capture_output, text):
        assert arguments[:4] == [
            "uv", "run", "blindgaming-registry", "validate"]
        seen["game"] = (
            root / "registry" / "games" / "test-game.json").exists()
        seen["mod"] = (
            root / "registry" / "mods" / "test-mod.json").exists()
        return subprocess.CompletedProcess(arguments, 0, "ok\n", "")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUE_BODY", _body(
        ("Mod record JSON", MOD),
        ("Companion game record JSON", GAME),
    ))
    monkeypatch.setattr(script.tempfile, "mkdtemp", lambda: next(created))
    monkeypatch.setattr(script.subprocess, "run", validate)

    assert script.main() == 0
    assert seen == {"game": True, "mod": True}


def test_duplicate_record_ids_are_rejected(monkeypatch):
    script = _load_script()
    duplicate = {**GAME, "name": "Duplicate"}

    monkeypatch.setenv("ISSUE_BODY", _body(
        ("Game record JSON", GAME),
        ("Companion game record JSON", duplicate),
    ))

    assert script.main() == 1
