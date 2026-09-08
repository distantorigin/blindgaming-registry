from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from .formatting import canonical_format_pointer, canonical_guide_bytes, canonical_record_bytes
from .loading import (
    RegistryParseError,
    DuplicateKeyError,
    UnexpectedPathError,
    _parse_guide_frontmatter,
    load_snapshot,
    reject_duplicate_pairs,
)
from .models import (
    Diagnostic,
    GameRecord,
    GuideRecord,
    ModRecord,
    Snapshot,
    StoreEntry,
    ValidationReport,
)
from .policy import (
    STORE_PLATFORM,
    classify_store_url,
    release_source_url_error,
    text_policy_error,
    url_policy_error,
    wiki_rating_url_error,
)
from .schemas import REGISTRY_SCHEMA_VERSION, validator_for


_TITLE_PAIR_LIMIT = 10_000
_TITLE_WARNING_LIMIT = 100


def _path(kind: str, identifier: str) -> str:
    return f"registry/{kind}s/{identifier}.json"


def _diagnostic(path: str, pointer: str, message: str) -> Diagnostic:
    return Diagnostic("error", path, pointer, message)


def _listing_diagnostics(
    path: str, pointer: str, listing: StoreEntry
) -> list[Diagnostic]:
    identity = classify_store_url(listing.url)
    if identity is None:
        return [
            _diagnostic(
                path,
                f"{pointer}/url",
                "URL is not a supported canonical store listing",
            )
        ]
    if identity[0] != listing.store:
        return [
            _diagnostic(
                path,
                f"{pointer}/store",
                f"declared store {listing.store!r} disagrees with URL store {identity[0]!r}",
            )
        ]
    return []


def _text_diagnostic(path: str, pointer: str, value: str) -> Diagnostic | None:
    error = text_policy_error(value)
    return _diagnostic(path, pointer, error) if error is not None else None


def _archive_date_diagnostic(path: str, value: str | None) -> Diagnostic | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.isoformat() != value:
        return _diagnostic(
            path,
            "/archivedAt",
            "archive date must be a real calendar date in YYYY-MM-DD form",
        )
    return None


def _validate_game(game: GameRecord) -> list[Diagnostic]:
    path = _path("game", game.id)
    diagnostics: list[Diagnostic] = []
    if not game.canonical_store:
        diagnostics.append(
            _diagnostic(
                path,
                "/stores",
                "exactly one store entry must be marked primary",
            )
        )
    seen_stores: set[str] = set()
    for index, entry in enumerate(game.stores):
        if entry.store in seen_stores:
            diagnostics.append(
                _diagnostic(
                    path,
                    f"/stores/{index}/store",
                    f"duplicate store {entry.store!r}",
                )
            )
        else:
            seen_stores.add(entry.store)
        if game.status == "active" and game.accessibility.access_via == "mod":
            platform = STORE_PLATFORM.get(entry.store)
            if platform is not None and platform != "pc":
                diagnostics.append(
                    _diagnostic(
                        path,
                        f"/stores/{index}/store",
                        f"a mod-access game cannot list a {platform} store; "
                        "mods do not apply there",
                    )
                )
        diagnostics.extend(
            _listing_diagnostics(path, f"/stores/{index}", entry)
        )
    text_values = [
        ("/name", game.name),
        *[
            (f"/resources/{index}/name", resource.name)
            for index, resource in enumerate(game.resources)
        ],
    ]
    if game.accessibility.description is not None:
        text_values.append(
            ("/accessibility/description", game.accessibility.description)
        )
    if game.archive_reason is not None:
        text_values.append(("/archiveReason", game.archive_reason))
    for pointer, value in text_values:
        diagnostic = _text_diagnostic(path, pointer, value)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
    if game.accessibility.wiki_rating_url is not None:
        error = wiki_rating_url_error(game.accessibility.wiki_rating_url)
        if error is not None:
            diagnostics.append(_diagnostic(path, "/accessibility/wikiRatingUrl", error))
    for index, resource in enumerate(game.resources):
        error = url_policy_error(resource.url)
        if error is not None:
            diagnostics.append(
                _diagnostic(path, f"/resources/{index}/url", error)
            )
    archive_date_diagnostic = _archive_date_diagnostic(path, game.archived_at)
    if archive_date_diagnostic is not None:
        diagnostics.append(archive_date_diagnostic)
    if game.release_year is not None:
        maximum_year = date.today().year + 1
        year = (
            int(game.release_year)
            if game.release_year.isascii() and game.release_year.isdigit()
            else 0
        )
        if year < 1950 or year > maximum_year:
            diagnostics.append(
                _diagnostic(
                    path,
                    "/releaseYear",
                    f"release year must be between 1950 and {maximum_year}",
                )
            )
    return diagnostics


def _validate_mod(mod: ModRecord) -> list[Diagnostic]:
    path = _path("mod", mod.id)
    diagnostics: list[Diagnostic] = []
    text_values = [("/name", mod.name)]
    if mod.framework is not None:
        text_values.append(("/framework", mod.framework))
    if mod.archive_reason is not None:
        text_values.append(("/archiveReason", mod.archive_reason))
    text_values.extend(
        (f"/links/{index}/label", link.label)
        for index, link in enumerate(mod.links)
    )
    for pointer, value in text_values:
        diagnostic = _text_diagnostic(path, pointer, value)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
    error = url_policy_error(mod.home_url, allow_nexus_description=True)
    if error is not None:
        diagnostics.append(_diagnostic(path, "/homeUrl", error))
    if mod.release_source is not None:
        error = release_source_url_error(
            mod.release_source.kind, mod.release_source.repository_url
        )
        if error is not None:
            diagnostics.append(_diagnostic(path, "/releaseSource/repositoryUrl", error))
    for index, link in enumerate(mod.links):
        error = url_policy_error(link.url, allow_nexus_description=True)
        if error is not None:
            diagnostics.append(_diagnostic(path, f"/links/{index}/url", error))
    archive_date_diagnostic = _archive_date_diagnostic(path, mod.archived_at)
    if archive_date_diagnostic is not None:
        diagnostics.append(archive_date_diagnostic)
    seen_links: set[tuple[str, str]] = set()
    for link in mod.links:
        identity = (link.kind, link.url)
        if identity in seen_links:
            diagnostics.append(
                _diagnostic(
                    path,
                    "/links",
                    f"duplicate mod link pair ({link.kind}, {link.url})",
                )
            )
            break
        seen_links.add(identity)
    return diagnostics


_GUIDE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_GUIDE_ID_MAX_LENGTH = 80
_GUIDE_TITLE_MAX_LENGTH = 200
_GUIDE_AUTHOR_MAX_LENGTH = 200
_GUIDE_BODY_MAX_LENGTH = 200_000


def _guide_date_diagnostic(path: str, value: str) -> Diagnostic | None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.isoformat() != value:
        return _diagnostic(
            path,
            "/lastModified",
            "last modified date must be a real calendar date in YYYY-MM-DD form",
        )
    return None


def _validate_guide(guide: GuideRecord) -> list[Diagnostic]:
    path = f"registry/guides/{guide.id}.md"
    diagnostics: list[Diagnostic] = []
    if len(guide.id) > _GUIDE_ID_MAX_LENGTH or _GUIDE_ID.fullmatch(guide.id) is None:
        diagnostics.append(
            _diagnostic(path, "", "guide filename must be a valid registry id")
        )
    if len(guide.title) > _GUIDE_TITLE_MAX_LENGTH:
        diagnostics.append(
            _diagnostic(
                path,
                "/title",
                f"title must not exceed {_GUIDE_TITLE_MAX_LENGTH} characters",
            )
        )
    title_error = text_policy_error(guide.title)
    if title_error is not None:
        diagnostics.append(_diagnostic(path, "/title", title_error))
    if guide.author is not None:
        if len(guide.author) > _GUIDE_AUTHOR_MAX_LENGTH:
            diagnostics.append(
                _diagnostic(
                    path,
                    "/author",
                    f"author must not exceed {_GUIDE_AUTHOR_MAX_LENGTH} characters",
                )
            )
        author_error = text_policy_error(guide.author)
        if author_error is not None:
            diagnostics.append(_diagnostic(path, "/author", author_error))
    if guide.last_modified is not None:
        date_diagnostic = _guide_date_diagnostic(path, guide.last_modified)
        if date_diagnostic is not None:
            diagnostics.append(date_diagnostic)
    if len(guide.body) > _GUIDE_BODY_MAX_LENGTH:
        diagnostics.append(
            _diagnostic(
                path,
                "",
                f"guide body must not exceed {_GUIDE_BODY_MAX_LENGTH} characters",
            )
        )
    body_error = text_policy_error(guide.body)
    if body_error is not None:
        diagnostics.append(_diagnostic(path, "", body_error))
    return diagnostics


def _schema_diagnostics(snapshot: Snapshot) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for path, contents in snapshot.files:
        if path.startswith("registry/guides/"):
            continue
        kind = "game" if path.startswith("registry/games/") else "mod"
        record = json.loads(contents)
        for error in validator_for(kind, snapshot.schema_version).iter_errors(record):
            pointer = "".join(
                f"/{str(part).replace('~', '~0').replace('/', '~1')}"
                for part in error.absolute_path
            )
            diagnostics.append(_diagnostic(path, pointer, error.message))
    return diagnostics


def _replacement_diagnostics(
    kind: str,
    records: tuple[GameRecord, ...] | tuple[ModRecord, ...],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    by_id = {record.id: record for record in records}
    graph: dict[str, str] = {}
    for record in records:
        if record.replaced_by is None:
            continue
        path = _path(kind, record.id)
        replacement = by_id.get(record.replaced_by)
        if record.replaced_by == record.id:
            diagnostics.append(
                _diagnostic(path, "/replacedBy", "record cannot replace itself")
            )
            continue
        if replacement is None:
            diagnostics.append(
                _diagnostic(
                    path,
                    "/replacedBy",
                    f"replacement {record.replaced_by!r} must reference an existing {kind}",
                )
            )
            continue
        graph[record.id] = replacement.id
        if replacement.status != "active":
            diagnostics.append(
                _diagnostic(
                    path,
                    "/replacedBy",
                    f"replacement {record.replaced_by!r} must be active",
                )
            )

    reported_cycles: set[frozenset[str]] = set()
    for origin in sorted(graph):
        positions: dict[str, int] = {}
        chain: list[str] = []
        current = origin
        while current in graph and current not in positions:
            positions[current] = len(chain)
            chain.append(current)
            current = graph[current]
        if current not in positions:
            continue
        cycle = chain[positions[current] :]
        cycle_key = frozenset(cycle)
        if cycle_key in reported_cycles:
            continue
        reported_cycles.add(cycle_key)
        display = " -> ".join((*cycle, cycle[0]))
        diagnostics.append(
            _diagnostic(
                _path(kind, cycle[0]),
                "/replacedBy",
                f"replacement cycle detected: {display}",
            )
        )
    return diagnostics


def _normalised_title(value: str) -> str:
    folded = unicodedata.normalize("NFC", value).casefold()
    without_punctuation = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in folded
    )
    return " ".join(without_punctuation.split())


def _store_identities(game: GameRecord) -> str:
    listings = game.stores
    return ", ".join(
        sorted(f"{listing.store}:{listing.store_key}" for listing in listings)
    )


def _title_similarity_diagnostics(
    games: tuple[GameRecord, ...],
    *,
    base_snapshot: Snapshot | None,
    bounded: bool,
) -> list[Diagnostic]:
    if len(games) < 2:
        return []

    if base_snapshot is None:
        candidate_origins = range(len(games) - 1)
        compare_from_origin = True
    else:
        base_games = {game.id: game for game in base_snapshot.games}
        candidate_origins = tuple(
            index
            for index, game in enumerate(games)
            if game.id not in base_games or base_games[game.id].name != game.name
        )
        compare_from_origin = False
        if not candidate_origins:
            return []

    warnings: list[Diagnostic] = []
    compared_pairs: set[tuple[int, int]] = set()
    normalised_titles = tuple(_normalised_title(game.name) for game in games)
    truncated = False
    truncation_origin = games[candidate_origins[0]].id
    for origin in candidate_origins:
        candidates = (
            range(origin + 1, len(games))
            if compare_from_origin
            else range(len(games))
        )
        for other in candidates:
            if origin == other:
                continue
            pair = (min(origin, other), max(origin, other))
            if pair in compared_pairs:
                continue
            if bounded and len(compared_pairs) >= _TITLE_PAIR_LIMIT:
                truncated = True
                break
            compared_pairs.add(pair)
            first, second = (games[index] for index in pair)
            first_title, second_title = (normalised_titles[index] for index in pair)
            if (
                first_title != second_title
                and SequenceMatcher(None, first_title, second_title).ratio() < 0.92
            ):
                continue
            if bounded and len(warnings) >= _TITLE_WARNING_LIMIT:
                truncated = True
                break
            warnings.append(
                Diagnostic(
                    "warning",
                    _path("game", second.id),
                    "/name",
                    f"title similarity warning: game {first.id} "
                    f"({_store_identities(first)}) resembles game {second.id} "
                    f"({_store_identities(second)})",
                )
            )
        if truncated:
            break

    if truncated:
        warnings.append(
            Diagnostic(
                "warning",
                _path("game", truncation_origin),
                "/name",
                f"title similarity scan stopped after {len(compared_pairs)} pairs; "
                "remaining pairs were not compared",
            )
        )
    return warnings


def _mass_change_status(
    snapshot: Snapshot,
    base_snapshot: Snapshot | None,
    *,
    allow_mass_change: bool,
) -> tuple[Diagnostic | None, bool]:
    if base_snapshot is None:
        return None, False
    # Guides have no permanent identity, so this gate only counts game and mod records.
    current_files = {
        path: contents
        for path, contents in snapshot.files
        if not path.startswith("registry/guides/")
    }
    base_files = {
        path: contents
        for path, contents in base_snapshot.files
        if not path.startswith("registry/guides/")
    }
    changed_paths = sorted(
        path
        for path, contents in current_files.items()
        if path not in base_files or base_files[path] != contents
    )
    limit = max(100, math.ceil(len(base_files) * 0.10))
    if len(changed_paths) <= limit:
        return None, False
    diagnostic: Diagnostic | None = None
    if not allow_mass_change:
        diagnostic = _diagnostic(
            changed_paths[0],
            "",
            f"mass change modifies {len(changed_paths)} records; limit is "
            f"{limit} without explicit authorization",
        )
    return diagnostic, True


def _owned_store_identities(game: GameRecord) -> set[tuple[str, str]]:
    return {
        (listing.store, listing.store_key)
        for listing in game.stores
    }


def _reserved_stores(game: GameRecord) -> set[tuple[str, str]]:
    return {
        (identity.store, identity.store_key)
        for identity in game.reserved_stores
    }


def _reservation_history_diagnostics(
    snapshot: Snapshot,
    base_snapshot: Snapshot | None,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if base_snapshot is None:
        for game in snapshot.games:
            owned = _owned_store_identities(game)
            reserved = _reserved_stores(game)
            path = _path("game", game.id)
            if game.status == "active":
                diagnostics.extend(
                    _diagnostic(
                        path,
                        f"/reservedStores/{reservation.store}",
                        "active bootstrap records cannot introduce store reservations",
                    )
                    for reservation in game.reserved_stores
                )
                continue
            for identity in sorted(owned - reserved):
                diagnostics.append(
                    _diagnostic(
                        path,
                        "/reservedStores",
                        "archived game must reserve current store identity "
                        f"{identity[0]}:{identity[1]}",
                    )
                )
            for identity in game.reserved_stores:
                exact = (identity.store, identity.store_key)
                if exact not in owned:
                    diagnostics.append(
                        _diagnostic(
                            path,
                            f"/reservedStores/{identity.store}",
                            f"bootstrap archive does not own store identity "
                            f"{identity.store}:{identity.store_key}",
                        )
                    )
        return diagnostics

    base_games = {game.id: game for game in base_snapshot.games}
    for game in snapshot.games:
        previous = base_games.get(game.id)
        path = _path("game", game.id)
        if previous is None:
            diagnostics.extend(
                _diagnostic(
                    path,
                    f"/reservedStores/{identity.store}",
                    f"new store reservation {identity.store}:{identity.store_key} "
                    "may only be introduced by an archive transition",
                )
                for identity in game.reserved_stores
            )
            continue

        previous_reserved = _reserved_stores(previous)
        current_reserved = _reserved_stores(game)
        for identity in sorted(previous_reserved - current_reserved):
            diagnostics.append(
                _diagnostic(
                    path,
                    "/reservedStores",
                    f"reserved store identity {identity[0]}:{identity[1]} is immutable",
                )
            )

        archive_transition = previous.status != "archived" and game.status == "archived"
        boundary_owned = _owned_store_identities(previous) | _owned_store_identities(game)
        for identity in game.reserved_stores:
            exact = (identity.store, identity.store_key)
            if exact in previous_reserved:
                continue
            if not archive_transition:
                diagnostics.append(
                    _diagnostic(
                        path,
                        f"/reservedStores/{identity.store}",
                        f"new store reservation {identity.store}:{identity.store_key} "
                        "may only be introduced by an archive transition",
                    )
                )
            elif exact not in boundary_owned:
                diagnostics.append(
                    _diagnostic(
                        path,
                        f"/reservedStores/{identity.store}",
                        f"store reservation {identity.store}:{identity.store_key} "
                        "was not owned at the archive transition",
                    )
                )

        if archive_transition:
            for identity in sorted(boundary_owned - current_reserved):
                diagnostics.append(
                    _diagnostic(
                        path,
                        "/reservedStores",
                        f"archive transition must reserve store identity "
                        f"{identity[0]}:{identity[1]}",
                    )
                )
        elif game.status == "archived":
            for identity in sorted(_owned_store_identities(game) - current_reserved):
                diagnostics.append(
                    _diagnostic(
                        path,
                        "/reservedStores",
                        f"archived game must reserve current store identity "
                        f"{identity[0]}:{identity[1]}",
                    )
                )
    return diagnostics


def _semantic_diagnostics(
    snapshot: Snapshot,
    *,
    base_snapshot: Snapshot | None,
    allow_mass_change: bool,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    games_by_id = {game.id: game for game in snapshot.games}
    mods_by_id = {mod.id: mod for mod in snapshot.mods}

    reservation_owners: dict[tuple[str, str], tuple[str, str]] = {}
    for game in snapshot.games:
        for reservation in game.reserved_stores:
            identity = (reservation.store, reservation.store_key)
            previous = reservation_owners.get(identity)
            if previous is not None and previous[0] != game.id:
                diagnostics.append(
                    _diagnostic(
                        _path("game", game.id),
                        f"/reservedStores/{reservation.store}",
                        f"store identity {reservation.store}:{reservation.store_key} "
                        f"is already reserved by game {previous[0]}",
                    )
                )
            else:
                reservation_owners[identity] = (
                    game.id,
                    f"/reservedStores/{reservation.store}",
                )

    store_owners: dict[tuple[str, str], tuple[str, str]] = {}
    for game in snapshot.games:
        listings = tuple(
            (f"/stores/{index}", listing)
            for index, listing in enumerate(game.stores)
        )
        for pointer, listing in listings:
            identity = (listing.store, listing.store_key)
            reservation_owner = reservation_owners.get(identity)
            if reservation_owner is not None and reservation_owner[0] != game.id:
                diagnostics.append(
                    _diagnostic(
                        _path("game", game.id),
                        pointer,
                        f"store identity {listing.store}:{listing.store_key} is "
                        f"reserved by archived game {reservation_owner[0]}",
                    )
                )
            previous = store_owners.get(identity)
            if previous is not None:
                diagnostics.append(
                    _diagnostic(
                        _path("game", game.id),
                        pointer,
                        f"store identity {listing.store}:{listing.store_key} is "
                        f"already owned by game {previous[0]}",
                    )
                )
            else:
                store_owners[identity] = (game.id, pointer)

    for mod in snapshot.mods:
        for index, game_id in enumerate(mod.game_ids):
            if game_id not in games_by_id:
                diagnostics.append(
                    _diagnostic(
                        _path("mod", mod.id),
                        f"/gameIds/{index}",
                        f"referenced game {game_id!r} does not exist",
                    )
                )

    for game in snapshot.games:
        primary_id = game.accessibility.primary_mod_id
        if primary_id is None:
            continue
        primary = mods_by_id.get(primary_id)
        path = _path("game", game.id)
        if primary is None:
            diagnostics.append(
                _diagnostic(
                    path,
                    "/accessibility/primaryModId",
                    f"primary mod {primary_id!r} does not exist",
                )
            )
        elif game.id not in primary.game_ids:
            diagnostics.append(
                _diagnostic(
                    path,
                    "/accessibility/primaryModId",
                    f"primary mod {primary_id!r} does not cover game {game.id!r} "
                    f"(covers {', '.join(primary.game_ids)})",
                )
            )
        elif primary.status != "active":
            diagnostics.append(
                _diagnostic(
                    path,
                    "/accessibility/primaryModId",
                    f"primary mod {primary_id!r} must be active",
                )
            )

    diagnostics.extend(_replacement_diagnostics("game", snapshot.games))
    diagnostics.extend(_replacement_diagnostics("mod", snapshot.mods))
    diagnostics.extend(_reservation_history_diagnostics(snapshot, base_snapshot))

    mass_change_diagnostic, is_mass_change = _mass_change_status(
        snapshot,
        base_snapshot,
        allow_mass_change=allow_mass_change,
    )
    if mass_change_diagnostic is not None:
        diagnostics.append(mass_change_diagnostic)
    else:
        diagnostics.extend(
            _title_similarity_diagnostics(
                snapshot.games,
                base_snapshot=base_snapshot,
                bounded=base_snapshot is None or is_mass_change,
            )
        )

    if base_snapshot is None:
        for record in (*snapshot.games, *snapshot.mods):
            if record.status != "active":
                kind = "game" if isinstance(record, GameRecord) else "mod"
                diagnostics.append(
                    _diagnostic(
                        _path(kind, record.id),
                        "/status",
                        "bootstrap records must be active",
                    )
                )
        return diagnostics

    current_files = dict(snapshot.files)
    base_files = dict(base_snapshot.files)
    for path in sorted(base_files.keys() - current_files.keys()):
        if path.startswith("registry/guides/"):
            continue
        diagnostics.append(
            _diagnostic(path, "", "published registry record files must not be deleted")
        )

    base_mods = {mod.id: mod for mod in base_snapshot.mods}
    for mod in snapshot.mods:
        previous = base_mods.get(mod.id)
        if previous is not None:
            dropped = sorted(set(previous.game_ids) - set(mod.game_ids))
            if dropped:
                diagnostics.append(
                    _diagnostic(
                        _path("mod", mod.id),
                        "/gameIds",
                        "published mod game bindings are monotonic; "
                        f"{', '.join(repr(g) for g in dropped)} may not be removed",
                    )
                )

    base_ids = {
        *(('game', game.id) for game in base_snapshot.games),
        *(('mod', mod.id) for mod in base_snapshot.mods),
    }
    for kind, records in (("game", snapshot.games), ("mod", snapshot.mods)):
        for record in records:
            if (kind, record.id) not in base_ids and record.status != "active":
                diagnostics.append(
                    _diagnostic(
                        _path(kind, record.id),
                        "/status",
                        "new records must be active",
                    )
                )

    return diagnostics


def _load_error_report(error: BaseException) -> ValidationReport:
    return ValidationReport(
        snapshot=None,
        diagnostics=(
            Diagnostic(
                "error",
                getattr(error, "path", "."),
                getattr(error, "pointer", ""),
                getattr(error, "message", str(error)),
            ),
        ),
        has_io_error=isinstance(error, OSError),
    )


def _record_failure_report(root: Path) -> ValidationReport | None:
    diagnostics: list[Diagnostic] = []
    has_io_error = False
    for kind in ("game", "mod"):
        directory = Path(root) / "registry" / f"{kind}s"
        for path in sorted(directory.glob("*.json")):
            relative = path.relative_to(root).as_posix()
            try:
                contents = path.read_bytes()
            except OSError as error:
                has_io_error = True
                diagnostics.append(_diagnostic(relative, "", str(error)))
                continue
            try:
                text = contents.decode("utf-8")
            except UnicodeDecodeError:
                diagnostics.append(_diagnostic(relative, "", "invalid UTF-8"))
                continue
            try:
                record = json.loads(text, object_pairs_hook=reject_duplicate_pairs)
            except json.JSONDecodeError:
                diagnostics.append(_diagnostic(relative, "", "invalid JSON"))
                continue
            duplicate_pointers = _duplicate_pointers(record)
            if duplicate_pointers:
                diagnostics.extend(
                    _diagnostic(relative, pointer, "duplicate JSON object key")
                    for pointer in duplicate_pointers
                )
                continue
            surrogate_pointers = _surrogate_pointers(record)
            if surrogate_pointers:
                diagnostics.extend(
                    _diagnostic(
                        relative,
                        pointer,
                        "text must contain only Unicode scalar values",
                    )
                    for pointer in surrogate_pointers
                )
                continue
            errors = list(validator_for(kind, REGISTRY_SCHEMA_VERSION).iter_errors(record))
            if not isinstance(record, Mapping):
                errors = [error for error in errors if error.validator == "type"][:1]
            for error in errors:
                pointer = "".join(
                    f"/{str(part).replace('~', '~0').replace('/', '~1')}"
                    for part in error.absolute_path
                )
                diagnostics.append(_diagnostic(relative, pointer, error.message))
            if not isinstance(record, Mapping):
                continue
            if contents != canonical_record_bytes(record, kind):
                diagnostics.append(
                    _diagnostic(
                        relative,
                        canonical_format_pointer(record, kind),
                        "record is not canonically formatted",
                    )
                )
            if record.get("id") != path.stem:
                diagnostics.append(
                    _diagnostic(
                        relative,
                        "/id",
                        "filename stem must match record id",
                    )
                )
    guide_directory = Path(root) / "registry" / "guides"
    for path in sorted(guide_directory.glob("*.md")):
        relative = path.relative_to(root).as_posix()
        try:
            contents = path.read_bytes()
        except OSError as error:
            has_io_error = True
            diagnostics.append(_diagnostic(relative, "", str(error)))
            continue
        try:
            text = contents.decode("utf-8")
        except UnicodeDecodeError:
            diagnostics.append(_diagnostic(relative, "", "invalid UTF-8"))
            continue
        try:
            frontmatter, body = _parse_guide_frontmatter(relative, text)
        except (RegistryParseError, DuplicateKeyError) as error:
            diagnostics.append(_diagnostic(relative, error.pointer, error.message))
            continue
        if contents != canonical_guide_bytes(frontmatter, body):
            diagnostics.append(
                _diagnostic(relative, "", "record is not canonically formatted")
            )
    if not diagnostics:
        return None
    return ValidationReport(
        snapshot=None,
        diagnostics=tuple(
            sorted(
                diagnostics,
                key=lambda item: (item.path, item.pointer, item.level, item.message),
            )
        ),
        has_io_error=has_io_error,
    )


def _duplicate_pointers(value: object, pointer: str = "") -> list[str]:
    pointers: list[str] = []
    duplicate_key = getattr(value, "duplicate_key", None)
    if isinstance(duplicate_key, str):
        escaped = duplicate_key.replace("~", "~0").replace("/", "~1")
        pointers.append(f"{pointer}/{escaped}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            pointers.extend(_duplicate_pointers(item, f"{pointer}/{escaped}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            pointers.extend(_duplicate_pointers(item, f"{pointer}/{index}"))
    return pointers


def _surrogate_pointers(value: object, pointer: str = "") -> list[str]:
    pointers: list[str] = []
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            pointers.append(pointer)
        return pointers
    if isinstance(value, Mapping):
        for key, item in value.items():
            if any(0xD800 <= ord(character) <= 0xDFFF for character in key):
                pointers.append(pointer)
                continue
            escaped = key.replace("~", "~0").replace("/", "~1")
            pointers.extend(_surrogate_pointers(item, f"{pointer}/{escaped}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            pointers.extend(_surrogate_pointers(item, f"{pointer}/{index}"))
    return pointers


def validate_snapshot(
    root: Path,
    *,
    base_root: Path | None = None,
    allow_mass_change: bool = False,
) -> ValidationReport:
    """Load and validate one snapshot without network or repository operations."""
    try:
        snapshot = load_snapshot(root)
    except UnexpectedPathError as error:
        if error.pointer == "/id":
            failure_report = _record_failure_report(Path(root))
            if failure_report is not None:
                return failure_report
        return _load_error_report(error)
    except (KeyError, TypeError) as error:
        failure_report = _record_failure_report(Path(root))
        return failure_report or _load_error_report(error)
    except ValueError as error:
        failure_report = _record_failure_report(Path(root))
        return failure_report or _load_error_report(error)
    except OSError as error:
        return _load_error_report(error)
    base_snapshot = None
    if base_root is not None:
        try:
            base_snapshot = load_snapshot(base_root)
        except UnexpectedPathError as error:
            if error.pointer == "/id":
                failure_report = _record_failure_report(Path(base_root))
                if failure_report is not None:
                    return failure_report
            return _load_error_report(error)
        except (KeyError, TypeError) as error:
            failure_report = _record_failure_report(Path(base_root))
            return failure_report or _load_error_report(error)
        except ValueError as error:
            failure_report = _record_failure_report(Path(base_root))
            return failure_report or _load_error_report(error)
        except OSError as error:
            return _load_error_report(error)
    diagnostics = _schema_diagnostics(snapshot)
    for game in snapshot.games:
        diagnostics.extend(_validate_game(game))
    for mod in snapshot.mods:
        diagnostics.extend(_validate_mod(mod))
    for guide in snapshot.guides:
        diagnostics.extend(_validate_guide(guide))
    diagnostics.extend(
        _semantic_diagnostics(
            snapshot,
            base_snapshot=base_snapshot,
            allow_mass_change=allow_mass_change,
        )
    )
    return ValidationReport(
        snapshot=snapshot,
        diagnostics=tuple(
            sorted(
                diagnostics,
                key=lambda item: (item.path, item.pointer, item.level, item.message),
            )
        ),
    )
