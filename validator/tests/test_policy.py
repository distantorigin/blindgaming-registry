from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from blindgaming_registry_validator.formatting import canonical_record_bytes
from blindgaming_registry_validator.policy import (
    STORE_PLATFORM,
    classify_store_url,
    github_repository_url_error,
    platform_for_store,
    release_source_url_error,
    url_policy_error,
)
from blindgaming_registry_validator.validation import validate_snapshot


FIXTURES = Path(__file__).parent / "fixtures" / "valid" / "registry"


def _fixture(kind: str, identifier: str) -> dict[str, object]:
    return json.loads((FIXTURES / f"{kind}s" / f"{identifier}.json").read_text())


def _write(root: Path, kind: str, record: dict[str, object]) -> None:
    path = root / "registry" / f"{kind}s" / f"{record['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_record_bytes(record, kind))


@pytest.mark.parametrize(
    ("url", "identity"),
    [
        ("https://store.steampowered.com/app/2379780/Balatro/", ("steam", "2379780")),
        ("https://www.gog.com/en/game/diablo", ("gog", "diablo")),
        ("https://www.gog.com/game/diablo", ("gog", "diablo")),
        ("https://www.humblebundle.com/store/some-game", ("humble", "some-game")),
        ("https://shop.battle.net/en-us/product/hearthstone", ("battlenet", "hearthstone")),
        ("https://us.shop.battle.net/en-us/product/hearthstone", ("battlenet", "hearthstone")),
        ("https://eu.shop.battle.net/en-gb/product/hearthstone/", ("battlenet", "hearthstone")),
        (
            "https://store.playstation.com/en-us/product/UP1018-PPSA07570_00-MKONE00000000000",
            ("psn", "UP1018-PPSA07570_00-MKONE00000000000"),
        ),
        ("https://store.playstation.com/en-us/concept/10002861", ("psn", "concept/10002861")),
        (
            "https://www.xbox.com/en-US/games/store/mortal-kombat-1/9N7271QN4SGB",
            ("xbox", "9N7271QN4SGB"),
        ),
        (
            "https://www.xbox.com/en-US/play/games/stardew-valley/C3D891Z6TNQM",
            ("xbox", "C3D891Z6TNQM"),
        ),
        ("https://www.xbox.com/en-US/games/store/deep-rock-galactic", ("xbox", "deep-rock-galactic")),
        ("https://www.xbox.com/games/diablo-iv", ("xbox", "diablo-iv")),
        (
            "https://www.nintendo.com/us/store/products/mario-kart-8-deluxe-switch/",
            ("switch", "mario-kart-8-deluxe-switch"),
        ),
        (
            "https://apps.apple.com/us/app/example-game/id1234567890",
            ("appstore", "id1234567890"),
        ),
        ("https://apps.apple.com/gb/app/id1234567890", ("appstore", "id1234567890")),
        (
            "https://play.google.com/store/apps/details?id=com.example.game",
            ("googleplay", "com.example.game"),
        ),
    ],
)
def test_classify_store_url_ports_supported_application_identities(url, identity):
    assert classify_store_url(url) == identity


@pytest.mark.parametrize(
    "url",
    [
        "https://apps.apple.com/us/app/example-game/id1234567890?platform=mac",
        "https://apps.apple.com/us/mac/app/example-game/id1234567890",
        "https://apps.apple.com/us/app/example-game/id1234567890?mt=12",
        "https://play.google.com/store/apps/details?id=com.example.game&hl=en",
        "https://play.google.com/store/apps/details",
        "https://play.google.com/store/apps/details?hl=en",
        "https://play.google.com/store/apps/collection/topselling_free",
    ],
)
def test_classify_store_url_rejects_mac_app_store_and_malformed_play_urls(url):
    assert classify_store_url(url) is None


def test_google_play_id_query_is_allowed_by_url_policy():
    assert url_policy_error(
        "https://play.google.com/store/apps/details?id=com.example.game") is None
    assert url_policy_error(
        "https://play.google.com/store/apps/details?id=com.example.game&gl=us"
    ) == "URL query parameters are not allowed for this field"


def test_store_platform_map_covers_every_store_in_the_schema():
    common = json.loads(
        (Path(__file__).parents[2] / "schemas" / "v1" / "common.schema.json").read_text()
    )
    assert set(STORE_PLATFORM) == set(common["$defs"]["store"]["enum"])
    assert set(STORE_PLATFORM.values()) == {"pc", "console", "mobile"}
    assert platform_for_store("appstore") == "mobile"
    assert platform_for_store("googleplay") == "mobile"
    assert platform_for_store("battlenet") == "pc"
    assert platform_for_store("psn") == "console"
    assert platform_for_store("steam") == "pc"
    with pytest.raises(KeyError):
        platform_for_store("nintendo")


@pytest.mark.parametrize(
    "url",
    [
        "http://store.steampowered.com/app/10/name",
        "https://user:password@store.steampowered.com/app/10/name",
        "https://store.steampowered.com/app/10/name#reviews",
        "https://store.steampowered.com/app/10/name#",
        "https://store.steampowered.com:8443/app/10/name",
        "https://store.steampowered.com/app/10/name?utm_source=affiliate",
        "https://store.steampowered.com/app/10/name?unexpected=value",
        "https://store.steampowered.com/app/10/name?",
        "https://store.steampowered.com/%2fapp/10/name",
        "https://store.steampowered.com/%5Capp/10/name",
        "https://store.steampowered.com\\app/10/name",
        "https://store.steampowered.com.attacker.test/app/10/name",
        "https://attacker.test/app/10/name?next=https://store.steampowered.com/app/10/name",
        "https://127.0.0.1/app/10/name",
        "https://[2001:db8::1]/app/10/name",
        "https://localhost/app/10/name",
        "https://store.steampowered.com./app/10/name",
        "https://store.steampowered.com/app/10/bad path",
        "https://store.steampowered.com/app/10/%zz",
        "https://www.xbox.com/games/store",
        "https://shop.battle.net.attacker.test/en-us/product/hearthstone",
        "https://attacker.shop.battle.net/en-us/product/hearthstone",
        "https://shop.battle.net/en-us/product/",
        "https://shop.battle.net/en-us/family/hearthstone",
        "https://shop.battle.net/en-us/product/hearthstone?utm_source=affiliate",
        "https://shop.battle.net/en-us/product/hearthstone/download",
    ],
)
def test_classify_store_url_rejects_noncanonical_or_unsafe_urls(url):
    assert classify_store_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        # GitHub permits a trailing hyphen, underscore, or dot in a repository
        # name even though an owner name may not end in one.
        "https://github.com/FioraXena/Cookie-Clicker-Enhanced-NVDA-Accessibility-Steam-Only-",
        "https://github.com/owner/repo_",
        "https://github.com/owner/repo.",
        "https://github.com/owner/.hidden",
    ],
)
def test_github_repository_names_may_end_in_a_punctuation_character(url: str):
    assert github_repository_url_error(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner-/repo",
        "https://github.com/-owner/repo",
        "https://github.com/owner/.",
        "https://github.com/owner/..",
        "https://github.com/owner/repo.git",
        "https://github.com/owner/repo/",
        "https://github.com/owner",
    ],
)
def test_github_repository_url_rejects_non_canonical_forms(url: str):
    assert github_repository_url_error(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.nexusmods.com/skyrimspecialedition/mods/181131",
        "https://nexusmods.com/stardewvalley/mods/16205",
    ],
)
def test_nexus_release_source_accepts_a_bare_mod_page(url: str):
    assert release_source_url_error("nexus", url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.nexusmods.com/skyrimspecialedition/mods/181131?tab=files",
        "https://www.nexusmods.com/skyrimspecialedition/mods/181131/",
        "https://www.nexusmods.com/skyrimspecialedition/mods/",
        "https://www.nexusmods.com/skyrimspecialedition/mods/abc",
        "https://www.nexusmods.com/mods/181131",
        "https://github.com/owner/repo",
        "http://www.nexusmods.com/skyrimspecialedition/mods/181131",
    ],
)
def test_nexus_release_source_rejects_non_canonical_forms(url: str):
    assert release_source_url_error("nexus", url) is not None


def test_github_release_source_still_requires_a_github_repository():
    assert release_source_url_error("github", "https://github.com/owner/repo") is None
    assert release_source_url_error(
        "github", "https://www.nexusmods.com/skyrimspecialedition/mods/181131"
    ) is not None


def test_mod_with_nexus_release_source_validates(tmp_path: Path):
    game = _fixture("game", "example-game")
    mod = _fixture("mod", "example-accessibility-mod")
    mod["releaseSource"] = {
        "kind": "nexus",
        "repositoryUrl": "https://www.nexusmods.com/skyrimspecialedition/mods/181131",
    }
    _write(tmp_path, "game", game)
    _write(tmp_path, "mod", mod)

    report = validate_snapshot(tmp_path)

    assert report.ok
    assert report.diagnostics == ()


@pytest.mark.parametrize(
    ("mutation", "pointer"),
    [
        (lambda game, mod: game["stores"][0].update(storeKey="999"), "/stores/0"),
        (lambda game, mod: game["stores"][0].update(store="gog"), "/stores/0/store"),
        (
            lambda game, mod: game["accessibility"].update(wikiRatingUrl="https://accessiblegaming.wiki.attacker.test/page"),
            "/accessibility/wikiRatingUrl",
        ),
        (
            lambda game, mod: game["accessibility"].update(wikiRatingUrl="https://accessiblegaming.wiki/DiabloIV"),
            "/accessibility/wikiRatingUrl",
        ),
        (
            lambda game, mod: mod["releaseSource"].update(repositoryUrl="https://github.com/owner/repo/"),
            "/releaseSource/repositoryUrl",
        ),
        (
            lambda game, mod: mod["releaseSource"].update(repositoryUrl="HTTPS://GITHUB.COM/owner/repo"),
            "/releaseSource/repositoryUrl",
        ),
        (lambda game, mod: mod.update(homeUrl="https://localhost/project"), "/homeUrl"),
        (lambda game, mod: mod.update(homeUrl="https://10.0.0.1/project"), "/homeUrl"),
        (lambda game, mod: mod.update(homeUrl="https://mods.example.internal/project"), "/homeUrl"),
        (lambda game, mod: mod.update(homeUrl="https://example.com/project?"), "/homeUrl"),
        (lambda game, mod: mod.update(homeUrl="https://example.com/project#"), "/homeUrl"),
        (lambda game, mod: mod.update(homeUrl="https://example.com/bad path"), "/homeUrl"),
        (lambda game, mod: mod.update(homeUrl="https://example.com/%zz"), "/homeUrl"),
        (lambda game, mod: mod.update(homeUrl="https://example.com/\x00project"), "/homeUrl"),
        (lambda game, mod: mod.update(homeUrl="https://example.com/\x07project"), "/homeUrl"),
        (lambda game, mod: mod.update(homeUrl="https://127.1/project"), "/homeUrl"),
        (lambda game, mod: mod.update(homeUrl="https://0177.0.0.1/project"), "/homeUrl"),
        (
            lambda game, mod: mod.update(homeUrl="https://0x7f.0x0.0x0.0x1/project"),
            "/homeUrl",
        ),
        (
                lambda game, mod: mod["links"].append(
                    {
                        "kind": "funding",
                    "label": "Tracked",
                    "url": "https://example.com/file?utm_source=test",
                    "requiresPayment": False,
                }
            ),
            "/links/1/url",
        ),
        (
                lambda game, mod: mod["links"].append(
                    {
                        "kind": "funding",
                    "label": "Unknown query",
                    "url": "https://example.com/file?tab=description",
                    "requiresPayment": False,
                }
            ),
            "/links/1/url",
        ),
    ],
)
def test_validate_snapshot_rejects_field_specific_url_policy(tmp_path, mutation, pointer):
    game = _fixture("game", "example-game")
    mod = _fixture("mod", "example-accessibility-mod")
    mutation(game, mod)
    _write(tmp_path, "game", game)
    _write(tmp_path, "mod", mod)

    report = validate_snapshot(tmp_path)

    assert not report.ok
    assert any(diagnostic.pointer == pointer for diagnostic in report.diagnostics)


def test_validate_snapshot_accepts_narrow_url_allowlists(tmp_path):
    game = _fixture("game", "example-game")
    mod = _fixture("mod", "example-accessibility-mod")
    mod["links"].append(
        {
            "kind": "download",
            "label": "Nexus description",
            "url": "https://www.nexusmods.com/examplegame/mods/1?tab=description",
            "requiresPayment": False,
        }
    )
    _write(tmp_path, "game", game)
    _write(tmp_path, "mod", mod)

    report = validate_snapshot(tmp_path)

    assert report.ok, report.diagnostics


def test_validate_snapshot_rejects_raw_html_and_executable_text(tmp_path):
    game = _fixture("game", "example-game")
    mod = _fixture("mod", "example-accessibility-mod")
    game["accessibility"]["description"] = "Use <script>alert(1)</script> with a screen reader."
    mod["links"][0]["label"] = "${{ secrets.TOKEN }}"
    _write(tmp_path, "game", game)
    _write(tmp_path, "mod", mod)

    report = validate_snapshot(tmp_path)

    assert not report.ok
    assert {(item.path, item.pointer) for item in report.diagnostics} >= {
        ("registry/games/example-game.json", "/accessibility/description"),
        ("registry/mods/example-accessibility-mod.json", "/links/0/label"),
    }


@pytest.mark.parametrize(
    "description",
    [
        "<!-- hidden markup -->Readable text",
        "Run ${command} to continue.",
        "Run $(command) to continue.",
    ],
)
def test_validate_snapshot_rejects_other_markup_and_expression_forms(tmp_path, description):
    game = _fixture("game", "example-game")
    mod = _fixture("mod", "example-accessibility-mod")
    game["accessibility"]["description"] = description
    _write(tmp_path, "game", game)
    _write(tmp_path, "mod", mod)

    report = validate_snapshot(tmp_path)

    assert not report.ok
    assert any(
        diagnostic.path == "registry/games/example-game.json"
        and diagnostic.pointer == "/accessibility/description"
        for diagnostic in report.diagnostics
    )
