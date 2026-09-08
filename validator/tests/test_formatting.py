from __future__ import annotations

import json
import shutil
import unicodedata
from pathlib import Path

import pytest

from blindgaming_registry_validator.formatting import (
    CanonicalFormatError,
    canonical_guide_bytes,
    canonical_record_bytes,
)
from blindgaming_registry_validator.loading import load_snapshot


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "valid"


def registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    shutil.copytree(FIXTURE_ROOT / "registry", root / "registry")
    return root


def game_path(root: Path) -> Path:
    return root / "registry" / "games" / "example-game.json"


def game_record(root: Path) -> dict[str, object]:
    return json.loads(game_path(root).read_text(encoding="utf-8"))


def test_canonical_record_bytes_uses_the_contract_order_and_final_newline(tmp_path: Path):
    root = registry_root(tmp_path)
    record = game_record(root)

    actual = canonical_record_bytes(record, "game")

    assert actual == game_path(root).read_bytes()
    assert actual.endswith(b"\n")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda source: source.removesuffix("\n"),
        lambda source: source.replace("  ", "\t", 1),
        lambda source: source.replace("  ", "    ", 1),
        lambda source: source.replace(
            '  "schemaVersion": 1,\n  "id": "example-game",',
            '  "id": "example-game",\n  "schemaVersion": 1,',
        ),
    ],
    ids=["missing-final-newline", "tab-indent", "four-space-indent", "property-order"],
)
def test_load_snapshot_rejects_noncanonical_json_layout(
    tmp_path: Path, mutation
):
    root = registry_root(tmp_path)
    path = game_path(root)
    path.write_text(mutation(path.read_text(encoding="utf-8")), encoding="utf-8")

    with pytest.raises(CanonicalFormatError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/games/example-game.json"
    assert caught.value.pointer == ""


def test_load_snapshot_rejects_nfd_text_even_when_json_remains_valid(tmp_path: Path):
    root = registry_root(tmp_path)
    record = game_record(root)
    record["name"] = unicodedata.normalize("NFD", "Café")
    game_path(root).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(CanonicalFormatError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/games/example-game.json"
    assert caught.value.pointer == "/name"


def test_canonical_record_bytes_sorts_unordered_registry_arrays(tmp_path: Path):
    root = registry_root(tmp_path)
    record = game_record(root)
    accessibility = record["accessibility"]
    assert isinstance(accessibility, dict)
    accessibility["tags"] = ["audio-description", "blind"]

    canonical = canonical_record_bytes(record, "game")

    assert b'"tags": [\n      "audio-description",\n      "blind"\n    ]' in canonical


def test_load_snapshot_rejects_noncanonical_unordered_array_order(tmp_path: Path):
    root = registry_root(tmp_path)
    record = game_record(root)
    accessibility = record["accessibility"]
    assert isinstance(accessibility, dict)
    accessibility["tags"] = ["blind", "audio-description"]
    game_path(root).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(CanonicalFormatError) as caught:
        load_snapshot(root)

    assert caught.value.path == "registry/games/example-game.json"
    assert caught.value.pointer == "/accessibility/tags"


def test_reserved_stores_have_canonical_key_and_array_order(tmp_path: Path):
    root = registry_root(tmp_path)
    record = game_record(root)
    record["reservedStores"] = {
        "steam": ["1234560", "1234559"],
        "gog": ["example-game"],
    }

    canonical = canonical_record_bytes(record, "game")

    reserved_offset = canonical.index(b'  "reservedStores":')
    assert canonical.index(b'  "stores":') < reserved_offset
    assert reserved_offset < canonical.index(b'  "accessibility":')
    reserved = canonical[reserved_offset:canonical.index(b'  "accessibility":')]
    assert reserved.index(b'"gog":') < reserved.index(b'"steam":')
    assert reserved.index(b'"1234559"') < reserved.index(b'"1234560"')

    game_path(root).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CanonicalFormatError) as caught:
        load_snapshot(root)

    assert caught.value.pointer == "/reservedStores"


def test_canonical_guide_bytes_orders_frontmatter_and_ends_with_one_newline():
    canonical = canonical_guide_bytes(
        {"draft": "true", "lastModified": "2026-08-01", "title": "A guide", "author": "Jane Doe"},
        "Body text.",
    )

    assert canonical == (
        b"---\n"
        b"title: A guide\n"
        b"author: Jane Doe\n"
        b"lastModified: 2026-08-01\n"
        b"draft: true\n"
        b"---\n"
        b"\n"
        b"Body text.\n"
    )


def test_canonical_guide_bytes_omits_absent_optional_frontmatter_keys():
    canonical = canonical_guide_bytes({"title": "A guide"}, "Body text.")

    assert canonical == b"---\ntitle: A guide\n---\n\nBody text.\n"


def test_canonical_guide_bytes_normalises_nfd_text_and_trailing_newlines():
    nfd_title = unicodedata.normalize("NFD", "Café guide")

    canonical = canonical_guide_bytes({"title": nfd_title}, "Body.\n\n\n")

    assert canonical == (
        f"---\ntitle: {unicodedata.normalize('NFC', 'Café guide')}\n---\n\nBody.\n"
    ).encode("utf-8")
