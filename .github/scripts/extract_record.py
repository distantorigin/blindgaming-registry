"""Validate record blocks from a registry submission issue.

Reads the issue body from ISSUE_BODY, extracts the fenced JSON under the
record headings, writes them into a scratch copy of the registry, and runs
the repository's own validator over the result. Writes nothing back.

The validator's ``--base`` snapshot must contain only the repository's
allowed registry layout, so this builds a second scratch copy of `registry/`
rather than pointing `--base` at the checkout root: after `uv sync` the
checkout root also holds `.venv`, which the validator rejects as an
unexpected path.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_BLOCK = re.compile(
    r"###\s*(?P<label>(?:Companion\s+)?(?:(?:Game|Mod)\s+record|Record) JSON)"
    r"\s*\n+```(?:json)?\s*\n(?P<body>.*?)\n```",
    re.I | re.S)

# Matches the schemas' registryId pattern. Checked before the id is used to
# build a filesystem path, so it cannot contain a path separator or a
# dot-segment.
_REGISTRY_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _record_kind(record: dict) -> str:
    return "mods" if "gameIds" in record else "games"


def _extract_records(body: str) -> list[tuple[str, dict]]:
    records = []
    for match in _BLOCK.finditer(body):
        label = " ".join(match.group("label").split())
        raw = match.group("body")
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as error:
            print(f"The {label} block is not valid JSON: {error}")
            raise SystemExit(1) from error
        if not isinstance(record, dict):
            print(f"The {label} block must be a JSON object.")
            raise SystemExit(1)
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier:
            print(f"The {label} block has no string id.")
            raise SystemExit(1)
        if _REGISTRY_ID.fullmatch(identifier) is None:
            print(f"The {label} block id is not a valid registry id.")
            raise SystemExit(1)
        records.append((_record_kind(record), record))
    return records


def main() -> int:
    body = os.environ.get("ISSUE_BODY", "")
    records = _extract_records(body)
    if not records:
        print("No record JSON block found. A curator will translate this "
              "issue by hand.")
        return 0
    seen = set()
    for kind, record in records:
        key = (kind, record["id"])
        if key in seen:
            print(f"Duplicate record id in submission: {record['id']}")
            return 1
        seen.add(key)
    root = Path(tempfile.mkdtemp())
    base = Path(tempfile.mkdtemp())
    try:
        shutil.copytree(Path("registry"), root / "registry")
        shutil.copytree(Path("registry"), base / "registry")
        for kind, record in records:
            target = root / "registry" / kind / f"{record['id']}.json"
            target.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        completed = subprocess.run(
            ["uv", "run", "blindgaming-registry", "validate",
             "--root", str(root), "--base", str(base)],
            capture_output=True, text=True)
        print(completed.stdout or completed.stderr or "No diagnostics.")
        return completed.returncode
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
