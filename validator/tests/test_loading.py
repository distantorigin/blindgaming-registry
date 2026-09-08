from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath

import pytest

from blindgaming_registry_validator.formatting import (
    CanonicalFormatError,
    canonical_guide_bytes,
    canonical_record_bytes,
    snapshot_digest,
)
from blindgaming_registry_validator.loading import (
    RegistryParseError,
    DuplicateKeyError,
    UnexpectedPathError,
    load_snapshot,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "valid"


def registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    shutil.copytree(FIXTURE_ROOT / "registry", root / "registry")
    return root


def record_path(root: Path, kind: str, identifier: str) -> Path:
    return root / "registry" / f"{kind}s" / f"{identifier}.json"


def read_record(root: Path, kind: str, identifier: str) -> dict[str, object]:
    return json.loads(record_path(root, kind, identifier).read_text(encoding="utf-8"))


def write_record(root: Path, kind: str, identifier: str, record: object) -> None:
    record_path(root, kind, identifier).write_bytes(
        canonical_record_bytes(record, kind)
    )


def test_load_snapshot_exposes_sorted_typed_records_and_canonical_file_bytes(tmp_path: Path):
    root = registry_root(tmp_path)

    snapshot = load_snapshot(root)

    assert snapshot.schema_version == 1
    assert [record.id for record in snapshot.games] == ["example-game"]
    assert [record.id for record in snapshot.mods] == ["example-accessibility-mod"]
    assert snapshot.games[0].canonical_store == "steam"
    assert snapshot.games[0].stores[0].store_key == "1234560"
    assert tuple(path for path, _ in snapshot.files) == (
        "registry/games/example-game.json",
        "registry/mods/example-accessibility-mod.json",
    )
    assert dict(snapshot.files)["registry/games/example-game.json"] == record_path(
        root, "game", "example-game"
    ).read_bytes()


def test_load_snapshot_exposes_reserved_store_keys(tmp_path: Path):
    root = registry_root(tmp_path)
    record = read_record(root, "game", "example-game")
    record["reservedStores"] = {"steam": ["1234560"]}
    record_path(root, "game", "example-game").write_bytes(
        canonical_record_bytes(record, "game")
    )

    snapshot = load_snapshot(root)

    assert snapshot.games[0].reserved_stores[0].store == "steam"
    assert snapshot.games[0].reserved_stores[0].store_key == "1234560"


def test_load_snapshot_omits_an_absent_accessibility_summary_and_tags(tmp_path: Path):
    root = registry_root(tmp_path)
    record = read_record(root, "game", "example-game")
    del record["accessibility"]["description"]
    del record["accessibility"]["tags"]
    record_path(root, "game", "example-game").write_bytes(
        canonical_record_bytes(record, "game")
    )

    snapshot = load_snapshot(root)

    assert snapshot.games[0].accessibility.description is None
    assert snapshot.games[0].accessibility.tags == ()
    assert snapshot.games[0].accessibility.coverage == "full"
    assert snapshot.games[0].accessibility.confidence == "verified"
    assert snapshot.games[0].accessibility.access_via == "mod"


def test_load_snapshot_reports_invalid_utf8_with_repository_file_context(tmp_path: Path):
    root = registry_root(tmp_path)
    record_path(root, "game", "example-game").write_bytes(b"\xff")

    with pytest.raises(RegistryParseError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/games/example-game.json"
    assert caught.value.pointer == ""
    assert caught.value.message == "invalid UTF-8"


def test_load_snapshot_reports_invalid_json_with_repository_file_context(tmp_path: Path):
    root = registry_root(tmp_path)
    record_path(root, "game", "example-game").write_text("{\n", encoding="utf-8")

    with pytest.raises(RegistryParseError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/games/example-game.json"
    assert caught.value.pointer == ""
    assert caught.value.message == "invalid JSON"


def test_load_snapshot_rejects_duplicate_json_object_keys_with_the_record_path(tmp_path: Path):
    root = registry_root(tmp_path)
    path = record_path(root, "game", "example-game")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '  "id": "example-game",',
            '  "id": "example-game",\n  "id": "different-id",',
        ),
        encoding="utf-8",
    )

    with pytest.raises(DuplicateKeyError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/games/example-game.json"
    assert caught.value.pointer == "/id"


def test_load_snapshot_reports_the_full_pointer_for_a_nested_duplicate_key(tmp_path: Path):
    root = registry_root(tmp_path)
    path = record_path(root, "game", "example-game")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '      "store": "steam",',
            '      "store": "steam",\n      "store": "gog",',
        ),
        encoding="utf-8",
    )

    with pytest.raises(DuplicateKeyError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/games/example-game.json"
    assert caught.value.pointer == "/stores/0/store"


def test_load_snapshot_escapes_a_nested_duplicate_key_pointer(tmp_path: Path):
    root = registry_root(tmp_path)
    path = record_path(root, "game", "example-game")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '  "accessibility": {',
            """  \"accessibility\": {
    \"a/b~c\": 1,
    \"a/b~c\": 2,""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(DuplicateKeyError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/games/example-game.json"
    assert caught.value.pointer == "/accessibility/a~1b~0c"


@pytest.mark.parametrize(
    "relative_path",
    ["registry/games/example-game.txt", "registry/notes.json", "unexpected.json"],
)
def test_load_snapshot_rejects_undeclared_repository_paths(
    tmp_path: Path, relative_path: str
):
    root = registry_root(tmp_path)
    unexpected = root / relative_path
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_text("not registry data\n", encoding="utf-8")

    with pytest.raises(UnexpectedPathError) as caught:
        load_snapshot(root)

    assert caught.value.path == relative_path
    assert caught.value.pointer == ""


def test_load_snapshot_rejects_case_folded_registry_path_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = registry_root(tmp_path)

    class Entry:
        def __init__(self, relative: str) -> None:
            self.relative = relative

        def relative_to(self, _: Path) -> PurePosixPath:
            return PurePosixPath(self.relative)

        def is_symlink(self) -> bool:
            return False

        def is_dir(self) -> bool:
            return False

        def is_file(self) -> bool:
            return True

    original_rglob = Path.rglob

    def colliding_rglob(path: Path, pattern: str):
        if path == root and pattern == "*":
            return [
                Entry("registry/games/example-game.json"),
                Entry("registry/games/EXAMPLE-GAME.json"),
            ]
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", colliding_rglob)

    with pytest.raises(UnexpectedPathError) as caught:
        load_snapshot(root)

    assert caught.value.pointer == ""
    assert "example-game.json" in caught.value.path.casefold()


def test_load_snapshot_ignores_git_metadata_when_scanning_a_checkout(tmp_path: Path):
    root = registry_root(tmp_path)
    git_metadata = root / ".git"
    git_metadata.mkdir()
    (git_metadata / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    snapshot = load_snapshot(root)

    assert [record.id for record in snapshot.games] == ["example-game"]


def test_load_snapshot_rejects_a_symlink_without_reading_its_target(tmp_path: Path):
    root = registry_root(tmp_path)
    target = tmp_path / "outside.json"
    target.write_text("{}\n", encoding="utf-8")
    link = root / "registry" / "games" / "linked.json"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"the filesystem does not support symlink tests: {error}")

    with pytest.raises(UnexpectedPathError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/games/linked.json"
    assert caught.value.pointer == ""


def test_load_snapshot_rejects_a_record_whose_id_does_not_match_its_filename(tmp_path: Path):
    root = registry_root(tmp_path)
    record = read_record(root, "game", "example-game")
    record["id"] = "a-different-id"
    write_record(root, "game", "example-game", record)

    with pytest.raises(UnexpectedPathError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/games/example-game.json"
    assert caught.value.pointer == "/id"


def test_load_snapshot_rejects_mixed_schema_versions(tmp_path: Path):
    root = registry_root(tmp_path)
    record = read_record(root, "mod", "example-accessibility-mod")
    record["schemaVersion"] = 2
    write_record(root, "mod", "example-accessibility-mod", record)

    with pytest.raises(ValueError, match="mixed registry schema versions"):
        load_snapshot(root)


@pytest.mark.parametrize("json_value", ["true", "1.0"])
def test_load_snapshot_rejects_non_integer_json_schema_versions(
    tmp_path: Path, json_value: str
):
    root = registry_root(tmp_path)
    path = record_path(root, "game", "example-game")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"schemaVersion": 1', f'"schemaVersion": {json_value}'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schemaVersion must be a JSON integer"):
        load_snapshot(root)


def test_load_snapshot_rejects_a_mixed_integer_and_non_integer_schema_version_pair(
    tmp_path: Path,
):
    root = registry_root(tmp_path)
    path = record_path(root, "mod", "example-accessibility-mod")
    path.write_text(
        path.read_text(encoding="utf-8").replace('"schemaVersion": 1', '"schemaVersion": 1.0'),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schemaVersion must be a JSON integer"):
        load_snapshot(root)


def test_snapshot_digest_uses_only_sorted_canonical_registry_bytes_with_null_framing(
    tmp_path: Path,
):
    snapshot = load_snapshot(registry_root(tmp_path))
    files = dict(snapshot.files)
    game_bytes = files["registry/games/example-game.json"]
    mod_bytes = files["registry/mods/example-accessibility-mod.json"]

    expected = hashlib.sha256(
        b"blindgaming-registry-v1\0"
        b"registry/games/example-game.json\0"
        + game_bytes
        + b"\0"
        b"registry/mods/example-accessibility-mod.json\0"
        + mod_bytes
        + b"\0"
    ).hexdigest()

    assert snapshot_digest(snapshot) == expected


def test_load_snapshot_accepts_the_submission_automation_paths(tmp_path: Path):
    root = registry_root(tmp_path)
    forms = root / ".github" / "ISSUE_TEMPLATE"
    forms.mkdir(parents=True)
    (forms / "add-game.yml").write_text("name: Add a game\n", encoding="utf-8")
    (forms / "config.yml").write_text("blank_issues_enabled: false\n", encoding="utf-8")
    scripts = root / ".github" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "extract_record.py").write_text("print('report')\n", encoding="utf-8")

    snapshot = load_snapshot(root)

    assert [record.id for record in snapshot.games] == ["example-game"]
    assert all(not path.startswith(".github/") for path, _ in snapshot.files)


def test_load_snapshot_rejects_an_undeclared_file_beside_the_submission_scripts(
    tmp_path: Path,
):
    root = registry_root(tmp_path)
    scripts = root / ".github" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "extract_record.sh").write_text("echo report\n", encoding="utf-8")

    with pytest.raises(UnexpectedPathError) as caught:
        load_snapshot(root)

    assert caught.value.path == ".github/scripts/extract_record.sh"


def guide_path(root: Path, identifier: str) -> Path:
    return root / "registry" / "guides" / f"{identifier}.md"


def write_guide(
    root: Path,
    identifier: str,
    *,
    title: str = "Getting started with a screen reader",
    author: str | None = "Jane Doe",
    last_modified: str | None = "2026-08-01",
    body: str = "# Heading\n\nA short guide body.",
    draft: bool = False,
) -> Path:
    frontmatter: dict[str, str] = {"title": title}
    if author is not None:
        frontmatter["author"] = author
    if last_modified is not None:
        frontmatter["lastModified"] = last_modified
    if draft:
        frontmatter["draft"] = "true"
    path = guide_path(root, identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_guide_bytes(frontmatter, body))
    return path


def test_load_snapshot_exposes_a_guide_with_frontmatter_and_body(tmp_path: Path):
    root = registry_root(tmp_path)
    write_guide(root, "screen-reader-basics")

    snapshot = load_snapshot(root)

    assert [guide.id for guide in snapshot.guides] == ["screen-reader-basics"]
    guide = snapshot.guides[0]
    assert guide.title == "Getting started with a screen reader"
    assert guide.author == "Jane Doe"
    assert guide.last_modified == "2026-08-01"
    assert guide.body == "# Heading\n\nA short guide body.\n"
    assert dict(snapshot.files)["registry/guides/screen-reader-basics.md"] == guide_path(
        root, "screen-reader-basics"
    ).read_bytes()


def test_load_snapshot_omits_absent_optional_guide_frontmatter(tmp_path: Path):
    root = registry_root(tmp_path)
    write_guide(root, "no-author-guide", author=None, last_modified=None)

    snapshot = load_snapshot(root)

    guide = snapshot.guides[0]
    assert guide.author is None
    assert guide.last_modified is None


def test_load_snapshot_rejects_a_guide_missing_the_opening_frontmatter_fence(
    tmp_path: Path,
):
    root = registry_root(tmp_path)
    path = guide_path(root, "broken-guide")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"title: no fence\n\nBody.\n")

    with pytest.raises(RegistryParseError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/guides/broken-guide.md"


def test_load_snapshot_rejects_a_guide_missing_its_closing_fence(tmp_path: Path):
    root = registry_root(tmp_path)
    path = guide_path(root, "broken-guide")
    path.parent.mkdir(parents=True, exist_ok=True)
    # No trailing newline: the frontmatter runs off the end of the file
    # without ever finding a closing '---' line.
    path.write_bytes(b"---\ntitle: No closing fence")

    with pytest.raises(RegistryParseError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/guides/broken-guide.md"
    assert "closing" in caught.value.message


def test_load_snapshot_marks_a_guide_with_draft_true_as_a_draft(tmp_path: Path):
    root = registry_root(tmp_path)
    write_guide(root, "draft-guide", draft=True)
    write_guide(root, "published-guide")

    snapshot = load_snapshot(root)

    drafts = {guide.id: guide.draft for guide in snapshot.guides}
    assert drafts == {"draft-guide": True, "published-guide": False}


def test_load_snapshot_rejects_a_draft_value_other_than_true(tmp_path: Path):
    root = registry_root(tmp_path)
    path = guide_path(root, "odd-draft")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"---\ntitle: A guide\ndraft: yes\n---\n\nBody.\n")

    with pytest.raises(RegistryParseError) as caught:
        load_snapshot(root)

    assert caught.value.pointer == "/draft"


def test_load_snapshot_rejects_a_guide_missing_a_title(tmp_path: Path):
    root = registry_root(tmp_path)
    path = guide_path(root, "no-title-guide")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"---\nauthor: Someone\n---\n\nBody.\n")

    with pytest.raises(RegistryParseError) as caught:
        load_snapshot(root)

    assert caught.value.pointer == "/title"


def test_load_snapshot_rejects_a_guide_with_an_empty_body(tmp_path: Path):
    root = registry_root(tmp_path)
    path = guide_path(root, "empty-guide")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"---\ntitle: Empty\n---\n\n")

    with pytest.raises(RegistryParseError) as caught:
        load_snapshot(root)

    assert "body" in caught.value.message


def test_load_snapshot_rejects_a_duplicate_guide_frontmatter_key(tmp_path: Path):
    root = registry_root(tmp_path)
    path = guide_path(root, "duplicate-guide")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"---\ntitle: One\ntitle: Two\n---\n\nBody.\n")

    with pytest.raises(DuplicateKeyError) as caught:
        load_snapshot(root)

    assert caught.value.pointer == "/title"


def test_load_snapshot_rejects_a_noncanonically_formatted_guide(tmp_path: Path):
    root = registry_root(tmp_path)
    path = write_guide(root, "messy-guide")
    path.write_bytes(path.read_bytes() + b"\n\n")  # extra trailing blank lines

    with pytest.raises(CanonicalFormatError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/guides/messy-guide.md"


def test_snapshot_digest_includes_guide_bytes(tmp_path: Path):
    with_guide_root = registry_root(tmp_path / "with-guide")
    write_guide(with_guide_root, "screen-reader-basics")
    without_guide_root = registry_root(tmp_path / "without-guide")

    with_guide = load_snapshot(with_guide_root)
    without_guide = load_snapshot(without_guide_root)

    assert snapshot_digest(with_guide) != snapshot_digest(without_guide)


def test_git_attributes_do_not_change_registry_snapshot(tmp_path: Path):
    root = registry_root(tmp_path)
    before = load_snapshot(root)
    (root / ".gitattributes").write_text("*.json text eol=lf\n", encoding="utf-8")

    after = load_snapshot(root)

    assert after == before
    assert snapshot_digest(after) == snapshot_digest(before)
