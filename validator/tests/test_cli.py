import json
import os
import shutil
from pathlib import Path

import pytest

import blindgaming_registry_validator.cli as cli
from blindgaming_registry_validator.cli import main
from blindgaming_registry_validator.formatting import canonical_guide_bytes, canonical_record_bytes


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "valid"


def _registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    shutil.copytree(FIXTURE_ROOT / "registry", root / "registry")
    return root


def test_version_command_reports_the_version_1_public_contract(capsys):
    assert main(["version"]) == 0

    assert capsys.readouterr().out == "blindgaming registry validator 1.2.1\n"


def test_validate_prints_plain_linear_warning_diagnostics_without_failing(tmp_path, capsys):
    root = _registry_root(tmp_path)
    source = root / "registry" / "games" / "example-game.json"
    record = json.loads(source.read_text(encoding="utf-8"))
    record["id"] = "other-example-game"
    record["stores"][0]["url"] = (
        "https://store.steampowered.com/app/1234561/Other_Example_Game/"
    )
    record["accessibility"]["accessVia"] = "native"
    record["accessibility"].pop("primaryModId")
    (root / "registry" / "games" / "other-example-game.json").write_bytes(
        canonical_record_bytes(record, "game")
    )

    assert main(["validate", "--root", str(root)]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "WARNING registry/games/other-example-game.json /name: title similarity warning: "
        "game example-game (steam:1234560) resembles game other-example-game "
        "(steam:1234561)\n"
    )
    assert "\x1b" not in captured.out
    assert "Traceback" not in captured.out


def test_validate_returns_one_and_prints_a_plain_error_for_invalid_registry_data(tmp_path, capsys):
    root = _registry_root(tmp_path)
    path = root / "registry" / "games" / "example-game.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["id"] = "wrong-id"
    path.write_bytes(canonical_record_bytes(record, "game"))

    assert main(["validate", "--root", str(root)]) == 1

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "ERROR registry/games/example-game.json /id: filename stem must match record id\n"
    )
    assert "\x1b" not in captured.out
    assert "Traceback" not in captured.out


def test_validate_returns_two_for_an_unreadable_root(tmp_path, capsys):
    missing_root = tmp_path / "missing"

    assert main(["validate", "--root", str(missing_root)]) == 2

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == f"ERROR . : root directory does not exist: {missing_root}\n"
    assert "Traceback" not in captured.out


def test_format_checks_then_rewrites_noncanonical_records_only_when_explicitly_requested(tmp_path, capsys):
    root = _registry_root(tmp_path)
    path = root / "registry" / "games" / "example-game.json"
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("  ", "    ", 1), encoding="utf-8")

    assert main(["format", "--root", str(root), "--check"]) == 1
    check = capsys.readouterr()
    assert check.out == (
        "ERROR registry/games/example-game.json : record is not canonically formatted\n"
    )
    assert path.read_text(encoding="utf-8") != original

    assert main(["format", "--root", str(root)]) == 0
    assert capsys.readouterr().out == ""
    assert path.read_text(encoding="utf-8") == original

    assert main(["format", "--root", str(root), "--check"]) == 0
    assert capsys.readouterr().out == ""


def test_format_refuses_a_symlinked_record_without_changing_its_external_target(tmp_path, capsys):
    root = _registry_root(tmp_path)
    external = tmp_path / "external.json"
    original = (root / "registry" / "games" / "example-game.json").read_text(encoding="utf-8")
    external.write_text(original.replace("  ", "    ", 1), encoding="utf-8")
    record = root / "registry" / "games" / "example-game.json"
    record.unlink()
    record.symlink_to(external)

    assert main(["format", "--root", str(root)]) == 2

    captured = capsys.readouterr()
    assert "symbolic links are not permitted" in captured.out
    assert external.read_text(encoding="utf-8") != original


def test_format_refuses_a_symlinked_root_without_changing_its_target(tmp_path, capsys):
    target = _registry_root(tmp_path)
    path = target / "registry" / "games" / "example-game.json"
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("  ", "    ", 1), encoding="utf-8")
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(target, target_is_directory=True)

    assert main(["format", "--root", str(linked_root)]) == 2

    captured = capsys.readouterr()
    assert captured.out == "ERROR . : root directory must not be a symlink\n"
    assert path.read_text(encoding="utf-8") != original


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    [
        (
            lambda source: source.replace(
                '  "id": "example-game",',
                '  "id": "example-game",\n  "id": "other-id",',
            ),
            "duplicate JSON object key",
        ),
        (
            lambda source: source.replace(
                '    "tags": [\n      "blind"\n    ],',
                '    "tags": "not-an-array",',
            ),
            "'not-an-array' is not of type 'array'",
        ),
    ],
    ids=["duplicate-key", "wrong-array-type"],
)
def test_format_rejects_malformed_records_without_mutating_them(
    tmp_path, capsys, mutation, expected_message
):
    root = _registry_root(tmp_path)
    path = root / "registry" / "games" / "example-game.json"
    malformed = mutation(path.read_text(encoding="utf-8"))
    path.write_text(malformed, encoding="utf-8")

    assert main(["format", "--root", str(root)]) == 2

    captured = capsys.readouterr()
    assert expected_message in captured.out
    assert path.read_text(encoding="utf-8") == malformed


def test_format_restores_every_record_after_a_later_atomic_replacement_failure(
    tmp_path, capsys, monkeypatch
):
    root = _registry_root(tmp_path)
    game = root / "registry" / "games" / "example-game.json"
    mod = root / "registry" / "mods" / "example-accessibility-mod.json"
    for path in (game, mod):
        path.write_text(path.read_text(encoding="utf-8").replace("  ", "    ", 1), encoding="utf-8")
    before = {path: path.read_bytes() for path in (game, mod)}
    original_replace = cli.os.replace
    replacement_attempts = 0

    def fail_second_canonical_replacement(source: str | Path, destination: str | Path) -> None:
        nonlocal replacement_attempts
        if str(source).endswith(".registry-format"):
            replacement_attempts += 1
            if replacement_attempts == 2:
                raise OSError("simulated replacement failure")
        original_replace(source, destination)

    monkeypatch.setattr(cli.os, "replace", fail_second_canonical_replacement)

    assert main(["format", "--root", str(root)]) == 2

    captured = capsys.readouterr()
    assert "simulated replacement failure" in captured.out
    assert {path: path.read_bytes() for path in (game, mod)} == before
    assert not list(root.rglob("*.registry-format"))
    assert not list(root.rglob("*.registry-original"))


def test_format_cleans_a_staged_backup_when_canonical_staging_fails(
    tmp_path, capsys, monkeypatch
):
    root = _registry_root(tmp_path)
    path = root / "registry" / "games" / "example-game.json"
    path.write_text(path.read_text(encoding="utf-8").replace("  ", "    ", 1), encoding="utf-8")
    original = path.read_bytes()
    stage_bytes = cli._stage_format_bytes

    def fail_canonical_staging(path: Path, contents: bytes, suffix: str) -> Path:
        if suffix == ".registry-format":
            raise OSError("simulated canonical staging failure")
        return stage_bytes(path, contents, suffix)

    monkeypatch.setattr(cli, "_stage_format_bytes", fail_canonical_staging)

    assert main(["format", "--root", str(root)]) == 2

    captured = capsys.readouterr()
    assert "simulated canonical staging failure" in captured.out
    assert path.read_bytes() == original
    assert not list(root.rglob("*.registry-format"))
    assert not list(root.rglob("*.registry-original"))


def test_format_retains_recovery_backup_and_reports_a_rollback_failure(
    tmp_path, capsys, monkeypatch
):
    root = _registry_root(tmp_path)
    game = root / "registry" / "games" / "example-game.json"
    mod = root / "registry" / "mods" / "example-accessibility-mod.json"
    for path in (game, mod):
        path.write_text(path.read_text(encoding="utf-8").replace("  ", "    ", 1), encoding="utf-8")
    before = {path: path.read_bytes() for path in (game, mod)}
    original_replace = cli.os.replace
    canonical_replacements = 0

    def fail_apply_then_rollback(source: str | Path, destination: str | Path) -> None:
        nonlocal canonical_replacements
        source_name = str(source)
        if source_name.endswith(".registry-format"):
            canonical_replacements += 1
            if canonical_replacements == 2:
                raise OSError("simulated apply failure")
        if source_name.endswith(".registry-original"):
            raise OSError("simulated rollback failure")
        original_replace(source, destination)

    monkeypatch.setattr(cli.os, "replace", fail_apply_then_rollback)

    assert main(["format", "--root", str(root)]) == 2

    captured = capsys.readouterr()
    backups = list(root.rglob("*.registry-original"))
    assert len(backups) == 1
    assert "simulated apply failure" in captured.out
    assert "simulated rollback failure" in captured.out
    assert backups[0].relative_to(root).as_posix() in captured.out
    assert backups[0].read_bytes() == before[game]
    assert game.read_bytes() != before[game]
    assert mod.read_bytes() == before[mod]
    assert not list(root.rglob("*.registry-format"))


def test_format_reports_retained_recovery_backup_for_a_relative_root(
    tmp_path, capsys, monkeypatch
):
    root = _registry_root(tmp_path)
    game = root / "registry" / "games" / "example-game.json"
    mod = root / "registry" / "mods" / "example-accessibility-mod.json"
    for path in (game, mod):
        path.write_text(path.read_text(encoding="utf-8").replace("  ", "    ", 1), encoding="utf-8")
    before = game.read_bytes()
    original_replace = cli.os.replace
    canonical_replacements = 0

    def fail_apply_then_rollback(source: str | Path, destination: str | Path) -> None:
        nonlocal canonical_replacements
        source_name = str(source)
        if source_name.endswith(".registry-format"):
            canonical_replacements += 1
            if canonical_replacements == 2:
                raise OSError("simulated apply failure")
        if source_name.endswith(".registry-original"):
            raise OSError("simulated rollback failure")
        original_replace(source, destination)

    monkeypatch.setattr(cli.os, "replace", fail_apply_then_rollback)
    monkeypatch.chdir(root)

    assert main(["format", "--root", "."]) == 2

    captured = capsys.readouterr()
    backups = list(root.rglob("*.registry-original"))
    assert len(backups) == 1
    assert "simulated apply failure" in captured.out
    assert "simulated rollback failure" in captured.out
    assert backups[0].relative_to(root).as_posix() in captured.out
    assert game.read_bytes() != before
    assert not list(root.rglob("*.registry-format"))


def test_format_rejects_unpaired_surrogates_without_a_traceback(tmp_path, capsys):
    root = _registry_root(tmp_path)
    path = root / "registry" / "games" / "example-game.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Example Game", r"\ud800"),
        encoding="utf-8",
    )

    assert main(["format", "--root", str(root)]) == 2

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "ERROR registry/games/example-game.json /name: "
        "text must contain only Unicode scalar values\n"
    )
    assert "Traceback" not in captured.out
    assert "\ud800" not in captured.out


def test_validate_classifies_an_actual_record_read_failure_as_cli_io(tmp_path, capsys):
    root = _registry_root(tmp_path)
    path = root / "registry" / "games" / "example-game.json"
    path.chmod(0)
    try:
        try:
            path.read_bytes()
        except PermissionError:
            pass
        else:
            pytest.skip("this platform does not enforce file mode permissions for this test user")

        assert main(["validate", "--root", str(root)]) == 2
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out.startswith("ERROR registry/games/example-game.json :")
    finally:
        path.chmod(0o644)


def test_validate_keeps_io_exit_status_when_fallback_also_reports_invalid_json(tmp_path, capsys):
    root = _registry_root(tmp_path)
    (root / "registry" / "games" / "a-invalid.json").write_text("{\n", encoding="utf-8")
    unreadable = root / "registry" / "games" / "z-unreadable.json"
    unreadable.write_bytes(
        (root / "registry" / "games" / "example-game.json").read_bytes()
    )
    unreadable.chmod(0)
    try:
        try:
            unreadable.read_bytes()
        except PermissionError:
            pass
        else:
            pytest.skip("this platform does not enforce file mode permissions for this test user")

        assert main(["validate", "--root", str(root)]) == 2

        captured = capsys.readouterr()
        assert "ERROR registry/games/a-invalid.json : invalid JSON\n" in captured.out
        assert "ERROR registry/games/z-unreadable.json :" in captured.out
    finally:
        unreadable.chmod(0o644)


def test_validate_escapes_control_characters_in_untrusted_diagnostic_paths(tmp_path, capsys):
    root = _registry_root(tmp_path)
    try:
        (root / "unexpected\x1b[31m.txt").write_text(
            "untrusted\n", encoding="utf-8"
        )
    except OSError:
        pytest.skip("this filesystem does not allow control characters in names")

    assert main(["validate", "--root", str(root)]) == 1

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == "ERROR unexpected\\x1b[31m.txt : unexpected repository path\n"
    assert "\x1b" not in captured.out
    assert "\n" not in captured.out.removesuffix("\n")


def test_validate_defaults_to_the_current_directory(tmp_path, capsys, monkeypatch):
    root = _registry_root(tmp_path)
    monkeypatch.chdir(root)

    assert main(["validate"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_parser_failures_use_one_plain_diagnostic_line(capsys):
    assert main(["validate", "--base"]) == 2

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == "ERROR . : argument --base: expected one argument\n"


def test_format_checks_then_rewrites_a_noncanonical_guide_only_when_requested(
    tmp_path, capsys
):
    root = _registry_root(tmp_path)
    path = root / "registry" / "guides" / "screen-reader-basics.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = canonical_guide_bytes({"title": "Screen reader basics"}, "Body text.")
    path.write_bytes(original + b"\n")  # extra trailing blank line

    assert main(["format", "--root", str(root), "--check"]) == 1
    check = capsys.readouterr()
    assert check.out == (
        "ERROR registry/guides/screen-reader-basics.md : "
        "record is not canonically formatted\n"
    )
    assert path.read_bytes() != original

    assert main(["format", "--root", str(root)]) == 0
    assert capsys.readouterr().out == ""
    assert path.read_bytes() == original

    assert main(["format", "--root", str(root), "--check"]) == 0
    assert capsys.readouterr().out == ""


def test_format_rejects_a_malformed_guide_without_mutating_it(tmp_path, capsys):
    root = _registry_root(tmp_path)
    path = root / "registry" / "guides" / "broken-guide.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b"title: no fence\n\nBody.\n"
    path.write_bytes(original)

    assert main(["format", "--root", str(root), "--check"]) == 2

    captured = capsys.readouterr()
    assert captured.out == (
        "ERROR registry/guides/broken-guide.md : "
        "guide must begin with a '---' frontmatter block\n"
    )
    assert path.read_bytes() == original
