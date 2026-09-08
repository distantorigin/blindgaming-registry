import importlib.util
import json
import subprocess
from pathlib import Path


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
