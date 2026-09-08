from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from blindgaming_registry_validator.schemas import schema_for


FIXTURES = Path(__file__).parent / "fixtures" / "valid" / "registry"


def load_fixture(kind: str, name: str) -> dict[str, Any]:
    with (FIXTURES / f"{kind}s" / f"{name}.json").open(encoding="utf-8") as source:
        return json.load(source)


def validation_errors(kind: str, record: Mapping[str, Any]) -> list[object]:
    return list(Draft202012Validator(schema_for(kind, 1)).iter_errors(record))


def format_validation_errors(kind: str, record: Mapping[str, Any]) -> list[object]:
    validator = Draft202012Validator(schema_for(kind, 1), format_checker=FormatChecker())
    return list(validator.iter_errors(record))


@pytest.mark.parametrize(
    ("kind", "name"),
    [("game", "example-game"), ("mod", "example-accessibility-mod")],
)
def test_version_1_schema_validates_its_contract_fixture(kind: str, name: str):
    record = load_fixture(kind, name)
    schema = schema_for(kind, 1)

    Draft202012Validator.check_schema(schema)

    assert not validation_errors(kind, record)


@pytest.mark.parametrize(
    ("kind", "name"),
    [("game", "example-game"), ("mod", "example-accessibility-mod")],
)
def test_version_1_schema_rejects_an_integer_valued_json_float_for_schema_version(
    kind: str, name: str
):
    from blindgaming_registry_validator.schemas import validator_for

    record = copy.deepcopy(load_fixture(kind, name))
    record["schemaVersion"] = 1.0

    assert list(validator_for(kind, 1).iter_errors(record))


def test_game_schema_supports_reserved_store_keys_for_archival():
    game = load_fixture("game", "example-game")
    game["reservedStores"] = {"steam": ["1234560"]}

    assert not validation_errors("game", game)

    unknown_store = copy.deepcopy(game)
    unknown_store["reservedStores"]["nintendo"] = ["1234560"]
    assert validation_errors("game", unknown_store)

    duplicate = copy.deepcopy(game)
    duplicate["reservedStores"]["steam"].append("1234560")
    assert validation_errors("game", duplicate)

    empty = copy.deepcopy(game)
    empty["reservedStores"]["steam"] = []
    assert validation_errors("game", empty)

    archived_without_reservations = load_fixture("game", "example-game")
    archived_without_reservations.update(
        status="archived",
        archivedAt="2026-08-16",
        archiveReason="No longer maintained.",
    )
    assert validation_errors("game", archived_without_reservations)


def test_game_schema_requires_one_primary_store_entry():
    game = load_fixture("game", "example-game")

    without_primary = copy.deepcopy(game)
    without_primary["stores"][0].pop("primary")
    assert validation_errors("game", without_primary)

    duplicate_primary = copy.deepcopy(game)
    duplicate_primary["stores"].append({
        "store": "gog",
        "primary": True,
        "url": "https://www.gog.com/game/example_game",
    })
    assert validation_errors("game", duplicate_primary)


def test_game_schema_accepts_battlenet_and_preserves_its_archived_identity():
    game = load_fixture("game", "example-game")
    game["stores"] = [{
        "store": "battlenet",
        "primary": True,
        "url": "https://shop.battle.net/en-us/product/hearthstone",
    }]
    assert not validation_errors("game", game)

    game.update(
        status="archived",
        archivedAt="2026-09-07",
        archiveReason="No longer maintained.",
        reservedStores={"battlenet": ["hearthstone"]},
    )
    assert not validation_errors("game", game)


def test_game_schema_accepts_accessibility_without_a_summary_or_tags():
    game = copy.deepcopy(load_fixture("game", "example-game"))
    del game["accessibility"]["description"]
    del game["accessibility"]["tags"]

    assert not validation_errors("game", game)

    empty_tags = copy.deepcopy(load_fixture("game", "example-game"))
    empty_tags["accessibility"]["tags"] = []
    assert not validation_errors("game", empty_tags)


def add_unknown_game_root(record: dict[str, Any]) -> None:
    record["unexpected"] = True


def add_unknown_store_entry_property(record: dict[str, Any]) -> None:
    record["stores"][0]["unexpected"] = True


def set_unknown_store_name(record: dict[str, Any]) -> None:
    record["stores"][0] = {
        "store": "nintendo",
        "url": "https://www.nintendo.com/store/loco/",
    }


def add_unknown_accessibility_property(record: dict[str, Any]) -> None:
    record["accessibility"]["unexpected"] = True


def add_unknown_resource_property(record: dict[str, Any]) -> None:
    record["resources"] = [
        {
            "name": "Accessibility notes",
            "url": "https://example.com/example-game-accessibility",
            "kind": "audiogames",
        }
    ]


def set_resources_to_kind_map(record: dict[str, Any]) -> None:
    record["resources"] = {
        "audiogames": {
            "name": "Accessibility notes",
            "url": "https://example.com/example-game-accessibility",
        }
    }


def add_unknown_mod_root(record: dict[str, Any]) -> None:
    record["unexpected"] = True


def add_unknown_release_source_property(record: dict[str, Any]) -> None:
    record["releaseSource"]["unexpected"] = True


def _download_link() -> dict[str, Any]:
    return {
        "kind": "download",
        "label": "Latest release",
        "url": "https://github.com/example/mod/releases",
        "requiresPayment": False,
    }


def add_unknown_mod_link_property(record: dict[str, Any]) -> None:
    record["links"] = [{**_download_link(), "unexpected": True}]


def set_explicit_null(record: dict[str, Any]) -> None:
    record["name"] = None


def set_blank_text(record: dict[str, Any]) -> None:
    record["name"] = " "


def set_bad_registry_id(record: dict[str, Any]) -> None:
    record["id"] = "Example Game"


def set_wrong_schema_version(record: dict[str, Any]) -> None:
    record["schemaVersion"] = 2


def add_active_archive_field(record: dict[str, Any]) -> None:
    record["archivedAt"] = "2026-08-16"


def remove_archived_fields(record: dict[str, Any]) -> None:
    record["status"] = "archived"


def set_impossible_archive_date(record: dict[str, Any]) -> None:
    record["status"] = "archived"
    record["archivedAt"] = "2026-99-99"
    record["archiveReason"] = "Superseded by a corrected registry record."


def set_blank_accessibility_description(record: dict[str, Any]) -> None:
    record["accessibility"]["description"] = " "


def set_confidence_without_coverage(record: dict[str, Any]) -> None:
    record["accessibility"].pop("coverage", None)
    record["accessibility"]["confidence"] = "verified"


def set_invalid_coverage(record: dict[str, Any]) -> None:
    record["accessibility"]["coverage"] = "verified_full"


def set_unknown_access_path(record: dict[str, Any]) -> None:
    record["accessibility"]["accessVia"] = "unknown"


def set_invalid_tag(record: dict[str, Any]) -> None:
    record["accessibility"]["tags"] = ["mobility"]


def set_duplicate_tag(record: dict[str, Any]) -> None:
    record["accessibility"]["tags"] = ["blind", "blind"]


def remove_required_primary_mod_id(record: dict[str, Any]) -> None:
    del record["accessibility"]["primaryModId"]


def add_forbidden_primary_mod_id(record: dict[str, Any]) -> None:
    record["accessibility"]["accessVia"] = "native"


def make_non_download_link_paid(record: dict[str, Any]) -> None:
    record["links"] = [{**_download_link(), "kind": "funding", "requiresPayment": True}]


def set_malformed_release_source(record: dict[str, Any]) -> None:
    record["releaseSource"]["repositoryUrl"] = "https://github.com/owner/repository/releases"


def set_nexus_url_under_github_kind(record: dict[str, Any]) -> None:
    record["releaseSource"]["repositoryUrl"] = "https://www.nexusmods.com/skyrimspecialedition/mods/181131"


def set_github_url_under_nexus_kind(record: dict[str, Any]) -> None:
    record["releaseSource"] = {
        "kind": "nexus",
        "repositoryUrl": "https://github.com/owner/repository",
    }


def set_nexus_files_tab_as_release_source(record: dict[str, Any]) -> None:
    record["releaseSource"] = {
        "kind": "nexus",
        "repositoryUrl": "https://www.nexusmods.com/skyrimspecialedition/mods/181131?tab=files",
    }


def set_unknown_release_source_kind(record: dict[str, Any]) -> None:
    record["releaseSource"]["kind"] = "gitlab"


Mutation = Callable[[dict[str, Any]], None]


@pytest.mark.parametrize(
    ("kind", "name", "mutation"),
    [
        ("game", "example-game", add_unknown_game_root),
        ("game", "example-game", add_unknown_store_entry_property),
        ("game", "example-game", set_unknown_store_name),
        ("game", "example-game", add_unknown_accessibility_property),
        ("game", "example-game", add_unknown_resource_property),
        ("game", "example-game", set_resources_to_kind_map),
        ("mod", "example-accessibility-mod", add_unknown_mod_root),
        ("mod", "example-accessibility-mod", add_unknown_release_source_property),
        ("mod", "example-accessibility-mod", add_unknown_mod_link_property),
        ("game", "example-game", set_explicit_null),
        ("mod", "example-accessibility-mod", set_blank_text),
        ("game", "example-game", set_bad_registry_id),
        ("game", "example-game", set_wrong_schema_version),
        ("mod", "example-accessibility-mod", set_wrong_schema_version),
        ("game", "example-game", add_active_archive_field),
        ("mod", "example-accessibility-mod", add_active_archive_field),
        ("game", "example-game", remove_archived_fields),
        ("mod", "example-accessibility-mod", remove_archived_fields),
        ("game", "example-game", set_blank_accessibility_description),
        ("game", "example-game", set_confidence_without_coverage),
        ("game", "example-game", set_invalid_coverage),
        ("game", "example-game", set_unknown_access_path),
        ("game", "example-game", set_invalid_tag),
        ("game", "example-game", set_duplicate_tag),
        ("game", "example-game", remove_required_primary_mod_id),
        ("game", "example-game", add_forbidden_primary_mod_id),
        ("mod", "example-accessibility-mod", make_non_download_link_paid),
        ("mod", "example-accessibility-mod", set_malformed_release_source),
        ("mod", "example-accessibility-mod", set_nexus_url_under_github_kind),
        ("mod", "example-accessibility-mod", set_github_url_under_nexus_kind),
        ("mod", "example-accessibility-mod", set_nexus_files_tab_as_release_source),
        ("mod", "example-accessibility-mod", set_unknown_release_source_kind),
    ],
    ids=[
        "game-root-is-closed",
        "store-entry-is-closed",
        "store-list-uses-known-stores",
        "accessibility-is-closed",
        "resource-entry-is-closed",
        "resources-are-not-keyed-by-kind",
        "mod-root-is-closed",
        "release-source-is-closed",
        "mod-link-is-closed",
        "null-is-not-a-name",
        "blank-text-is-rejected",
        "registry-id-format",
        "game-schema-version",
        "mod-schema-version",
        "active-game-has-no-archive-fields",
        "active-mod-has-no-archive-fields",
        "archived-game-requires-archive-fields",
        "archived-mod-requires-archive-fields",
        "present-accessibility-summary-is-not-blank",
        "confidence-requires-coverage",
        "coverage-vocabulary",
        "access-path-has-no-unknown-member",
        "registry-tag-vocabulary",
        "registry-tags-are-unique",
        "mod-access-requires-primary-mod",
        "non-mod-access-forbids-primary-mod",
        "only-download-links-may-require-payment",
        "release-source-is-a-github-repository",
        "github-release-source-rejects-nexus-url",
        "nexus-release-source-rejects-github-url",
        "nexus-release-source-is-a-bare-mod-page",
        "release-source-kind-vocabulary",
    ],
)
def test_version_1_schema_rejects_invalid_record_shape(
    kind: str, name: str, mutation: Mutation
):
    record = copy.deepcopy(load_fixture(kind, name))
    mutation(record)

    assert validation_errors(kind, record)


@pytest.mark.parametrize(("kind", "name"), [("game", "example-game"), ("mod", "example-accessibility-mod")])
def test_version_1_schema_declares_archive_dates_as_rfc_3339_full_dates(
    kind: str, name: str
):
    record = copy.deepcopy(load_fixture(kind, name))
    set_impossible_archive_date(record)

    assert format_validation_errors(kind, record)


@pytest.mark.parametrize("kind", ["game", "mod"])
def test_schema_for_rejects_unsupported_registry_schema_versions(kind: str):
    with pytest.raises(ValueError, match="unsupported registry schema version: 2"):
        schema_for(kind, 2)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.nexusmods.com/skyrimspecialedition/mods/181131",
        "https://nexusmods.com/stardewvalley/mods/16205",
    ],
)
def test_mod_schema_accepts_a_nexus_mods_release_source(url: str):
    record = copy.deepcopy(load_fixture("mod", "example-accessibility-mod"))
    record["releaseSource"] = {"kind": "nexus", "repositoryUrl": url}

    assert validation_errors("mod", record) == []


def test_game_schema_accepts_mobile_store_entries():
    game = load_fixture("game", "example-game")
    game["stores"].append({
        "store": "appstore",
        "url": "https://apps.apple.com/us/app/example-game/id1234567890",
    })
    game["stores"].append({
        "store": "googleplay",
        "url": "https://play.google.com/store/apps/details?id=com.example.game",
    })
    assert validation_errors("game", game) == []


def test_game_schema_allows_nine_store_entries():
    game = load_fixture("game", "example-game")
    extra = [
        ("gog", "https://www.gog.com/game/example_game"),
        ("humble", "https://www.humblebundle.com/store/example-game"),
        ("battlenet", "https://shop.battle.net/en-us/product/example-game"),
        ("psn", "https://store.playstation.com/en-us/concept/10000001"),
        ("xbox", "https://www.xbox.com/en-US/games/store/example-game/9NABCDEFGHIJ"),
        ("switch", "https://www.nintendo.com/us/store/products/example-game-switch/"),
        ("appstore", "https://apps.apple.com/us/app/example-game/id1234567890"),
        ("googleplay", "https://play.google.com/store/apps/details?id=com.example.game"),
    ]
    game["stores"] = [game["stores"][0]] + [
        {"store": store, "url": url} for store, url in extra]
    assert len(game["stores"]) == 9
    assert validation_errors("game", game) == []


def test_game_schema_rejects_ten_store_entries():
    game = load_fixture("game", "example-game")
    extra = [
        ("gog", "https://www.gog.com/game/example_game"),
        ("humble", "https://www.humblebundle.com/store/example-game"),
        ("battlenet", "https://shop.battle.net/en-us/product/example-game"),
        ("psn", "https://store.playstation.com/en-us/concept/10000001"),
        ("xbox", "https://www.xbox.com/en-US/games/store/example-game/9NABCDEFGHIJ"),
        ("switch", "https://www.nintendo.com/us/store/products/example-game-switch/"),
        ("appstore", "https://apps.apple.com/us/app/example-game/id1234567890"),
        ("googleplay", "https://play.google.com/store/apps/details?id=com.example.game"),
    ]
    game["stores"] = [game["stores"][0]] + [
        {"store": store, "url": url} for store, url in extra]
    game["stores"].append({
        "store": "gog",
        "url": "https://www.gog.com/game/example_game_alternate",
    })
    assert len(game["stores"]) == 10

    errors = validation_errors("game", game)

    assert errors
    assert any("maxItems" in str(error.validator) for error in errors)


def test_reserved_store_map_accepts_mobile_stores():
    game = load_fixture("game", "example-game")
    game["reservedStores"] = {}
    game["reservedStores"]["appstore"] = ["id1234567890"]
    game["reservedStores"]["googleplay"] = ["com.example.game"]
    game["status"] = "archived"
    game["archivedAt"] = "2026-08-16"
    game["archiveReason"] = "No longer maintained."
    assert validation_errors("game", game) == []
