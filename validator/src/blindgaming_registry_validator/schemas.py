from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator, validators
from referencing import Registry, Resource


REGISTRY_SCHEMA_VERSION = 1
_SCHEMA_RESOURCE_PATH = ("_schemas", "v1")
_COMMON_ID = "https://blindgaming.net/schemas/v1/common.schema.json"
_SCHEMA_IDS = {
    "common": _COMMON_ID,
    "game": "https://blindgaming.net/schemas/v1/game.schema.json",
    "mod": "https://blindgaming.net/schemas/v1/mod.schema.json",
}


def _is_json_integer(_: object, instance: object) -> bool:
    return isinstance(instance, int) and not isinstance(instance, bool)


RegistryDraft202012Validator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", _is_json_integer
    ),
)


def _schema_directory() -> resources.abc.Traversable:
    packaged = resources.files("blindgaming_registry_validator").joinpath(
        *_SCHEMA_RESOURCE_PATH
    )
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[3] / "schemas" / "v1"


def _load_documents() -> tuple[dict[str, Mapping[str, object]], Registry]:
    directory = _schema_directory()
    documents = {
        name: json.loads(directory.joinpath(f"{name}.schema.json").read_bytes())
        for name in _SCHEMA_IDS
    }
    registry = Registry().with_resources(
        (document["$id"], Resource.from_contents(document))
        for document in documents.values()
    )
    return documents, registry


def _bundle_common_definitions(
    schema: Mapping[str, object], common: Mapping[str, object]
) -> Mapping[str, object]:
    bundled = copy.deepcopy(schema)
    common_definitions = copy.deepcopy(common["$defs"])

    def replace_common_references(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: (
                    f"#/$defs/{item.removeprefix(f'{_COMMON_ID}#/$defs/')}"
                    if key == "$ref" and isinstance(item, str) and item.startswith(f"{_COMMON_ID}#/$defs/")
                    else replace_common_references(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [replace_common_references(item) for item in value]
        return value

    bundled["$defs"] = common_definitions
    return replace_common_references(bundled)


def schema_for(kind: Literal["game", "mod"], version: int) -> Mapping[str, object]:
    if version != REGISTRY_SCHEMA_VERSION:
        raise ValueError(f"unsupported registry schema version: {version}")

    documents, registry = _load_documents()
    try:
        schema = registry.contents(_SCHEMA_IDS[kind])
    except KeyError as error:
        raise ValueError(f"unsupported registry schema kind: {kind}") from error
    return _bundle_common_definitions(schema, documents["common"])


def validator_for(kind: Literal["game", "mod"], version: int) -> RegistryDraft202012Validator:
    return RegistryDraft202012Validator(schema_for(kind, version))
