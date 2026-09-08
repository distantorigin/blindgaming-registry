import re
import shlex
import shutil
import subprocess
from pathlib import Path

import yaml


WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "validate.yml"
ACTION_REFERENCE = re.compile(r"^[^/\s]+/[^@\s]+@[0-9a-f]{40}$")


def _workflow() -> dict[str, object]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    steps = job["steps"]
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def _mapping_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {
            *value.keys(),
            *set().union(*(_mapping_keys(item) for item in value.values())),
        }
    if isinstance(value, list):
        return set().union(*(_mapping_keys(item) for item in value)) if value else set()
    return set()


def _shell_tokens(command: str, expression: str, replacement: str) -> list[str]:
    return shlex.split(command.replace(expression, replacement))


def test_required_registry_workflow_uses_an_unprivileged_base_validator_boundary():
    workflow = _workflow()

    assert workflow["on"] == {
        "pull_request": {"types": ["opened", "reopened", "synchronize", "labeled", "unlabeled"]}
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in workflow
    assert "secrets" not in {key.casefold() for key in _mapping_keys(workflow)}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    registry = jobs["registry-validation"]
    assert isinstance(registry, dict)
    assert registry["name"] == "registry-validation"
    assert registry["permissions"] == {"contents": "read"}

    steps = _steps(registry)
    checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 3
    assert checkouts[0]["with"] == {
        "ref": "${{ github.event.pull_request.base.sha }}",
        "path": "base",
    }
    assert checkouts[1]["with"] == {
        "ref": "${{ github.event.pull_request.base.sha }}",
        "path": "base-data",
    }
    assert checkouts[2]["with"] == {
        "ref": "${{ github.event.pull_request.merge_commit_sha }}",
        "path": "proposed",
    }

    for step in steps:
        action = step.get("uses")
        if action is not None:
            assert isinstance(action, str)
            assert ACTION_REFERENCE.fullmatch(action)

    install = next(step for step in steps if step.get("name") == "Install base validator")
    assert install["working-directory"] == "base"
    assert shlex.split(install["run"]) == ["uv", "sync", "--locked", "--extra", "test"]

    validation = next(step for step in steps if step.get("name") == "Validate proposed registry")
    assert validation["working-directory"] == "base"
    assert validation["env"] == {
        "ALLOW_MASS_CHANGE": "${{ contains(github.event.pull_request.labels.*.name, 'registry-mass-change-approved') }}"
    }
    validation_commands = validation["run"].splitlines()
    assert _shell_tokens(
        validation_commands[0],
        "${{ env.ALLOW_MASS_CHANGE == 'true' && '--allow-mass-change' || '' }}",
        "--allow-mass-change",
    ) == [
        "uv",
        "run",
        "blindgaming-registry",
        "validate",
        "--root",
        "../proposed",
        "--base",
        "../base-data",
        "--allow-mass-change",
    ]
    assert shlex.split(validation_commands[1]) == [
        "uv",
        "run",
        "blindgaming-registry",
        "format",
        "--root",
        "../proposed",
        "--check",
    ]


def test_base_validator_install_does_not_pollute_the_base_data_snapshot(tmp_path):
    repository = Path(__file__).parents[2]
    ignored = shutil.ignore_patterns(
        ".git", ".venv", ".worktrees", ".tools", ".pytest_cache", "__pycache__", "*.pyc", ".DS_Store"
    )
    base = tmp_path / "base"
    base_data = tmp_path / "base-data"
    proposed = tmp_path / "proposed"
    for destination in (base, base_data, proposed):
        shutil.copytree(repository, destination, ignore=ignored)

    subprocess.run(["uv", "sync", "--locked", "--extra", "test"], cwd=base, check=True)
    result = subprocess.run(
        [
            "uv",
            "run",
            "blindgaming-registry",
            "validate",
            "--root",
            "../proposed",
            "--base",
            "../base-data",
        ],
        cwd=base,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (base_data / ".venv").exists()
    assert not list(base_data.rglob("__pycache__"))


def test_validator_development_is_review_check_not_registry_publication_authority():
    workflow = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    development = jobs["validator-development"]
    assert isinstance(development, dict)
    assert development["name"] == "validator-development"
    assert development["permissions"] == {"contents": "read"}

    steps = _steps(development)
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {
        "ref": "${{ github.event.pull_request.merge_commit_sha }}",
        "path": "proposed",
        "fetch-depth": 0,
    }
    detection = next(step for step in steps if step.get("name") == "Detect proposed validator changes")
    assert detection["id"] == "validator-changes"
    assert detection["working-directory"] == "proposed"
    test_step = next(step for step in steps if step.get("name") == "Test proposed validator changes")
    assert test_step["working-directory"] == "proposed"
    assert shlex.split(test_step["run"]) == [
        "uv",
        "run",
        "--extra",
        "test",
        "python",
        "-m",
        "pytest",
        "validator/tests",
        "-q",
    ]
    assert test_step["if"] == "${{ steps.validator-changes.outputs.changed == 'true' }}"


def test_validator_change_detection_only_requests_development_tests_for_trust_boundary_paths(tmp_path):
    workflow = _workflow()
    development = workflow["jobs"]["validator-development"]
    assert isinstance(development, dict)
    detection = next(
        step
        for step in _steps(development)
        if step.get("name") == "Detect proposed validator changes"
    )

    repository = tmp_path / "proposed"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Workflow test"], cwd=repository, check=True)
    (repository / "validator").mkdir()
    (repository / "validator" / "contract.py").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "base"], cwd=repository, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, text=True, capture_output=True
    ).stdout.strip()

    output = tmp_path / "output"
    command = detection["run"].replace(
        "${{ github.event.pull_request.base.sha }}", base_sha
    )
    subprocess.run(
        ["bash", "-c", command],
        cwd=repository,
        check=True,
        env={"GITHUB_OUTPUT": str(output)},
    )
    assert output.read_text(encoding="utf-8") == "changed=false\n"

    (repository / "validator" / "contract.py").write_text("proposed\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "validator change"], cwd=repository, check=True)
    subprocess.run(
        ["bash", "-c", command],
        cwd=repository,
        check=True,
        env={"GITHUB_OUTPUT": str(output)},
    )
    assert output.read_text(encoding="utf-8").endswith("changed=true\n")


def test_validator_change_detection_includes_workflow_contract_changes(tmp_path):
    workflow = _workflow()
    development = workflow["jobs"]["validator-development"]
    assert isinstance(development, dict)
    detection = next(
        step
        for step in _steps(development)
        if step.get("name") == "Detect proposed validator changes"
    )

    repository = tmp_path / "proposed"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Workflow test"], cwd=repository, check=True)
    workflow_path = repository / ".github" / "workflows" / "validate.yml"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "base"], cwd=repository, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, text=True, capture_output=True
    ).stdout.strip()
    workflow_path.write_text("proposed\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "workflow change"], cwd=repository, check=True)

    output = tmp_path / "output"
    command = detection["run"].replace(
        "${{ github.event.pull_request.base.sha }}", base_sha
    )
    subprocess.run(
        ["bash", "-c", command],
        cwd=repository,
        check=True,
        env={"GITHUB_OUTPUT": str(output)},
    )

    assert output.read_text(encoding="utf-8") == "changed=true\n"
