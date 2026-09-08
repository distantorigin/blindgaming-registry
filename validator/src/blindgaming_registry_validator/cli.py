from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from . import VALIDATOR_VERSION
from .formatting import canonical_format_pointer, canonical_guide_bytes, canonical_record_bytes
from .loading import (
    RegistryParseError,
    DuplicateKeyError,
    UnexpectedPathError,
    _duplicate_pointer,
    _is_guide_path,
    _is_record_path,
    _parse_guide_frontmatter,
    _repository_paths,
    reject_duplicate_pairs,
)
from .models import Diagnostic
from .schemas import REGISTRY_SCHEMA_VERSION, validator_for
from .validation import _surrogate_pointers, validate_snapshot


_BIDI_CONTROL_CODEPOINTS = frozenset(
    (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
)


class _UsageError(ValueError):
    pass


class _DiagnosticParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def _display(value: object) -> str:
    rendered: list[str] = []
    for character in str(value):
        codepoint = ord(character)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            rendered.append(f"\\x{codepoint:02x}")
        elif codepoint in _BIDI_CONTROL_CODEPOINTS:
            rendered.append(f"\\u{codepoint:04x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def _print_diagnostics(diagnostics: Iterable[Diagnostic]) -> None:
    for diagnostic in diagnostics:
        print(
            f"{diagnostic.level.upper()} {_display(diagnostic.path)} "
            f"{_display(diagnostic.pointer)}: {_display(diagnostic.message)}"
        )


def _missing_directory_diagnostic(label: str, path: Path) -> Diagnostic | None:
    if path.is_symlink():
        return Diagnostic("error", ".", "", f"{label} directory must not be a symlink")
    if path.is_dir():
        return None
    return Diagnostic("error", ".", "", f"{label} directory does not exist: {path}")


def _stage_format_bytes(path: Path, contents: bytes, suffix: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=suffix,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise
    return temporary


def _apply_format_writes(
    root: Path,
    pending_writes: list[tuple[str, Path, bytes, bytes]],
) -> tuple[Diagnostic, ...]:
    resolved_root = root.resolve()
    staged: list[tuple[str, Path, Path, Path | None]] = []
    applied: list[tuple[str, Path, Path, Path | None]] = []
    retained_backups: set[Path] = set()
    failure: OSError | None = None
    diagnostics: list[Diagnostic] = []
    try:
        for relative, path, original, canonical in pending_writes:
            backup = _stage_format_bytes(path, original, ".registry-original")
            staged.append((relative, path, backup, None))
            replacement = _stage_format_bytes(path, canonical, ".registry-format")
            staged[-1] = (relative, path, backup, replacement)
        for item in staged:
            _, path, _, replacement = item
            assert replacement is not None
            os.replace(replacement, path)
            applied.append(item)
    except OSError as error:
        failure = error
        for relative, path, backup, _ in reversed(applied):
            try:
                os.replace(backup, path)
            except OSError as rollback_error:
                retained_backups.add(backup)
                backup_path = backup.relative_to(resolved_root).as_posix()
                diagnostics.append(
                    Diagnostic(
                        "error",
                        relative,
                        "",
                        f"format apply failed: {error}; rollback failed: {rollback_error}; "
                        f"recovery backup retained at {backup_path}",
                    )
                )
    finally:
        for _, _, backup, replacement in staged:
            for temporary in (backup, replacement):
                if temporary is None or temporary in retained_backups:
                    continue
                with contextlib.suppress(OSError):
                    temporary.unlink()
    if diagnostics:
        return tuple(diagnostics)
    if failure is not None:
        return (Diagnostic("error", ".", "", str(failure)),)
    return ()


def _format_diagnostics(root: Path, *, check: bool) -> tuple[int, tuple[Diagnostic, ...]]:
    missing = _missing_directory_diagnostic("root", root)
    if missing is not None:
        return 2, (missing,)

    diagnostics: list[Diagnostic] = []
    pending_writes: list[tuple[str, Path, bytes, bytes]] = []
    try:
        paths = _repository_paths(root)
    except (OSError, UnexpectedPathError) as error:
        return 2, (
            Diagnostic(
                "error",
                getattr(error, "path", "."),
                getattr(error, "pointer", ""),
                getattr(error, "message", str(error)),
            ),
        )

    for relative, path in paths:
        if _is_guide_path(relative):
            try:
                contents = path.read_bytes()
                text = contents.decode("utf-8")
            except OSError as error:
                diagnostics.append(Diagnostic("error", relative, "", str(error)))
                continue
            except UnicodeDecodeError:
                diagnostics.append(Diagnostic("error", relative, "", "invalid UTF-8"))
                continue
            try:
                frontmatter, body = _parse_guide_frontmatter(relative, text)
            except (RegistryParseError, DuplicateKeyError) as error:
                diagnostics.append(Diagnostic("error", relative, error.pointer, error.message))
                continue
            canonical = canonical_guide_bytes(frontmatter, body)
            if contents != canonical:
                pending_writes.append((relative, path, contents, canonical))
                diagnostics.append(
                    Diagnostic(
                        "error",
                        relative,
                        "",
                        "record is not canonically formatted",
                    )
                )
            continue
        if not _is_record_path(relative):
            continue
        kind = "game" if relative.startswith("registry/games/") else "mod"
        try:
            contents = path.read_bytes()
            record = json.loads(
                contents.decode("utf-8"), object_pairs_hook=reject_duplicate_pairs
            )
        except OSError as error:
            diagnostics.append(Diagnostic("error", relative, "", str(error)))
            continue
        except UnicodeDecodeError:
            diagnostics.append(Diagnostic("error", relative, "", "invalid UTF-8"))
            continue
        except json.JSONDecodeError:
            diagnostics.append(Diagnostic("error", relative, "", "invalid JSON"))
            continue
        if not isinstance(record, Mapping):
            diagnostics.append(
                Diagnostic("error", relative, "", "registry records must be JSON objects")
            )
            continue
        duplicate_pointer = _duplicate_pointer(record)
        if duplicate_pointer is not None:
            diagnostics.append(
                Diagnostic("error", relative, duplicate_pointer, "duplicate JSON object key")
            )
            continue
        surrogate_pointers = _surrogate_pointers(record)
        if surrogate_pointers:
            diagnostics.extend(
                Diagnostic(
                    "error",
                    relative,
                    pointer,
                    "text must contain only Unicode scalar values",
                )
                for pointer in surrogate_pointers
            )
            continue
        for error in validator_for(kind, REGISTRY_SCHEMA_VERSION).iter_errors(record):
            pointer = "".join(
                f"/{str(part).replace('~', '~0').replace('/', '~1')}"
                for part in error.absolute_path
            )
            diagnostics.append(Diagnostic("error", relative, pointer, error.message))
        if record.get("id") != Path(relative).stem:
            diagnostics.append(
                Diagnostic(
                    "error",
                    relative,
                    "/id",
                    "filename stem must match record id",
                )
            )
            continue
        if any(diagnostic.path == relative for diagnostic in diagnostics):
            continue
        canonical = canonical_record_bytes(record, kind)
        if contents != canonical:
            pending_writes.append((relative, path, contents, canonical))
            diagnostics.append(
                Diagnostic(
                    "error",
                    relative,
                    canonical_format_pointer(record, kind),
                    "record is not canonically formatted",
                )
            )
    diagnostics.sort(key=lambda item: (item.path, item.pointer, item.level, item.message))
    if any(
        diagnostic.message != "record is not canonically formatted"
        for diagnostic in diagnostics
    ):
        return 2, tuple(diagnostics)
    if not check:
        transaction_diagnostics = _apply_format_writes(root, pending_writes)
        if transaction_diagnostics:
            return 2, transaction_diagnostics
    return (1 if pending_writes and check else 0), tuple(diagnostics)


def _parser() -> argparse.ArgumentParser:
    parser = _DiagnosticParser(prog="blindgaming-registry")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("version")

    validate = subcommands.add_parser("validate")
    validate.add_argument("--root", default=Path("."), type=Path)
    validate.add_argument("--base", type=Path)
    validate.add_argument("--allow-mass-change", action="store_true")

    format_command = subcommands.add_parser("format")
    format_command.add_argument("--root", default=Path("."), type=Path)
    format_command.add_argument("--check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        parsed = _parser().parse_args(arguments)
    except _UsageError as error:
        _print_diagnostics((Diagnostic("error", ".", "", str(error)),))
        return 2
    except SystemExit as error:
        return int(error.code)

    if parsed.command == "version":
        print(f"blindgaming registry validator {VALIDATOR_VERSION}")
        return 0

    if parsed.command == "validate":
        missing = _missing_directory_diagnostic("root", parsed.root)
        if missing is None and parsed.base is not None:
            missing = _missing_directory_diagnostic("base", parsed.base)
        if missing is not None:
            _print_diagnostics((missing,))
            return 2
        try:
            report = validate_snapshot(
                parsed.root,
                base_root=parsed.base,
                allow_mass_change=parsed.allow_mass_change,
            )
        except OSError as error:
            _print_diagnostics((Diagnostic("error", ".", "", str(error)),))
            return 2
        _print_diagnostics(report.diagnostics)
        if report.has_io_error:
            return 2
        return 0 if report.ok else 1

    if parsed.command == "format":
        status, diagnostics = _format_diagnostics(parsed.root, check=parsed.check)
        if parsed.check or status == 2:
            _print_diagnostics(diagnostics)
        return status

    raise AssertionError(f"unsupported command: {parsed.command}")


def entrypoint() -> None:
    raise SystemExit(main())
