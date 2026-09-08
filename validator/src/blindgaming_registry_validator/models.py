from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Diagnostic:
    level: Literal["error", "warning"]
    path: str
    pointer: str
    message: str


@dataclass(frozen=True, slots=True)
class StoreEntry:
    store: str
    store_key: str
    url: str


@dataclass(frozen=True, slots=True)
class StoreIdentity:
    store: str
    store_key: str


@dataclass(frozen=True, slots=True)
class Resource:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class Accessibility:
    description: str | None = None
    coverage: str | None = None
    confidence: str | None = None
    access_via: str | None = None
    tags: tuple[str, ...] = ()
    wiki_rating_url: str | None = None
    primary_mod_id: str | None = None


@dataclass(frozen=True, slots=True)
class GameRecord:
    schema_version: int
    id: str
    status: str
    name: str
    canonical_store: str
    stores: tuple[StoreEntry, ...]
    reserved_stores: tuple[StoreIdentity, ...]
    accessibility: Accessibility
    resources: tuple[Resource, ...] = ()
    release_year: str | None = None
    archived_at: str | None = None
    archive_reason: str | None = None
    replaced_by: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseSource:
    kind: str
    repository_url: str


@dataclass(frozen=True, slots=True)
class ModLink:
    kind: str
    label: str
    url: str
    requires_payment: bool


@dataclass(frozen=True, slots=True)
class ModRecord:
    schema_version: int
    id: str
    status: str
    game_ids: tuple[str, ...]
    name: str
    home_url: str
    links: tuple[ModLink, ...]
    framework: str | None = None
    release_source: ReleaseSource | None = None
    archived_at: str | None = None
    archive_reason: str | None = None
    replaced_by: str | None = None


@dataclass(frozen=True, slots=True)
class GuideRecord:
    id: str
    title: str
    author: str | None = None
    last_modified: str | None = None
    body: str = ""
    draft: bool = False


@dataclass(frozen=True, slots=True)
class Snapshot:
    schema_version: int
    games: tuple[GameRecord, ...]
    mods: tuple[ModRecord, ...]
    files: tuple[tuple[str, bytes], ...]
    guides: tuple[GuideRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationReport:
    snapshot: Snapshot | None
    diagnostics: tuple[Diagnostic, ...]
    has_io_error: bool = False

    @property
    def ok(self) -> bool:
        return not any(diagnostic.level == "error" for diagnostic in self.diagnostics)
