from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from blindgaming_registry_validator.formatting import canonical_guide_bytes, canonical_record_bytes
from blindgaming_registry_validator.policy import classify_store_url
from blindgaming_registry_validator.validation import validate_snapshot


FIXTURES = Path(__file__).parent / "fixtures" / "valid" / "registry"


def _fixture(kind: str, identifier: str) -> dict[str, object]:
    return json.loads((FIXTURES / f"{kind}s" / f"{identifier}.json").read_text())


def _write(root: Path, kind: str, record: dict[str, object], *, filename: str | None = None) -> None:
    path = root / "registry" / f"{kind}s" / f"{filename or record['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_record_bytes(record, kind))


def _steam_listing(store_key: str, identifier: str, *, primary: bool = True) -> dict[str, object]:
    listing: dict[str, object] = {
        "store": "steam",
        "url": f"https://store.steampowered.com/app/{store_key}/{identifier}/",
    }
    if primary:
        listing["primary"] = True
    return listing


def _replace_steam_listing(record: dict[str, object], store_key: str) -> None:
    record["stores"] = [_steam_listing(store_key, str(record["id"]))]


def _game(identifier: str, store_key: str, name: str | None = None) -> dict[str, object]:
    record = _fixture("game", "example-game")
    record.update(id=identifier, name=name or identifier.replace("-", " ").title())
    record["stores"] = [_steam_listing(store_key, identifier)]
    record["accessibility"].update(accessVia="native")
    record["accessibility"].pop("primaryModId", None)
    record["accessibility"].pop("wikiRatingUrl", None)
    record.pop("releaseYear", None)
    return record


def _mod(identifier: str, game_id: str) -> dict[str, object]:
    record = _fixture("mod", "example-accessibility-mod")
    record.update(id=identifier, gameIds=[game_id], name=identifier.replace("-", " ").title())
    record["homeUrl"] = f"https://github.com/example/{identifier}"
    record["releaseSource"] = {
        "kind": "github",
        "repositoryUrl": f"https://github.com/example/{identifier}",
    }
    record["links"] = []
    return record


def _archive(record: dict[str, object], *, replaced_by: str | None = None) -> None:
    record.update(status="archived", archivedAt="2026-08-16", archiveReason="No longer maintained.")
    if "stores" in record:
        reserved: dict[str, list[str]] = {}
        for entry in record["stores"]:
            identity = classify_store_url(entry["url"])
            if identity is not None:
                reserved.setdefault(identity[0], []).append(identity[1])
        record["reservedStores"] = {
            store: sorted(keys) for store, keys in sorted(reserved.items())
        }
    if replaced_by is not None:
        record["replacedBy"] = replaced_by


def _errors(report) -> list[tuple[str, str, str]]:
    return [
        (diagnostic.path, diagnostic.pointer, diagnostic.message)
        for diagnostic in report.diagnostics
        if diagnostic.level == "error"
    ]


def _write_guide(
    root: Path,
    identifier: str,
    *,
    title: str = "A guide",
    author: str | None = None,
    last_modified: str | None = None,
    body: str = "Body text.",
) -> None:
    frontmatter: dict[str, str] = {"title": title}
    if author is not None:
        frontmatter["author"] = author
    if last_modified is not None:
        frontmatter["lastModified"] = last_modified
    path = root / "registry" / "guides" / f"{identifier}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_guide_bytes(frontmatter, body))


def test_a_game_without_an_accessibility_summary_or_tags_validates(tmp_path):
    game = _game("summary-free-game", "10")
    game["accessibility"].pop("description", None)
    game["accessibility"].pop("tags", None)
    _write(tmp_path, "game", game)

    report = validate_snapshot(tmp_path, base_root=tmp_path)

    assert _errors(report) == []


def test_game_resources_are_generic_named_https_links(tmp_path):
    game = _game("resource-game", "9")
    game["resources"] = [
        {
            "name": "Official accessibility guide",
            "url": "https://example.com/accessibility-guide",
        }
    ]
    _write(tmp_path, "game", game)

    report = validate_snapshot(tmp_path)

    assert report.ok, report.diagnostics


def test_game_resource_urls_follow_generic_url_policy(tmp_path):
    game = _game("tracked-resource-game", "109")
    game["resources"] = [
        {
            "name": "Discussion",
            "url": "https://example.com/thread?utm_source=newsletter",
        }
    ]
    _write(tmp_path, "game", game)

    report = validate_snapshot(tmp_path)

    assert (
        "registry/games/tracked-resource-game.json",
        "/resources/0/url",
        "URL tracking and affiliate parameters are forbidden",
    ) in _errors(report)


def test_global_store_identities_are_unique_even_across_archived_games(tmp_path):
    first = _game("first-game", "10")
    second = _game("second-game", "10")
    _archive(first)
    _write(tmp_path, "game", first)
    _write(tmp_path, "game", second)

    report = validate_snapshot(tmp_path, base_root=tmp_path)

    assert (
        "registry/games/second-game.json",
        "/stores/0",
        "store identity steam:10 is already owned by game first-game",
    ) in _errors(report)


def test_store_identity_collision_spans_canonical_and_supplementary_listings(tmp_path):
    first = _game("canonical-owner", "110")
    second = _game("supplementary-owner", "111")
    gog_listing = {
        "store": "gog",
        "url": "https://www.gog.com/game/shared_game",
    }
    second["stores"].append(deepcopy(gog_listing))
    first["stores"].append(gog_listing)
    _write(tmp_path, "game", first)
    _write(tmp_path, "game", second)

    report = validate_snapshot(tmp_path)

    assert (
            "registry/games/supplementary-owner.json",
            "/stores/1",
            "store identity gog:shared_game is already owned by game canonical-owner",
        ) in _errors(report)


def test_mod_must_reference_an_existing_game(tmp_path):
    _write(tmp_path, "mod", _mod("orphan-mod", "missing-game"))

    report = validate_snapshot(tmp_path)

    assert _errors(report) == [
        (
            "registry/mods/orphan-mod.json",
            "/gameIds/0",
            "referenced game 'missing-game' does not exist",
        )
    ]


@pytest.mark.parametrize(
    ("mod_game", "mod_status", "message"),
    [
        ("other-game", "active", "primary mod 'primary-mod' does not cover game 'main-game' (covers other-game)"),
        ("main-game", "archived", "primary mod 'primary-mod' must be active"),
    ],
)
def test_primary_mod_must_be_active_and_belong_to_its_game(tmp_path, mod_game, mod_status, message):
    main = _game("main-game", "12")
    other = _game("other-game", "13")
    main["accessibility"].update(accessVia="mod", primaryModId="primary-mod")
    mod = _mod("primary-mod", mod_game)
    if mod_status == "archived":
        _archive(mod)
    _write(tmp_path, "game", main)
    _write(tmp_path, "game", other)
    _write(tmp_path, "mod", mod)

    report = validate_snapshot(tmp_path, base_root=tmp_path)

    assert ("registry/games/main-game.json", "/accessibility/primaryModId", message) in _errors(report)


def test_primary_mod_must_exist(tmp_path):
    game = _game("main-game", "112")
    game["accessibility"].update(accessVia="mod", primaryModId="missing-mod")
    _write(tmp_path, "game", game)

    report = validate_snapshot(tmp_path)

    assert (
        "registry/games/main-game.json",
        "/accessibility/primaryModId",
        "primary mod 'missing-mod' does not exist",
    ) in _errors(report)


def test_duplicate_kind_and_url_mod_links_are_rejected(tmp_path):
    game = _game("linked-game", "14")
    mod = _mod("linked-mod", game["id"])
    mod["links"] = [
        {
            "kind": "download",
            "label": "Primary",
            "url": "https://example.com/file",
            "requiresPayment": False,
        },
        {
            "kind": "download",
            "label": "Mirror label",
            "url": "https://example.com/file",
            "requiresPayment": False,
        },
    ]
    _write(tmp_path, "game", game)
    _write(tmp_path, "mod", mod)

    report = validate_snapshot(tmp_path)

    assert (
        "registry/mods/linked-mod.json",
        "/links",
        "duplicate mod link pair (download, https://example.com/file)",
    ) in _errors(report)


def test_distinct_mods_may_share_project_and_repository_urls(tmp_path):
    first_game = _game("first-game", "15")
    second_game = _game("second-game", "16")
    first_mod = _mod("first-mod", first_game["id"])
    second_mod = _mod("second-mod", second_game["id"])
    for mod in (first_mod, second_mod):
        mod["homeUrl"] = "https://github.com/example/shared-project"
        mod["releaseSource"]["repositoryUrl"] = "https://github.com/example/shared-project"
    for record in (first_game, second_game):
        _write(tmp_path, "game", record)
    for record in (first_mod, second_mod):
        _write(tmp_path, "mod", record)

    report = validate_snapshot(tmp_path)

    assert report.ok, report.diagnostics


def test_release_year_cannot_be_after_next_calendar_year(tmp_path):
    game = _game("future-game", "17")
    game["releaseYear"] = str(date.today().year + 2)
    _write(tmp_path, "game", game)

    report = validate_snapshot(tmp_path)

    assert (
        "registry/games/future-game.json",
        "/releaseYear",
        f"release year must be between 1950 and {date.today().year + 1}",
    ) in _errors(report)


def test_archive_date_must_be_a_real_calendar_date(tmp_path):
    game = _game("bad-date-game", "113")
    _archive(game)
    game["archivedAt"] = "2026-99-99"
    _write(tmp_path, "game", game)

    report = validate_snapshot(tmp_path, base_root=tmp_path)

    assert (
        "registry/games/bad-date-game.json",
        "/archivedAt",
        "archive date must be a real calendar date in YYYY-MM-DD form",
    ) in _errors(report)


def test_bootstrap_and_post_bootstrap_additions_must_start_active(tmp_path):
    bootstrap = tmp_path / "bootstrap"
    archived = _game("already-archived", "18")
    _archive(archived)
    _write(bootstrap, "game", archived)

    bootstrap_report = validate_snapshot(bootstrap)

    assert (
        "registry/games/already-archived.json",
        "/status",
        "bootstrap records must be active",
    ) in _errors(bootstrap_report)

    base = tmp_path / "base"
    current = tmp_path / "current"
    _write(base, "game", _game("existing-game", "19"))
    _write(current, "game", _game("existing-game", "19"))
    _write(current, "game", archived)

    current_report = validate_snapshot(current, base_root=base)

    assert (
        "registry/games/already-archived.json",
        "/status",
        "new records must be active",
    ) in _errors(current_report)


def test_history_rejects_deleted_files_and_id_mutation(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    deleted = _game("deleted-game", "20")
    stable = _game("stable-game", "21")
    stable_mod = _mod("stable-mod", stable["id"])
    for game in (deleted, stable):
        _write(base, "game", game)
    _write(base, "mod", stable_mod)
    _write(current, "game", stable)
    _write(current, "mod", stable_mod)

    report = validate_snapshot(current, base_root=base)

    assert (
        "registry/games/deleted-game.json",
        "",
        "published registry record files must not be deleted",
    ) in _errors(report)
    bad_id_root = tmp_path / "bad-id"
    changed_id = _game("changed-id", "22")
    _write(bad_id_root, "game", changed_id, filename="original-id")
    id_report = validate_snapshot(bad_id_root)
    assert _errors(id_report) == [
        (
            "registry/games/original-id.json",
            "/id",
            "filename stem must match record id",
        )
    ]


def test_history_rejects_dropping_a_published_mod_game_binding(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    first_game = _game("first-game", "216")
    second_game = _game("second-game", "217")
    original = _mod("stable-mod", first_game["id"])
    rebound = deepcopy(original)
    rebound["gameIds"] = [second_game["id"]]
    for root in (base, current):
        _write(root, "game", first_game)
        _write(root, "game", second_game)
    _write(base, "mod", original)
    _write(current, "mod", rebound)

    report = validate_snapshot(current, base_root=base)

    assert (
        "registry/mods/stable-mod.json",
        "/gameIds",
        "published mod game bindings are monotonic; 'first-game' may not be removed",
    ) in _errors(report)


def test_archived_store_identity_cannot_be_reassigned(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    archived = _game("archived-owner", "210")
    _archive(archived)
    _write(base, "game", archived)

    changed_archived = deepcopy(archived)
    _replace_steam_listing(changed_archived, "211")
    new_owner = _game("new-owner", "210")
    _write(current, "game", changed_archived)
    _write(current, "game", new_owner)

    report = validate_snapshot(current, base_root=base)

    assert (
        "registry/games/new-owner.json",
        "/stores/0",
        "store identity steam:210 is reserved by archived game archived-owner",
    ) in _errors(report)


def test_archived_store_identity_cannot_be_removed_before_later_reassignment(tmp_path):
    active_root = tmp_path / "active"
    archived_root = tmp_path / "archived"
    removal_root = tmp_path / "removal"
    reassignment_root = tmp_path / "reassignment"
    active = _game("archived-owner", "214")
    archived = deepcopy(active)
    _archive(archived)
    _write(active_root, "game", active)
    _write(archived_root, "game", archived)

    archive_report = validate_snapshot(archived_root, base_root=active_root)

    assert archive_report.ok, archive_report.diagnostics

    changed_archived = deepcopy(archived)
    _replace_steam_listing(changed_archived, "215")
    _write(removal_root, "game", changed_archived)

    removal_report = validate_snapshot(removal_root, base_root=archived_root)

    assert (
        "registry/games/archived-owner.json",
        "/reservedStores",
        "archived game must reserve current store identity steam:215",
    ) in _errors(removal_report)

    _write(reassignment_root, "game", archived)
    _write(reassignment_root, "game", _game("new-owner", "214"))

    reassignment_report = validate_snapshot(reassignment_root, base_root=archived_root)

    assert (
        "registry/games/new-owner.json",
        "/stores/0",
        "store identity steam:214 is already owned by game archived-owner",
    ) in _errors(reassignment_report)


def test_unarchived_store_identity_cannot_be_freed_then_reassigned(tmp_path):
    archived_root = tmp_path / "archived"
    unarchived_root = tmp_path / "unarchived"
    freeing_root = tmp_path / "freeing"
    reassignment_root = tmp_path / "reassignment"
    archived = _game("archived-owner", "218")
    _archive(archived)
    unarchived = deepcopy(archived)
    unarchived["status"] = "active"
    for field in ("archivedAt", "archiveReason", "replacedBy"):
        unarchived.pop(field, None)
    _write(archived_root, "game", archived)
    _write(unarchived_root, "game", unarchived)

    unarchive_report = validate_snapshot(unarchived_root, base_root=archived_root)

    assert unarchive_report.ok, unarchive_report.diagnostics

    changed = deepcopy(unarchived)
    _replace_steam_listing(changed, "219")
    _write(freeing_root, "game", changed)

    freeing_report = validate_snapshot(freeing_root, base_root=unarchived_root)

    assert freeing_report.ok, freeing_report.diagnostics

    _write(reassignment_root, "game", changed)
    _write(reassignment_root, "game", _game("new-owner", "218"))

    reassignment_report = validate_snapshot(
        reassignment_root,
        base_root=freeing_root,
    )

    assert not reassignment_report.ok
    assert any(
        path == "registry/games/new-owner.json" and "steam:218" in message
        for path, _, message in _errors(reassignment_report)
    )


def test_always_active_store_identity_can_move_during_reviewed_correction(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    original = _game("original-owner", "212")
    _write(base, "game", original)
    changed = deepcopy(original)
    _replace_steam_listing(changed, "213")
    _write(current, "game", changed)
    _write(current, "game", _game("corrected-owner", "212"))

    report = validate_snapshot(current, base_root=base)

    assert report.ok, report.diagnostics


def test_archive_transition_reserves_every_prior_and_current_store_identity(tmp_path):
    base = tmp_path / "base"
    incomplete = tmp_path / "incomplete"
    complete = tmp_path / "complete"
    squatted = tmp_path / "squatted"
    original = _game("archive-boundary", "220")
    _write(base, "game", original)

    archived = deepcopy(original)
    _replace_steam_listing(archived, "221")
    _archive(archived)
    _write(incomplete, "game", archived)

    incomplete_report = validate_snapshot(incomplete, base_root=base)

    assert (
        "registry/games/archive-boundary.json",
        "/reservedStores",
        "archive transition must reserve store identity steam:220",
    ) in _errors(incomplete_report)

    archived["reservedStores"].setdefault("steam", []).append("220")
    archived["reservedStores"]["steam"] = sorted(archived["reservedStores"]["steam"])
    _write(complete, "game", archived)

    complete_report = validate_snapshot(complete, base_root=base)

    assert complete_report.ok, complete_report.diagnostics

    archived["reservedStores"]["gog"] = ["unowned"]
    archived["reservedStores"] = dict(sorted(archived["reservedStores"].items()))
    _write(squatted, "game", archived)

    squatted_report = validate_snapshot(squatted, base_root=base)

    assert (
        "registry/games/archive-boundary.json",
        "/reservedStores/gog",
        "store reservation gog:unowned was not owned at the archive transition",
    ) in _errors(squatted_report)


@pytest.mark.parametrize("reserved_key", ["222", "unowned-key"])
def test_active_record_cannot_introduce_a_store_reservation(tmp_path, reserved_key):
    base = tmp_path / "base"
    current = tmp_path / "current"
    game = _game("reservation-squatter", "222")
    _write(base, "game", game)
    proposed = deepcopy(game)
    proposed["reservedStores"] = {"steam": [reserved_key]}
    _write(current, "game", proposed)

    report = validate_snapshot(current, base_root=base)

    assert (
        "registry/games/reservation-squatter.json",
        "/reservedStores/steam",
        f"new store reservation steam:{reserved_key} may only be introduced by an archive transition",
    ) in _errors(report)


def test_store_reservations_are_monotonic_after_unarchive(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    game = _game("former-archive", "224")
    game["reservedStores"] = {"steam": ["223"]}
    _write(base, "game", game)
    proposed = deepcopy(game)
    proposed.pop("reservedStores")
    _write(current, "game", proposed)

    report = validate_snapshot(current, base_root=base)

    assert (
        "registry/games/former-archive.json",
        "/reservedStores",
        "reserved store identity steam:223 is immutable",
    ) in _errors(report)


def test_active_bootstrap_record_cannot_introduce_store_reservations(tmp_path):
    game = _game("bootstrap-squatter", "225")
    game["reservedStores"] = {"steam": ["225"]}
    _write(tmp_path, "game", game)

    report = validate_snapshot(tmp_path)

    assert (
        "registry/games/bootstrap-squatter.json",
        "/reservedStores/steam",
        "active bootstrap records cannot introduce store reservations",
    ) in _errors(report)


def test_store_reservations_are_globally_exclusive_but_allow_the_owner(tmp_path):
    first = _game("first-owner", "226")
    second = _game("second-owner", "227")
    first["reservedStores"] = {"steam": ["226"]}
    second["reservedStores"] = {"steam": ["226"]}
    _write(tmp_path, "game", first)
    _write(tmp_path, "game", second)

    report = validate_snapshot(tmp_path, base_root=tmp_path)

    assert _errors(report) == [
        (
            "registry/games/second-owner.json",
            "/reservedStores/steam",
            "store identity steam:226 is already reserved by game first-owner",
        )
    ]


def test_explicit_archive_and_unarchive_transitions_are_valid(tmp_path):
    active_root = tmp_path / "active"
    archived_root = tmp_path / "archived"
    restored_root = tmp_path / "restored"
    active = _game("lifecycle-game", "23")
    archived = deepcopy(active)
    _archive(archived)
    restored = deepcopy(archived)
    restored["status"] = "active"
    for field in ("archivedAt", "archiveReason", "replacedBy"):
        restored.pop(field, None)
    _write(active_root, "game", active)
    _write(archived_root, "game", archived)
    _write(restored_root, "game", restored)

    archive_report = validate_snapshot(archived_root, base_root=active_root)
    restore_report = validate_snapshot(restored_root, base_root=archived_root)

    assert archive_report.ok, archive_report.diagnostics
    assert restore_report.ok, restore_report.diagnostics


@pytest.mark.parametrize("kind", ["game", "mod"])
def test_replacement_must_exist_with_same_type_and_be_active(tmp_path, kind):
    if kind == "game":
        record = _game("old-record", "24")
        wrong_type_target = _mod("replacement", "old-record")
        _write(tmp_path, "mod", wrong_type_target)
    else:
        owner = _game("owner-game", "25")
        _write(tmp_path, "game", owner)
        record = _mod("old-record", owner["id"])
    _archive(record, replaced_by="replacement")
    _write(tmp_path, kind, record)

    report = validate_snapshot(tmp_path, base_root=tmp_path)

    assert (
        f"registry/{kind}s/old-record.json",
        "/replacedBy",
        f"replacement 'replacement' must reference an existing {kind}",
    ) in _errors(report)

    target = _game("replacement", "26") if kind == "game" else _mod("replacement", "owner-game")
    _archive(target)
    _write(tmp_path, kind, target)
    archived_report = validate_snapshot(tmp_path, base_root=tmp_path)
    assert (
        f"registry/{kind}s/old-record.json",
        "/replacedBy",
        "replacement 'replacement' must be active",
    ) in _errors(archived_report)


def test_replacement_cycles_and_self_replacement_are_rejected(tmp_path):
    first = _game("first-game", "27")
    second = _game("second-game", "28")
    self_replacing = _game("self-game", "29")
    _archive(first, replaced_by="second-game")
    _archive(second, replaced_by="first-game")
    _archive(self_replacing, replaced_by="self-game")
    for game in (first, second, self_replacing):
        _write(tmp_path, "game", game)

    report = validate_snapshot(tmp_path, base_root=tmp_path)

    assert any(pointer == "/replacedBy" and "replacement cycle" in message for _, pointer, message in _errors(report))
    assert (
        "registry/games/self-game.json",
        "/replacedBy",
        "record cannot replace itself",
    ) in _errors(report)


@pytest.mark.parametrize(
    ("first_name", "second_name"),
    [
        ("The Last of Us Part One", "The Last of Us Part Ones"),
        ("Example Game", "Example Game!"),
    ],
)
def test_similar_game_titles_warn_with_ids_and_store_identities_without_failing(
    tmp_path, first_name, second_name
):
    first = _game("last-of-us-one", "30", first_name)
    second = _game("last-of-us-1", "31", second_name)
    _write(tmp_path, "game", first)
    _write(tmp_path, "game", second)

    report = validate_snapshot(tmp_path)

    warnings = [diagnostic for diagnostic in report.diagnostics if diagnostic.level == "warning"]
    assert report.ok
    assert len(warnings) == 1
    assert warnings[0].path == "registry/games/last-of-us-one.json"
    assert warnings[0].pointer == "/name"
    assert all(value in warnings[0].message for value in ("last-of-us-1", "steam:31", "last-of-us-one", "steam:30"))


def test_mass_change_counts_each_changed_record_once_and_requires_override(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    for index in range(101):
        identifier = f"game-{index}"
        original = _game(identifier, str(1000 + index), f"Original title token {index}")
        changed = deepcopy(original)
        changed["name"] = f"Changed title token {index}"
        changed["releaseYear"] = "2025"
        _write(base, "game", original)
        _write(current, "game", changed)

    report = validate_snapshot(current, base_root=base)
    override_report = validate_snapshot(current, base_root=base, allow_mass_change=True)

    assert (
        "registry/games/game-0.json",
        "",
        "mass change modifies 101 records; limit is 100 without explicit authorization",
    ) in _errors(report)
    assert override_report.ok, override_report.diagnostics


def test_unauthorized_mass_change_stops_before_title_similarity_warnings(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    for index in range(101):
        identifier = f"game-{index}"
        original = _game(identifier, str(3000 + index), f"Original distinct title {index}")
        changed = deepcopy(original)
        changed["name"] = "One repeated proposed title"
        _write(base, "game", original)
        _write(current, "game", changed)

    report = validate_snapshot(current, base_root=base)

    assert [diagnostic.level for diagnostic in report.diagnostics] == ["error"]
    assert "mass change modifies 101 records" in report.diagnostics[0].message


def test_large_bootstrap_title_scan_has_bounded_runtime_and_diagnostics(tmp_path):
    for index in range(400):
        identifier = f"game-{index}"
        dissimilar_title = hashlib.sha256(str(index).encode("ascii")).hexdigest()
        _write(
            tmp_path,
            "game",
            _game(identifier, str(4000 + index), dissimilar_title),
        )

    started = time.perf_counter()
    report = validate_snapshot(tmp_path)
    elapsed = time.perf_counter() - started

    warnings = [
        diagnostic for diagnostic in report.diagnostics if diagnostic.level == "warning"
    ]
    assert report.ok
    # The truncation assertions below pin the scan cost. This timing bound only
    # catches an unbounded quadratic scan, so it stays loose enough for shared CI.
    assert elapsed < 10.0
    assert len(warnings) <= 101
    truncations = [
        warning
        for warning in warnings
        if "title similarity scan stopped after" in warning.message
    ]
    assert len(truncations) == 1
    assert "remaining pairs were not compared" in truncations[0].message


def test_below_threshold_title_changes_compare_every_changed_title(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    records: list[dict[str, object]] = []
    for index in range(414):
        identifier = f"game-{index:03d}"
        title = hashlib.sha256(f"base-{index}".encode("ascii")).hexdigest()
        record = _game(identifier, str(6000 + index), title)
        records.append(record)
        _write(base, "game", record)

    matching_title = records[413]["name"]
    for index, record in enumerate(records):
        proposed = deepcopy(record)
        if index < 24:
            proposed["name"] = hashlib.sha256(
                f"changed-{index}".encode("ascii")
            ).hexdigest()
        elif index == 24:
            proposed["name"] = matching_title
        _write(current, "game", proposed)

    report = validate_snapshot(current, base_root=base)

    warnings = [
        diagnostic for diagnostic in report.diagnostics if diagnostic.level == "warning"
    ]
    assert report.ok, report.diagnostics
    assert not any("scan stopped" in warning.message for warning in warnings)
    assert any(
        all(
            value in warning.message
            for value in ("game-024", "steam:6024", "game-413", "steam:6413")
        )
        for warning in warnings
    )


def test_diagnostics_are_stably_sorted(tmp_path):
    later = _game("z-game", "40")
    earlier = _game("a-game", "41")
    later["stores"][0]["store"] = "gog"
    earlier["releaseYear"] = str(date.today().year + 2)
    _write(tmp_path, "game", later)
    _write(tmp_path, "game", earlier)

    report = validate_snapshot(tmp_path)

    assert report.diagnostics == tuple(
        sorted(
            report.diagnostics,
            key=lambda item: (item.path, item.pointer, item.level, item.message),
        )
    )


@pytest.mark.parametrize(
    ("mutate", "pointer", "message"),
    [
        (lambda game: game.pop("name"), "", "'name' is a required property"),
        (lambda game: game.update(name=5), "/name", "5 is not of type 'string'"),
    ],
)
def test_structural_loader_failure_becomes_a_filename_scoped_schema_diagnostic(
    tmp_path, mutate, pointer, message
):
    game = _game("missing-name", "42")
    mutate(game)
    _write(tmp_path, "game", game)

    report = validate_snapshot(tmp_path)

    assert report.snapshot is None
    assert _errors(report) == [
        (
            "registry/games/missing-name.json",
            pointer,
            message,
        )
    ]


def test_parser_canonical_and_structural_failures_are_aggregated_stably(tmp_path):
    invalid_path = tmp_path / "registry" / "games" / "a-invalid-json.json"
    invalid_path.parent.mkdir(parents=True)
    invalid_path.write_text("{\n", encoding="utf-8")

    duplicate = _game("b-duplicate", "5100")
    duplicate_path = tmp_path / "registry" / "games" / "b-duplicate.json"
    duplicate_bytes = canonical_record_bytes(duplicate, "game")
    duplicate_path.write_bytes(
        duplicate_bytes.replace(
            b'  "id": "b-duplicate",',
            b'  "id": "b-duplicate",\n  "id": "b-duplicate",',
        )
    )

    noncanonical = _game("c-noncanonical", "5101")
    noncanonical_path = tmp_path / "registry" / "games" / "c-noncanonical.json"
    noncanonical_path.write_text(
        json.dumps(noncanonical, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )

    structural = _game("d-missing-name", "5102")
    structural.pop("name")
    _write(tmp_path, "game", structural)

    report = validate_snapshot(tmp_path)

    assert report.snapshot is None
    assert _errors(report) == [
        ("registry/games/a-invalid-json.json", "", "invalid JSON"),
        ("registry/games/b-duplicate.json", "/id", "duplicate JSON object key"),
        (
            "registry/games/c-noncanonical.json",
            "",
            "record is not canonically formatted",
        ),
        (
            "registry/games/d-missing-name.json",
            "",
            "'name' is a required property",
        ),
    ]


def test_filename_id_failure_is_aggregated_with_other_record_failures(tmp_path):
    wrong_id = _game("actual-id", "5200")
    _write(tmp_path, "game", wrong_id, filename="a-wrong-filename")

    invalid_path = tmp_path / "registry" / "games" / "b-invalid.json"
    invalid_path.write_text("{\n", encoding="utf-8")

    structural = _game("c-missing-name", "5201")
    structural.pop("name")
    _write(tmp_path, "game", structural)

    report = validate_snapshot(tmp_path)

    assert report.snapshot is None
    assert _errors(report) == [
        (
            "registry/games/a-wrong-filename.json",
            "/id",
            "filename stem must match record id",
        ),
        ("registry/games/b-invalid.json", "", "invalid JSON"),
        (
            "registry/games/c-missing-name.json",
            "",
            "'name' is a required property",
        ),
    ]


def test_non_object_record_becomes_a_filename_scoped_schema_diagnostic(tmp_path):
    path = tmp_path / "registry" / "games" / "not-an-object.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]\n")

    report = validate_snapshot(tmp_path)

    assert report.snapshot is None
    assert _errors(report) == [
        (
            "registry/games/not-an-object.json",
            "",
            "[] is not of type 'object'",
        )
    ]


def test_unpaired_surrogate_has_a_filename_and_pointer_diagnostic(tmp_path):
    game = _game("bad-unicode", "43")
    path = tmp_path / "registry" / "games" / "bad-unicode.json"
    path.parent.mkdir(parents=True)
    game["name"] = "\ud800"
    path.write_text(json.dumps(game, ensure_ascii=True, indent=2) + "\n")

    report = validate_snapshot(tmp_path)

    assert report.snapshot is None
    assert _errors(report) == [
        (
            "registry/games/bad-unicode.json",
            "/name",
            "text must contain only Unicode scalar values",
        )
    ]


def test_a_minimal_guide_validates(tmp_path):
    _write_guide(tmp_path, "screen-reader-basics", title="Screen reader basics")

    report = validate_snapshot(tmp_path)

    assert report.ok, report.diagnostics
    assert [guide.id for guide in report.snapshot.guides] == ["screen-reader-basics"]


def test_a_guide_with_an_invalid_filename_is_flagged(tmp_path):
    _write_guide(tmp_path, "Not_A_Valid_Id", title="A guide")

    report = validate_snapshot(tmp_path)

    assert (
        "registry/guides/Not_A_Valid_Id.md",
        "",
        "guide filename must be a valid registry id",
    ) in _errors(report)


def test_a_guide_with_html_in_its_title_is_flagged(tmp_path):
    _write_guide(tmp_path, "html-title", title="<b>bold</b> guide")

    report = validate_snapshot(tmp_path)

    assert (
        "registry/guides/html-title.md",
        "/title",
        "display text must be plain text without raw HTML",
    ) in _errors(report)


def test_a_guide_with_an_invalid_last_modified_date_is_flagged(tmp_path):
    _write_guide(tmp_path, "bad-date", title="A guide", last_modified="2026-13-40")

    report = validate_snapshot(tmp_path)

    assert (
        "registry/guides/bad-date.md",
        "/lastModified",
        "last modified date must be a real calendar date in YYYY-MM-DD form",
    ) in _errors(report)


def test_deleting_a_guide_is_not_a_published_record_deletion(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    _write_guide(base, "retired-guide", title="Retired guide")
    _write_guide(current, "kept-guide", title="Kept guide")

    report = validate_snapshot(current, base_root=base)

    assert report.ok, report.diagnostics
    assert not any(
        "must not be deleted" in message for _, _, message in _errors(report)
    )


def test_guide_only_changes_never_trigger_the_mass_change_gate(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    for index in range(101):
        _write_guide(base, f"guide-{index}", title=f"Original title {index}")
        _write_guide(current, f"guide-{index}", title=f"Changed title {index}")

    report = validate_snapshot(current, base_root=base)

    assert report.ok, report.diagnostics


def _mod_access_game(identifier: str, extra_listing: dict[str, object]) -> dict[str, object]:
    record = _game(identifier, "4100")
    record["stores"].append(extra_listing)
    record["accessibility"].update(accessVia="mod", primaryModId=f"{identifier}-mod")
    return record


@pytest.mark.parametrize(
    ("listing", "platform"),
    [
        ({"store": "appstore",
          "url": "https://apps.apple.com/us/app/mod-game/id1234567890"}, "mobile"),
        ({"store": "googleplay",
          "url": "https://play.google.com/store/apps/details?id=com.example.modgame"},
         "mobile"),
        ({"store": "xbox",
          "url": "https://www.xbox.com/en-US/games/store/mod-game/9NABCDEFGHIJ"},
         "console"),
    ],
)
def test_mod_access_games_cannot_list_console_or_mobile_stores(tmp_path, listing, platform):
    game = _mod_access_game("mod-game", listing)
    _write(tmp_path, "game", game)
    _write(tmp_path, "mod", _mod("mod-game-mod", "mod-game"))
    report = validate_snapshot(tmp_path)
    messages = [
        (diagnostic.pointer, diagnostic.message)
        for diagnostic in report.diagnostics
        if diagnostic.path.endswith("mod-game.json")
    ]
    assert (
        "/stores/1/store",
        f"a mod-access game cannot list a {platform} store; mods do not apply there",
    ) in messages


def test_native_access_games_may_list_mobile_stores(tmp_path):
    game = _game("native-game", "4200")
    game["stores"].append({
        "store": "googleplay",
        "url": "https://play.google.com/store/apps/details?id=com.example.nativegame",
    })
    _write(tmp_path, "game", game)
    assert _errors(validate_snapshot(tmp_path)) == []


def test_mod_access_game_with_unknown_store_does_not_crash(tmp_path):
    game = _mod_access_game(
        "unknown-store-mod-game",
        {"store": "epicgames", "url": "https://store.epicgames.com/en-US/p/mod-game"},
    )
    _write(tmp_path, "game", game)
    _write(tmp_path, "mod", _mod("unknown-store-mod-game-mod", "unknown-store-mod-game"))
    report = validate_snapshot(tmp_path)
    messages = [
        (diagnostic.pointer, diagnostic.message)
        for diagnostic in report.diagnostics
        if diagnostic.path.endswith("unknown-store-mod-game.json")
    ]
    assert not any(
        pointer == "/stores/1/store" and "mod-access game cannot list" in message
        for pointer, message in messages
    )


def test_native_access_game_with_console_store_gets_no_mod_gate_diagnostic(tmp_path):
    game = _game("console-native-game", "4300")
    game["accessibility"].pop("accessVia", None)
    game["stores"].append({
        "store": "xbox",
        "url": "https://www.xbox.com/en-US/games/store/console-native-game/9NABCDEFGHIK",
    })
    _write(tmp_path, "game", game)
    report = validate_snapshot(tmp_path)
    messages = [
        (diagnostic.pointer, diagnostic.message)
        for diagnostic in report.diagnostics
        if diagnostic.path.endswith("console-native-game.json")
    ]
    assert not any("mod-access game cannot list" in message for _, message in messages)


def test_archived_mod_access_game_with_console_listing_gets_no_mod_gate_diagnostic(tmp_path):
    game = _mod_access_game(
        "archived-mod-game",
        {
            "store": "xbox",
            "url": "https://www.xbox.com/en-US/games/store/archived-mod-game/9NABCDEFGHIL",
        },
    )
    _archive(game)
    _write(tmp_path, "game", game)
    _write(tmp_path, "mod", _mod("archived-mod-game-mod", "archived-mod-game"))
    report = validate_snapshot(tmp_path, base_root=tmp_path)
    assert report.ok, report.diagnostics


def test_unarchiving_a_mod_access_game_with_console_listing_gets_mod_gate_diagnostic(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    game = _mod_access_game(
        "unarchived-mod-game",
        {
            "store": "xbox",
            "url": "https://www.xbox.com/en-US/games/store/unarchived-mod-game/9NABCDEFGHIM",
        },
    )
    _archive(game)
    mod = _mod("unarchived-mod-game-mod", "unarchived-mod-game")
    _write(base, "game", game)
    _write(base, "mod", mod)

    unarchived = deepcopy(game)
    unarchived["status"] = "active"
    unarchived.pop("archivedAt", None)
    unarchived.pop("archiveReason", None)
    unarchived.pop("replacedBy", None)
    _write(current, "game", unarchived)
    _write(current, "mod", mod)

    report = validate_snapshot(current, base_root=base)

    messages = [
        (diagnostic.pointer, diagnostic.message)
        for diagnostic in report.diagnostics
        if diagnostic.path.endswith("unarchived-mod-game.json")
    ]
    assert (
        "/stores/1/store",
        "a mod-access game cannot list a console store; mods do not apply there",
    ) in messages


def test_one_hundred_changed_records_do_not_require_mass_change_override(tmp_path):
    base = tmp_path / "base"
    current = tmp_path / "current"
    for index in range(100):
        original = _game(f"game-{index}", str(10000 + index))
        changed = deepcopy(original)
        changed["releaseYear"] = "2025"
        _write(base, "game", original)
        _write(current, "game", changed)

    report = validate_snapshot(current, base_root=base)

    assert report.ok, report.diagnostics


@pytest.mark.parametrize("changed_count", [101, 102])
def test_mass_change_percentage_scales_above_the_hundred_record_floor(tmp_path, changed_count):
    base = tmp_path / "base"
    current = tmp_path / "current"
    for index in range(1001):
        original = _game(f"game-{index}", str(20000 + index))
        changed = deepcopy(original)
        if index < changed_count:
            changed["releaseYear"] = "2025"
        _write(base, "game", original)
        _write(current, "game", changed)

    report = validate_snapshot(current, base_root=base)

    if changed_count == 101:
        assert report.ok, report.diagnostics
    else:
        assert (
            "registry/games/game-0.json", "",
            "mass change modifies 102 records; limit is 101 without explicit authorization",
        ) in _errors(report)
