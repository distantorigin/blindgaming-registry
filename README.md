# blindgaming-registry

[![Registry validation](https://github.com/distantorigin/blindgaming-registry/actions/workflows/validate.yml/badge.svg)](https://github.com/distantorigin/blindgaming-registry/actions/workflows/validate.yml)
![Games](https://img.shields.io/github/directory-file-count/distantorigin/blindgaming-registry/registry/games?type=file&extension=json&label=games&color=2f6f6f)
![Mods](https://img.shields.io/github/directory-file-count/distantorigin/blindgaming-registry/registry/mods?type=file&extension=json&label=mods&color=6f4f8f)
![Schema](https://img.shields.io/badge/schema-v1-555555)

The public data registry for [Blind Gaming Database](https://blindgaming.net).
It contains game and accessibility mod records, guides, JSON schemas, and a validator.

A game is represented by one file in [registry/games](registry/games). An accessibility mod is
one file in [registry/mods](registry/mods). The website imports updates from this repository.

Contributions are welcome. Include links to accessibility documentation or player
reports when available.

## Contributing

For a new playable game or accessibility mod, start with the
[submission tool](https://blindgaming.net/submit). It helps you find store pages
and create the JSON records.

To edit locally, install Python 3.12 or later and uv, then clone the repository:

```sh
git clone https://github.com/distantorigin/blindgaming-registry.git
cd blindgaming-registry
uv sync --locked --extra test
```

Save game records in `registry/games`, mod records in `registry/mods`, and
Markdown guides in `registry/guides`. You can create or edit these files by hand.

Include the records needed to explain how the game is accessible:

- If the game is accessible on its own, submit the game record.
- If the game is accessible because of a mod, submit the game and mod together.
- If the game already exists, the mod record can point at that game ID.
- If the mod already exists, the game record can point at that mod ID. For
  example, some Ren'Py games share one Ren'Py accessibility mod.

A game that requires a mod sets `accessibility.accessVia` to `"mod"` and
`primaryModId` to the mod ID. The mod lists each supported game in `gameIds`.
Both records must reference each other before the pull request is merged.

Before opening a pull request, format and validate the checkout:

```sh
uv run blindgaming-registry format
uv run blindgaming-registry validate
```

For record-only changes, those two commands are usually enough. If you change
schemas, validator code, workflow files, or documentation examples, run the
validator tests too:

```sh
uv run python -m pytest validator/tests -q
```

Keep pull requests focused. A PR that adds a mod-based accessibility path should
include the game record, the mod record, and the cross-links between them.

If editing records is impractical, open an issue with the game or mod name and
the change you want. A maintainer can turn it into a record.

## Record rules

The [JSON schemas](schemas/v1) define the complete record format:

- `schemaVersion` is the registry format version. Current value: 1.
- `id` matches the filename without .json.
- IDs and filenames are permanent after publication.
- Unknown fields are rejected.

```text
registry/games/example-game.json -> "id": "example-game"
registry/mods/example-accessibility-mod.json -> "id": "example-accessibility-mod"
```

Do not delete a published record to fix or remove it. Archive it instead.

## Record format

### Game records

Game files live in `registry/games/<id>.json`.

Required fields:

- `schemaVersion`: always 1
- `id`: lowercase record ID, matching the filename
- `status`: active or archived
- `name`: the game title
- `stores`: store pages for the game
- `accessibility`: what we know about blind accessibility

Optional fields you will see often:

- `resources`: links about the game or its accessibility
- `releaseYear`: four-digit year

### Store links

Use `stores` for places someone can get the game. Each entry has:

- `store`: steam, gog, humble, battlenet, psn, xbox, switch, appstore, or googleplay
- `url`: the store page
- `primary`: only on the main listing, always true

Use exactly one primary store. If the game has a Steam page, use Steam as the
primary store unless another listing is the better canonical source. For
GOG-only, Humble-only, or console-only games, use the best available listing.
The primary store controls display order and price checking priority.

Battle.net uses `battlenet`, with a product page such as
`https://shop.battle.net/en-us/product/hearthstone`.

For other storefronts that are not supported as store entries, include a named
purchase link in `resources`. For example, Apotheorasis includes its itch.io
purchase page there.

```json
"stores": [
  {
    "store": "steam",
    "primary": true,
    "url": "https://store.steampowered.com/app/1234560/Example_Game/"
  },
  {
    "store": "gog",
    "url": "https://www.gog.com/game/example_game"
  }
]
```

Do not add store identity fields to records. The validator derives them from the URL:

- `https://store.steampowered.com/app/1234560/Example_Game/` becomes
  `steam:1234560`
- `https://store.playstation.com/en-us/concept/10006418` becomes
  `psn:concept/10006418`
- `https://apps.apple.com/us/app/example-game/id1234567890` becomes
  `appstore:id1234567890`
- `https://play.google.com/store/apps/details?id=com.example.game` becomes
  `googleplay:com.example.game`

Those derived identities prevent duplicate records from claiming the same store
page.

### Platforms

Each store belongs to one browsing category: steam, gog, humble, and battlenet are
`pc`; psn, xbox, and switch are `console`; appstore and googleplay are
`mobile`. The category decides which site page shows the listing. It does
not describe operating systems. Steam covers Windows, Mac, and Linux and is
still `pc`. An App Store listing means the iOS build; Mac App Store pages
are rejected. App Store URLs must include the two-letter country segment,
for example `https://apps.apple.com/us/app/example-game/id1234567890`; the
country-less form is rejected.

Google Play listings are the only store URLs that carry a query string, and
it must be exactly `?id=<package>`.

An active game whose accessibility comes from a mod (`accessVia: mod`) may
list only `pc` stores. The registry currently supports mod-based accessibility only
on PC; console and mobile store links would imply support that these records
do not establish.

### Accessibility

The `accessibility` object may be empty when details are unknown. When available,
describe the features that make the game playable and any known limitations.

Common accessibility fields:

- `description`: one to a few plain sentences about what works and what does
  not
- `coverage`: full, mostly, partial, or none
- `confidence`: verified if someone checked it, reported if it has not
  been directly verified
- `accessVia`: native or mod
- `primaryModId`: required when accessVia is mod
- `tags`: `blind` or `audio-description`; these tags may be deprecated in a
  future update
- `wikiRatingUrl`: the Accessible Gaming Wiki rating page, if there is one

The website also uses `iap:appstore` and `iap:googleplay` tags to indicate
in-app purchases. It maintains these automatically from store pages during
price updates; do not include them in registry records.

Keep ratings and articles separate. `wikiRatingUrl` is for the rating page the
site uses. A normal Accessible Gaming Wiki article link goes in `resources`.

### Resources

Resources are named links to supporting information:

```json
"resources": [
  {
    "name": "Accessibility notes",
    "url": "https://example.com/example-game-accessibility"
  }
]
```

Use the name to say where the link goes. There is no `kind` field.

### Example game

<!-- registry-example: game -->
```json
{
  "schemaVersion": 1,
  "id": "example-game",
  "status": "active",
  "name": "Example Game",
  "stores": [
    {
      "store": "steam",
      "primary": true,
      "url": "https://store.steampowered.com/app/1234560/Example_Game/"
    }
  ],
  "accessibility": {
    "description": "Accessible with the Example Accessibility Mod.",
    "coverage": "full",
    "confidence": "verified",
    "accessVia": "mod",
    "tags": [
      "blind"
    ],
    "wikiRatingUrl": "https://accessiblegaming.wiki/AccessibilityRating/ExampleGame/Blind",
    "primaryModId": "example-accessibility-mod"
  },
  "resources": [
    {
      "name": "Accessibility notes",
      "url": "https://example.com/example-game-accessibility"
    }
  ],
  "releaseYear": "2026"
}
```

### Mod records

Mod files live in `registry/mods/<id>.json`.

Required fields:

- `schemaVersion`: always 1
- `id`: lowercase record ID, matching the filename
- `status`: active or archived
- `gameIds`: games this mod makes accessible
- `name`: mod name
- `homeUrl`: project page
- `links`: download, discussion, or funding links

Common optional fields:

- `framework`: short implementation note, such as Python mod
- `releaseSource`: a GitHub repository (kind github) or a Nexus Mods mod page
  (kind nexus); BlindGaming.net scans it for updates and new releases

Every mod link has requiresPayment. Use true only when the linked download
requires payment before someone can get the mod. Use false for free downloads,
discussions, source repositories, and funding pages. Only download links can
set it to true.

### Example mod

<!-- registry-example: mod -->
```json
{
  "schemaVersion": 1,
  "id": "example-accessibility-mod",
  "status": "active",
  "gameIds": [
    "example-game"
  ],
  "name": "Example Accessibility Mod",
  "framework": "Screen reader mod",
  "homeUrl": "https://github.com/blindgaming-example/example-accessibility-mod",
  "releaseSource": {
    "kind": "github",
    "repositoryUrl": "https://github.com/blindgaming-example/example-accessibility-mod"
  },
  "links": [
    {
      "kind": "download",
      "label": "Latest release",
      "url": "https://github.com/blindgaming-example/example-accessibility-mod/releases",
      "requiresPayment": false
    }
  ]
}
```

### Archiving

Do not delete published records. To retire a record:

1. Keep the file.
2. Set `status` to `archived`.
3. Add `archivedAt`.
4. Add `archiveReason`.
5. Add `replacedBy` only if there is an active replacement record of the same
   kind.

Archived games also need `reservedStores`. This preserves store identities that
belonged to the archived game, so a later record cannot accidentally claim the
same Steam app, PSN concept, Xbox product, and so on.

### Guide records

A guide is a standalone accessibility guide or walkthrough, not tied to any
particular game or mod. Unlike games and mods, a guide has no permanent ID
beyond its filename and no archive lifecycle: editing or deleting the file
is how you update or retire it. Use `draft: true`
to keep an unfinished guide out of the website. Drafts remain visible in this
public repository.

Guide files live in `registry/guides/<id>.md`. Each file starts with a
frontmatter block:

```text
---
title: Getting started with a screen reader
author: Jane Doe
lastModified: 2026-08-01
---

Body Markdown starts here, after exactly one blank line.
```

A draft adds one more line at the end of the frontmatter:

```text
---
title: Getting started with a screen reader
author: Jane Doe
lastModified: 2026-08-01
draft: true
---
```

- `title` is required. `author`, `lastModified`, and `draft` are optional. Omit the
  line entirely rather than leaving it blank.
- `lastModified` uses `YYYY-MM-DD`.
- `draft: true` keeps the guide in the repository without publishing it. The
  site skips drafts on import, so the guide has no page until the line is
  removed. It is the only accepted value; leave the line out to publish.
- The guide ID comes from its filename without `.md`, using lowercase words
  separated by hyphens. Do not add an `id` field to the frontmatter.
- The body is plain Markdown: headings, paragraphs, emphasis, links, lists,
  blockquotes, and code blocks. Raw HTML is not supported.
- Like JSON records, a guide file has one canonical form. The
  `blindgaming-registry format` command formats guides as well as JSON records.

## Validation

Pull requests run validation automatically. For ordinary record edits, that is
usually all you need.

The validator checks schema shape, canonical JSON output, duplicate store
identities, archive rules, URL policy, and cross-links between games and mods.

To run the local validator against your checkout:

```sh
uv sync --locked --extra test
uv run blindgaming-registry validate
uv run blindgaming-registry format --check
```

For schema, validator, or large registry changes, see the
[maintainer checks](.github/MAINTAINING.md). Routine updates may change up to
`max(100, ceil(existing game and mod records * 0.10))` records. Larger changes
require a maintainer to apply the `registry-mass-change-approved` label.

The validator emits one line per result: `LEVEL path JSON-Pointer: message`.
Warnings do not fail validation. Validation errors exit with status 1;
command-line usage and filesystem errors exit with status 2.

Merged changes become available to Blind Gaming Database on its next successful
registry import.

## License

This repository uses the [MIT License](LICENSE). Third-party names, marks, and
material remain their owners' property; this repository does not grant rights it
does not hold.
