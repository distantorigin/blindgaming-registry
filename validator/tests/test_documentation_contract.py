from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from blindgaming_registry_validator.formatting import canonical_record_bytes
from blindgaming_registry_validator.validation import validate_snapshot


ROOT = Path(__file__).parents[2]
LOCAL_VALIDATION_PATTERN = re.compile(
    r"<!-- local-history-validation: -->\n```sh\n(.*?)\n```",
    flags=re.DOTALL,
)
VALIDATOR_DEVELOPMENT_PATTERN = re.compile(
    r"<!-- validator-development-check: -->\n```sh\n(.*?)\n```",
    flags=re.DOTALL,
)


def _documentation_examples() -> dict[str, dict[str, object]]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pattern = re.compile(
        r"<!-- registry-example: (game|mod) -->\n```json\n(.*?)\n```",
        flags=re.DOTALL,
    )
    examples: dict[str, dict[str, object]] = {}
    for kind, contents in pattern.findall(readme):
        parsed = json.loads(contents)
        assert isinstance(parsed, dict)
        assert kind not in examples, f"duplicate {kind} documentation example"
        examples[kind] = parsed
    return examples


def test_documentation_json_examples_are_complete_canonical_registry_snapshot(tmp_path):
    examples = _documentation_examples()
    assert set(examples) == {"game", "mod"}

    for kind, record in examples.items():
        identifier = record["id"]
        assert isinstance(identifier, str)
        destination = tmp_path / "registry" / f"{kind}s" / f"{identifier}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(canonical_record_bytes(record, kind))

    report = validate_snapshot(tmp_path)
    assert report.ok, report.diagnostics


def _repository_with_history(tmp_path, *, reserve_identity: bool) -> tuple[Path, str]:
    repository = tmp_path / "registry"
    shutil.copytree(
        ROOT,
        repository,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".venv",
            ".worktrees",
            "__pycache__",
            "*.pyc",
            ".DS_Store",
        ),
    )
    for command in (
        ["git", "init", "--quiet"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Documentation contract"],
        ["git", "add", "."],
        ["git", "commit", "--quiet", "-m", "documentation"],
        ["git", "branch", "-M", "main"],
    ):
        subprocess.run(command, cwd=repository, check=True)

    fixture_root = ROOT / "validator" / "tests" / "fixtures" / "valid" / "registry"
    game = json.loads((fixture_root / "games" / "example-game.json").read_text())
    mod = json.loads((fixture_root / "mods" / "example-accessibility-mod.json").read_text())
    game_path = repository / "registry" / "games" / "example-game.json"
    mod_path = repository / "registry" / "mods" / "example-accessibility-mod.json"
    game_path.parent.mkdir(parents=True, exist_ok=True)
    mod_path.parent.mkdir(parents=True, exist_ok=True)
    game_path.write_bytes(canonical_record_bytes(game, "game"))
    mod_path.write_bytes(canonical_record_bytes(mod, "mod"))
    subprocess.run(["git", "add", "registry"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "active base"], cwd=repository, check=True)
    base_ref = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, text=True, capture_output=True
    ).stdout.strip()
    remote = tmp_path / "upstream.git"
    subprocess.run(["git", "clone", "--bare", repository, remote], check=True)
    subprocess.run(["git", "remote", "add", "upstream", remote], cwd=repository, check=True)

    archived_game = {
        **game,
        "status": "archived",
        "archivedAt": "2026-08-16",
        "archiveReason": "Superseded after review.",
    }
    if reserve_identity:
        archived_game["reservedStores"] = {"steam": ["1234560"]}
    game_path.write_bytes(canonical_record_bytes(archived_game, "game"))
    subprocess.run(["git", "add", "registry"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "archive with reservation"],
        cwd=repository,
        check=True,
    )
    return repository, base_ref


def _documented_command(pattern: re.Pattern[str]) -> str:
    match = pattern.search((ROOT / ".github" / "MAINTAINING.md").read_text(encoding="utf-8"))
    assert match, "Maintainer documentation must provide the executable command"
    return match.group(1)


def _run_documented_command(repository: Path, command: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    temporary_parent = tmp_path / "temporary"
    temporary_parent.mkdir(exist_ok=True)
    return subprocess.run(
        ["sh", "-c", command],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "TMPDIR": str(temporary_parent)},
    )


def _workspace(repository: Path, tmp_path: Path) -> Path:
    common_dir = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if not Path(common_dir).is_absolute():
        common_dir = str(repository / common_dir)
    checksum = subprocess.run(
        ["cksum"], input=common_dir, check=True, text=True, capture_output=True
    ).stdout.split()[0]
    return tmp_path / "temporary" / f"blindgaming-registry-check-{checksum}"


def _assert_clean(repository: Path, workspace: Path) -> None:
    assert not workspace.exists()
    assert not list(repository.rglob(".venv"))
    assert not list(repository.rglob(".pytest_cache"))
    assert not list(repository.rglob("__pycache__"))
    worktrees = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert str(workspace) not in worktrees


def test_documented_local_history_check_validates_reservation_and_prunes_orphans(tmp_path):
    command = _documented_command(LOCAL_VALIDATION_PATTERN)
    repository, base_ref = _repository_with_history(tmp_path, reserve_identity=True)
    workspace = _workspace(repository, tmp_path)

    result = _run_documented_command(repository, command, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    fetched_base = subprocess.run(
        ["git", "rev-parse", "upstream/main"],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert fetched_base == base_ref
    _assert_clean(repository, workspace)

    workspace.mkdir()
    for name, revision in (
        ("base-validator", base_ref),
        ("base-data", base_ref),
        ("proposed-data", "HEAD"),
    ):
        subprocess.run(
            ["git", "worktree", "add", "--detach", workspace / name, revision],
            cwd=repository,
            check=True,
        )
    (workspace / "uv-environment" / "orphan").mkdir(parents=True)
    sibling = tmp_path / "temporary" / "do-not-remove"
    sibling.mkdir()

    rerun = _run_documented_command(repository, command, tmp_path)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert sibling.is_dir()
    _assert_clean(repository, workspace)


def test_documented_local_history_check_preserves_validation_failure_and_cleans_up(tmp_path):
    command = _documented_command(LOCAL_VALIDATION_PATTERN)
    repository, _ = _repository_with_history(tmp_path, reserve_identity=False)
    workspace = _workspace(repository, tmp_path)

    result = _run_documented_command(repository, command, tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    _assert_clean(repository, workspace)


def test_documented_local_history_check_rejects_failed_fetch_despite_stale_remote_ref(tmp_path):
    command = _documented_command(LOCAL_VALIDATION_PATTERN)
    repository, _ = _repository_with_history(tmp_path, reserve_identity=True)
    subprocess.run(["git", "fetch", "upstream", "main"], cwd=repository, check=True)
    subprocess.run(
        ["git", "remote", "set-url", "upstream", str(tmp_path / "missing-upstream.git")],
        cwd=repository,
        check=True,
    )
    workspace = _workspace(repository, tmp_path)

    result = _run_documented_command(repository, command, tmp_path)
    assert result.returncode != 0
    assert "missing-upstream.git" in result.stderr
    _assert_clean(repository, workspace)


def test_documented_validator_development_check_uses_proposed_checkout(tmp_path):
    command = _documented_command(VALIDATOR_DEVELOPMENT_PATTERN)
    repository, _ = _repository_with_history(tmp_path, reserve_identity=True)
    test_path = repository / "validator" / "tests" / "test_documentation_contract.py"
    test_path.write_text(
        test_path.read_text(encoding="utf-8").replace(
            "def test_documented_validator_development_check_uses_proposed_checkout(",
            "def _documented_validator_development_check_uses_proposed_checkout(",
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "--all", "validator/tests"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "avoid nested documentation harness"], cwd=repository, check=True)

    result = _run_documented_command(repository, command, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(repository.rglob(".venv"))
    assert not list(repository.rglob(".pytest_cache"))
    assert not list(repository.rglob("__pycache__"))
