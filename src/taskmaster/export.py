"""Export assembly and the shared reader acceptance rules (plan sections 3, 5, 7).

Batch 1 assembles the export bytes in memory. Atomic publication and the OS
writer lock are Batch 2 and wrap these functions without changing them.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import schema, timefmt
from .canonical import canonical, sha256_hex, snapshot_id
from .strictjson import StrictJSONError, loads


class ExportError(Exception):
    """A whole-file failure with a typed code and a CLI exit status."""

    EXIT = {"export_missing": 3, "invalid_export": 4, "unsupported_export": 4, "export_unreadable": 8}

    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason

    @property
    def exit_code(self) -> int:
        return self.EXIT[self.code]


def assemble(entries: list[dict], published_at: str, max_age_seconds: int) -> bytes:
    """Serialize a validated export. Raises ExportError if it fails validation."""
    ordered = sorted(entries, key=lambda e: e["objective_key"])
    doc = {
        "schema": schema.EXPORT_SCHEMA,
        "schema_version": 1,
        "snapshot_id": snapshot_id((e["objective_key"], e["trail_sha256"]) for e in ordered),
        "published_at": published_at,
        "max_age_seconds": max_age_seconds,
        "entries": ordered,
    }
    data = canonical(doc)
    verify(data)  # the writer runs the same acceptance rules as every reader
    for entry in ordered:
        if entry["content_json"] is not None:
            content = schema.parse_content(entry)
            if schema.assert_canonical_content(content) != entry["content_json"]:
                raise ExportError("invalid_export", "writer produced non-canonical content")
    return data


def verify(data: bytes) -> dict:
    """Full reader acceptance: strict parse, closed schema, every hash recomputed."""
    try:
        doc = loads(data, max_bytes=schema.MAX_EXPORT_BYTES)
    except StrictJSONError as exc:
        code = "invalid_export"
        raise ExportError(code, exc.reason) from None
    if not isinstance(doc, dict):
        raise ExportError("invalid_export", "root is not an object")
    version = doc.get("schema_version")
    if doc.get("schema") != schema.EXPORT_SCHEMA or version != 1:
        newer = isinstance(version, int) and not isinstance(version, bool) and version > 1
        prefixed = isinstance(doc.get("schema"), str) and doc["schema"].startswith("taskmaster.export/")
        if newer or (prefixed and doc.get("schema") != schema.EXPORT_SCHEMA):
            raise ExportError("unsupported_export", "export schema version is not supported")
        raise ExportError("invalid_export", "not a taskmaster export")
    try:
        schema._keys(doc, {"schema", "schema_version", "snapshot_id", "published_at", "max_age_seconds", "entries"},
                     "export")
        schema._time(doc["published_at"], "published_at", nullable=False)
        schema._int(doc["max_age_seconds"], "max_age_seconds", 1, 31_536_000)
        entries = schema._list(doc["entries"], "entries", schema.MAX_OBJECTIVES)
        for i, entry in enumerate(entries):
            schema.validate_entry(entry, i)
            schema.parse_content(entry)
        keys = [e["objective_key"] for e in entries]
        if keys != sorted(set(keys)):
            raise schema.SchemaError("entries must be sorted by objective_key without duplicates")
        tuples = [(e["namespace"], e["id"]) for e in entries]
        if len(set(tuples)) != len(tuples):
            raise schema.SchemaError("duplicate objective tuple")
        expected = snapshot_id((e["objective_key"], e["trail_sha256"]) for e in entries)
        if doc["snapshot_id"] != expected:
            raise schema.SchemaError("snapshot_id does not match entries")
        overhead = len(data) - sum(schema.entry_size(e) for e in entries)
        if overhead > schema.MAX_ENVELOPE_OVERHEAD:
            raise schema.SchemaError("envelope overhead over bounds")
    except schema.SchemaError as exc:
        raise ExportError("invalid_export", exc.reason) from None
    return doc


def file_sha256(data: bytes) -> str:
    return sha256_hex(data)


def read_bytes(path: Path, *, attempts: int = 3, delays=(0.05, 0.1)) -> bytes:
    """Bounded read; sharing/access failures retry, then export_unreadable."""
    last = None
    for attempt in range(attempts):
        try:
            with open(path, "rb") as handle:
                data = handle.read(schema.MAX_EXPORT_BYTES + 1)
            if len(data) > schema.MAX_EXPORT_BYTES:
                raise ExportError("invalid_export", "too_large")
            return data
        except FileNotFoundError:
            raise ExportError("export_missing", "export file not found") from None
        except IsADirectoryError:
            raise ExportError("export_unreadable", "export path is a directory") from None
        except PermissionError as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(delays[min(attempt, len(delays) - 1)])
        except OSError as exc:
            last = exc
            break
    raise ExportError("export_unreadable", type(last).__name__ if last else "unreadable")


def load(path: Path) -> tuple[dict, bytes]:
    data = read_bytes(path)
    return verify(data), data


def is_valid_time(value: str) -> bool:
    return timefmt.is_valid(value)
