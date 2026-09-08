from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from .formatting import (
    CanonicalFormatError,
    canonical_format_pointer,
    canonical_guide_bytes,
    canonical_record_bytes,
)
from .policy import classify_store_url
from .schemas import REGISTRY_SCHEMA_VERSION
from .models import (
    Accessibility,
    GameRecord,
    GuideRecord,
    ModLink,
    ModRecord,
    ReleaseSource,
    Resource,
    Snapshot,
    StoreEntry,
    StoreIdentity,
)


class UnexpectedPathError(ValueError):
    """A path is not part of the registry repository layout."""

    def __init__(self, path: str, pointer: str, message: str) -> None:
        self.path = path
        self.pointer = pointer
        self.message = message
        location = f"{path}{pointer}" if pointer else path
        super().__init__(f"{location}: {message}")


class DuplicateKeyError(ValueError):
    """A JSON object declares the same key more than once."""

    def __init__(self, path: str, pointer: str, message: str) -> None:
        self.path = path
        self.pointer = pointer
        self.message = message
        location = f"{path}{pointer}" if pointer else path
        super().__init__(f"{location}: {message}")


class RegistryParseError(ValueError):
    """A registry record cannot be decoded as one JSON document."""

    def __init__(self, path: str, pointer: str, message: str) -> None:
        self.path = path
        self.pointer = pointer
        self.message = message
        location = f"{path}{pointer}" if pointer else path
        super().__init__(f"{location}: {message}")


class RegistryIOError(OSError):
    """A registry record could not be read from the filesystem."""

    def __init__(self, path: str, pointer: str, message: str) -> None:
        self.path = path
        self.pointer = pointer
        self.message = message
        location = f"{path}{pointer}" if pointer else path
        super().__init__(f"{location}: {message}")


class _JSONPairs(dict[str, object]):
    def __init__(self, pairs: Sequence[tuple[str, object]]) -> None:
        super().__init__()
        self.duplicate_key: str | None = None
        for key, value in pairs:
            if key in self and self.duplicate_key is None:
                self.duplicate_key = key
            else:
                self[key] = value


def reject_duplicate_pairs(pairs: Sequence[tuple[str, object]]) -> _JSONPairs:
    """json.loads hook that rejects duplicate object keys."""
    return _JSONPairs(pairs)


_ROOT_FILES = frozenset(
    {
        ".gitignore",
        ".gitattributes",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
    }
)
_SCHEMA_FILES = frozenset(
    {
        "schemas/v1/common.schema.json",
        "schemas/v1/game.schema.json",
        "schemas/v1/mod.schema.json",
    }
)


def load_snapshot(root: Path) -> Snapshot:
    """Load a canonical registry snapshot."""
    root = Path(root)
    if root.is_symlink():
        raise UnexpectedPathError("", "", "repository root must not be a symlink")
    paths = _repository_paths(root)
    records: list[tuple[str, str, Mapping[str, object], bytes]] = []
    guides: list[tuple[str, GuideRecord, bytes]] = []
    for relative, path in paths:
        if _is_guide_path(relative):
            try:
                contents = path.read_bytes()
            except OSError as error:
                raise RegistryIOError(relative, "", str(error)) from error
            try:
                text = contents.decode("utf-8")
            except UnicodeDecodeError as error:
                raise RegistryParseError(relative, "", "invalid UTF-8") from error
            frontmatter, body = _parse_guide_frontmatter(relative, text)
            expected_guide_bytes = canonical_guide_bytes(frontmatter, body)
            if contents != expected_guide_bytes:
                raise CanonicalFormatError(
                    relative, "", "record is not canonically formatted"
                )
            guides.append((
                relative,
                GuideRecord(
                    id=Path(relative).stem,
                    title=frontmatter["title"],
                    author=frontmatter.get("author"),
                    last_modified=frontmatter.get("lastModified"),
                    body=body,
                    draft="draft" in frontmatter,
                ),
                expected_guide_bytes,
            ))
            continue
        if not _is_record_path(relative):
            continue
        kind = "game" if relative.startswith("registry/games/") else "mod"
        try:
            contents = path.read_bytes()
        except OSError as error:
            raise RegistryIOError(relative, "", str(error)) from error
        try:
            text = contents.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RegistryParseError(relative, "", "invalid UTF-8") from error
        try:
            raw_record = json.loads(text, object_pairs_hook=reject_duplicate_pairs)
        except json.JSONDecodeError as error:
            raise RegistryParseError(relative, "", "invalid JSON") from error
        if not isinstance(raw_record, Mapping):
            raise ValueError(f"{relative}: registry records must be JSON objects")
        duplicate_pointer = _duplicate_pointer(raw_record)
        if duplicate_pointer is not None:
            raise DuplicateKeyError(relative, duplicate_pointer, "duplicate JSON object key")
        record = dict(raw_record)
        schema_version = record.get("schemaVersion")
        if type(schema_version) is not int:
            raise ValueError(
                f"{relative}/schemaVersion: schemaVersion must be a JSON integer"
            )
        expected = canonical_record_bytes(record, kind)
        if contents != expected:
            raise CanonicalFormatError(
                relative,
                canonical_format_pointer(record, kind),
                "record is not canonically formatted",
            )
        identifier = record.get("id")
        if identifier != Path(relative).stem:
            raise UnexpectedPathError(relative, "/id", "filename stem must match record id")
        records.append((relative, kind, record, expected))

    versions: set[int] = set()
    for relative, _, record, _ in records:
        schema_version = record.get("schemaVersion")
        versions.add(schema_version)
    if len(versions) > 1:
        raise ValueError("mixed registry schema versions")
    schema_version = (
        REGISTRY_SCHEMA_VERSION if not versions else next(iter(versions))
    )
    if schema_version != REGISTRY_SCHEMA_VERSION:
        raise ValueError(f"unsupported registry schema version: {schema_version}")

    games = tuple(
        _game_record(record)
        for _, kind, record, _ in records
        if kind == "game"
    )
    mods = tuple(
        _mod_record(record)
        for _, kind, record, _ in records
        if kind == "mod"
    )
    files = tuple((relative, contents) for relative, _, _, contents in records) + tuple(
        (relative, contents) for relative, _, contents in guides
    )
    return Snapshot(
        schema_version=schema_version,
        games=tuple(sorted(games, key=lambda record: record.id)),
        mods=tuple(sorted(mods, key=lambda record: record.id)),
        files=files,
        guides=tuple(sorted((record for _, record, _ in guides), key=lambda record: record.id)),
    )


def _repository_paths(root: Path) -> tuple[tuple[str, Path], ...]:
    entries: list[tuple[str, Path]] = []
    casefolded: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().encode("utf-8")):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_symlink():
            raise UnexpectedPathError(relative, "", "symbolic links are not permitted")
        folded = relative.casefold()
        previous = casefolded.get(folded)
        if previous is not None and previous != relative:
            raise UnexpectedPathError(relative, "", "case-folded path collision")
        casefolded[folded] = relative
        if path.is_dir():
            if not _is_allowed_directory(relative):
                raise UnexpectedPathError(relative, "", "unexpected repository directory")
            continue
        if not path.is_file() or not _is_allowed_file(relative):
            raise UnexpectedPathError(relative, "", "unexpected repository path")
        entries.append((relative, path))
    return tuple(entries)


def _is_allowed_directory(relative: str) -> bool:
    return relative in {
        "registry",
        "registry/games",
        "registry/mods",
        "registry/guides",
        "schemas",
        "schemas/v1",
        "validator",
        "validator/src",
        "validator/src/blindgaming_registry_validator",
        "validator/src/blindgaming_registry_validator/_schemas",
        "validator/src/blindgaming_registry_validator/_schemas/v1",
        "validator/tests",
        "validator/tests/fixtures",
        "validator/tests/fixtures/valid",
        "validator/tests/fixtures/valid/registry",
        "validator/tests/fixtures/valid/registry/games",
        "validator/tests/fixtures/valid/registry/mods",
        ".github",
        ".github/ISSUE_TEMPLATE",
        ".github/scripts",
        ".github/workflows",
    }


def _is_allowed_file(relative: str) -> bool:
    if relative in _ROOT_FILES or relative in _SCHEMA_FILES:
        return True
    if relative in {
        "registry/games/.gitkeep",
        "registry/mods/.gitkeep",
        "registry/guides/.gitkeep",
    }:
        return True
    if _is_record_path(relative) or _is_guide_path(relative):
        return True
    if relative.startswith("validator/src/blindgaming_registry_validator/"):
        return relative.endswith((".py", ".json"))
    if relative.startswith("validator/tests/"):
        return relative.endswith((".py", ".json"))
    # Submission automation keeps its helper script under .github/scripts.
    if relative.startswith(".github/scripts/"):
        return relative.endswith(".py")
    if relative.startswith(".github/"):
        return relative.endswith((".yml", ".md")) or relative == ".github/CODEOWNERS"
    return False


def _is_record_path(relative: str) -> bool:
    return (
        (relative.startswith("registry/games/") or relative.startswith("registry/mods/"))
        and relative.endswith(".json")
        and "/" not in relative.removeprefix("registry/games/").removeprefix("registry/mods/")
    )


def _is_guide_path(relative: str) -> bool:
    return (
        relative.startswith("registry/guides/")
        and relative.endswith(".md")
        and "/" not in relative.removeprefix("registry/guides/")
    )


_GUIDE_FRONTMATTER_LINE = re.compile(r"(title|author|lastModified|draft): (.+)")


def _parse_guide_frontmatter(relative: str, text: str) -> tuple[dict[str, str], str]:
    """Parse the guide frontmatter block and Markdown body.

    Shape errors raise load-time parse errors so validation can report them
    against the file. Policy checks stay in `_validate_guide`.
    """
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise RegistryParseError(
            relative, "", "guide must begin with a '---' frontmatter block"
        )
    frontmatter: dict[str, str] = {}
    index = 1
    while index >= len(lines) or lines[index] != "---":
        if index >= len(lines):
            raise RegistryParseError(
                relative, "", "guide frontmatter is missing its closing '---'"
            )
        line = lines[index]
        match = _GUIDE_FRONTMATTER_LINE.fullmatch(line)
        if match is None:
            raise RegistryParseError(
                relative,
                "",
                "guide frontmatter line is not a recognized 'key: value' pair",
            )
        key, value = match.group(1), match.group(2)
        if key in frontmatter:
            raise DuplicateKeyError(relative, f"/{key}", "duplicate frontmatter key")
        frontmatter[key] = value
        index += 1
    index += 1
    if index >= len(lines) or lines[index] != "":
        raise RegistryParseError(
            relative,
            "",
            "guide frontmatter must be followed by exactly one blank line",
        )
    body = "\n".join(lines[index + 1 :])
    if "title" not in frontmatter:
        raise RegistryParseError(relative, "/title", "guide frontmatter must include a title")
    # A draft stays in the repository but is not published. The only accepted
    # spelling is `draft: true`; omit the line entirely to publish.
    if "draft" in frontmatter and frontmatter["draft"] != "true":
        raise RegistryParseError(
            relative, "/draft", "draft must be exactly 'true'; omit the line to publish"
        )
    if not body.strip():
        raise RegistryParseError(relative, "", "guide body must not be empty")
    return frontmatter, body


def _escape_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _duplicate_pointer(value: object, pointer: str = "") -> str | None:
    if isinstance(value, _JSONPairs) and value.duplicate_key is not None:
        return f"{pointer}/{_escape_pointer_part(value.duplicate_key)}"
    if isinstance(value, Mapping):
        for key, item in value.items():
            found = _duplicate_pointer(item, f"{pointer}/{_escape_pointer_part(key)}")
            if found is not None:
                return found
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _duplicate_pointer(item, f"{pointer}/{index}")
            if found is not None:
                return found
    return None


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("registry record has an invalid object value")
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("registry record has an invalid string value")
    return value


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise ValueError("registry record has an invalid array value")
    return value


def _derived_store_key(store: str, url: str) -> str:
    identity = classify_store_url(url)
    if identity is None or identity[0] != store:
        return ""
    return identity[1]


def _store_list(value: object) -> tuple[StoreEntry, ...]:
    return tuple(
        StoreEntry(
            store=_string(_mapping(entry)["store"]),
            store_key=_derived_store_key(
                _string(_mapping(entry)["store"]),
                _string(_mapping(entry)["url"]),
            ),
            url=_string(_mapping(entry)["url"]),
        )
        for entry in _sequence(value)
    )


def _primary_store(value: object) -> str:
    for entry in _sequence(value):
        source = _mapping(entry)
        if source.get("primary") is True:
            return _string(source["store"])
    return ""


def _reserved_store_map(value: object) -> tuple[StoreIdentity, ...]:
    source = _mapping(value)
    return tuple(
        StoreIdentity(store=store, store_key=_string(key))
        for store, keys in sorted(source.items())
        for key in _sequence(keys)
    )


def _accessibility(value: object) -> Accessibility:
    source = _mapping(value)
    return Accessibility(
        description=_string(source["description"]) if "description" in source else None,
        coverage=_string(source["coverage"]) if "coverage" in source else None,
        confidence=_string(source["confidence"]) if "confidence" in source else None,
        access_via=_string(source["accessVia"]) if "accessVia" in source else None,
        tags=(
            tuple(_string(tag) for tag in _sequence(source["tags"]))
            if "tags" in source
            else ()
        ),
        wiki_rating_url=_string(source["wikiRatingUrl"]) if "wikiRatingUrl" in source else None,
        primary_mod_id=_string(source["primaryModId"]) if "primaryModId" in source else None,
    )


def _resource_list(value: object) -> tuple[Resource, ...]:
    return tuple(
        Resource(
            name=_string(_mapping(entry)["name"]),
            url=_string(_mapping(entry)["url"]),
        )
        for entry in _sequence(value)
    )


def _release_source(value: object) -> ReleaseSource:
    source = _mapping(value)
    return ReleaseSource(
        kind=_string(source["kind"]), repository_url=_string(source["repositoryUrl"])
    )


def _mod_link(value: object) -> ModLink:
    source = _mapping(value)
    return ModLink(
        kind=_string(source["kind"]),
        label=_string(source["label"]),
        url=_string(source["url"]),
        requires_payment=source["requiresPayment"] is True,
    )


def _game_record(source: Mapping[str, object]) -> GameRecord:
    return GameRecord(
        schema_version=source["schemaVersion"] if isinstance(source["schemaVersion"], int) else 0,
        id=_string(source["id"]),
        status=_string(source["status"]),
        name=_string(source["name"]),
        canonical_store=_primary_store(source["stores"]),
        stores=_store_list(source["stores"]),
        reserved_stores=_reserved_store_map(source.get("reservedStores", {})),
        accessibility=_accessibility(source["accessibility"]),
        resources=_resource_list(source.get("resources", [])),
        release_year=_string(source["releaseYear"]) if "releaseYear" in source else None,
        archived_at=_string(source["archivedAt"]) if "archivedAt" in source else None,
        archive_reason=_string(source["archiveReason"]) if "archiveReason" in source else None,
        replaced_by=_string(source["replacedBy"]) if "replacedBy" in source else None,
    )


def _mod_record(source: Mapping[str, object]) -> ModRecord:
    return ModRecord(
        schema_version=source["schemaVersion"] if isinstance(source["schemaVersion"], int) else 0,
        id=_string(source["id"]),
        status=_string(source["status"]),
        game_ids=tuple(_string(v) for v in _sequence(source["gameIds"])),
        name=_string(source["name"]),
        home_url=_string(source["homeUrl"]),
        links=tuple(_mod_link(item) for item in _sequence(source["links"])),
        framework=_string(source["framework"]) if "framework" in source else None,
        release_source=_release_source(source["releaseSource"])
        if "releaseSource" in source
        else None,
        archived_at=_string(source["archivedAt"]) if "archivedAt" in source else None,
        archive_reason=_string(source["archiveReason"]) if "archiveReason" in source else None,
        replaced_by=_string(source["replacedBy"]) if "replacedBy" in source else None,
    )
