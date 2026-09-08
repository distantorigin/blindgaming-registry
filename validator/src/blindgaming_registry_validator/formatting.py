from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Literal

from .models import Snapshot


class CanonicalFormatError(ValueError):
    """A registry record differs from its canonical JSON representation."""

    def __init__(self, path: str, pointer: str, message: str) -> None:
        self.path = path
        self.pointer = pointer
        self.message = message
        location = f"{path}{pointer}" if pointer else path
        super().__init__(f"{location}: {message}")


_ROOT_PROPERTY_ORDER = {
    "game": (
        "schemaVersion",
        "id",
        "status",
        "name",
        "stores",
        "reservedStores",
        "accessibility",
        "resources",
        "releaseYear",
        "archivedAt",
        "archiveReason",
        "replacedBy",
    ),
    "mod": (
        "schemaVersion",
        "id",
        "status",
        "gameIds",
        "name",
        "framework",
        "homeUrl",
        "releaseSource",
        "links",
        "archivedAt",
        "archiveReason",
        "replacedBy",
    ),
}
_NESTED_PROPERTY_ORDER = {
    "store-entry": ("store", "primary", "url"),
    "accessibility": (
        "description",
        "coverage",
        "confidence",
        "accessVia",
        "tags",
        "wikiRatingUrl",
        "primaryModId",
    ),
    "resource-entry": ("name", "url"),
    "release-source": ("kind", "repositoryUrl"),
    "link": ("kind", "label", "url", "requiresPayment"),
}


def _escape_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _ordered_mapping(value: Mapping[str, object], order: Sequence[str]) -> dict[str, object]:
    known = [key for key in order if key in value]
    unknown = sorted((key for key in value if key not in order), key=str)
    return {key: value[key] for key in (*known, *unknown)}


def _normalise(value: object, shape: str | None = None) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        order = _NESTED_PROPERTY_ORDER.get(shape or "", ())
        return {
            key: _normalise_nested(key, item)
            for key, item in _ordered_mapping(value, order).items()
        }
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    return value


def _normalise_nested(key: str, value: object) -> object:
    if key == "stores":
        items = [_normalise(item, "store-entry") for item in _as_list(value)]
        return sorted(
            items,
            key=lambda item: (
                0 if _as_mapping(item).get("primary") is True else 1,
                str(_as_mapping(item).get("store", "")),
                str(_as_mapping(item).get("url", "")),
            ),
        )
    if key == "reservedStores":
        return {
            store: sorted(_normalise(item) for item in _as_list(keys))
            for store, keys in sorted(_as_mapping(value).items())
        }
    if key == "accessibility":
        return _normalise(value, "accessibility")
    if key == "resources":
        items = [_normalise(item, "resource-entry") for item in _as_list(value)]
        return sorted(
            items,
            key=lambda item: tuple(
                str(_as_mapping(item).get(field, ""))
                for field in ("url", "name")
            ),
        )
    if key == "gameIds":
        return sorted(_normalise(item) for item in _as_list(value))
    if key == "releaseSource":
        return _normalise(value, "release-source")
    if key == "links":
        items = [_normalise(item, "link") for item in _as_list(value)]
        return sorted(
            items,
            key=lambda item: tuple(
                str(_as_mapping(item).get(field, ""))
                for field in ("kind", "url", "label", "requiresPayment")
            ),
        )
    if key == "tags":
        return sorted(_normalise(item) for item in _as_list(value))
    return _normalise(value)


def _as_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return value


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return value


def canonical_record_bytes(record: Mapping[str, object], kind: Literal["game", "mod"] | str) -> bytes:
    """Return a registry record in the repository's canonical JSON form."""
    try:
        root_order = _ROOT_PROPERTY_ORDER[kind]
    except KeyError as error:
        raise ValueError(f"unsupported registry record kind: {kind}") from error
    canonical = {
        key: _normalise_nested(key, value)
        for key, value in _ordered_mapping(record, root_order).items()
    }
    return (json.dumps(canonical, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def canonical_format_pointer(record: Mapping[str, object], kind: Literal["game", "mod"] | str) -> str:
    """Return the pointer for a canonical-format mismatch."""
    root_order = _ROOT_PROPERTY_ORDER[kind]
    pointer = _first_nfd_pointer(record)
    if pointer is not None:
        return pointer
    for key in root_order:
        if key not in record:
            continue
        value = record[key]
        if key == "tags":
            continue
        if key == "accessibility" and isinstance(value, Mapping):
            tags = value.get("tags")
            if isinstance(tags, list) and tags != sorted(tags):
                return "/accessibility/tags"
        if key == "gameIds" and isinstance(value, list):
            if value != sorted(value):
                return "/gameIds"
        if key == "stores" and isinstance(value, list):
            if value != _normalise_nested("stores", value):
                return "/stores"
        if key == "reservedStores" and isinstance(value, Mapping):
            if list(value) != sorted(value) or value != _normalise_nested(key, value):
                return f"/{key}"
        if key == "resources" and isinstance(value, list):
            if value != _normalise_nested("resources", value):
                return "/resources"
        if key == "links" and isinstance(value, list):
            if value != _normalise_nested("links", value):
                return "/links"
    return ""


def _first_nfd_pointer(value: object, pointer: str = "") -> str | None:
    if isinstance(value, str):
        return pointer if value != unicodedata.normalize("NFC", value) else None
    if isinstance(value, Mapping):
        for key, item in value.items():
            found = _first_nfd_pointer(item, f"{pointer}/{_escape_pointer_part(key)}")
            if found is not None:
                return found
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _first_nfd_pointer(item, f"{pointer}/{index}")
            if found is not None:
                return found
    return None


_GUIDE_FRONTMATTER_ORDER = ("title", "author", "lastModified", "draft")


def canonical_guide_bytes(frontmatter: Mapping[str, str], body: str) -> bytes:
    """Return one guide file in the repository's canonical Markdown form."""
    lines = ["---"]
    for key in _GUIDE_FRONTMATTER_ORDER:
        if key in frontmatter and frontmatter[key] is not None:
            value = unicodedata.normalize("NFC", str(frontmatter[key]))
            lines.append(f"{key}: {value}")
    lines.append("---")
    normalised_body = unicodedata.normalize("NFC", body).strip("\n")
    header = "\n".join(lines) + "\n\n"
    return (header + normalised_body + "\n").encode("utf-8")


def snapshot_digest(snapshot: Snapshot) -> str:
    """Hash canonical registry files in a stable order."""
    digest = hashlib.sha256(b"blindgaming-registry-v1\0")
    for path, contents in sorted(snapshot.files, key=lambda item: item[0].encode("utf-8")):
        if path.startswith(("registry/games/", "registry/mods/")) and path.endswith(".json"):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(contents)
            digest.update(b"\0")
        elif path.startswith("registry/guides/") and path.endswith(".md"):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(contents)
            digest.update(b"\0")
    return digest.hexdigest()
