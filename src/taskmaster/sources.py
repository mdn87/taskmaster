"""Bounded read footprint for an Autowork run store (plan section 3, F14).

`autowork campaign outcome` locates each run with `rglob(run_id)` over the
whole runs root and, for attestation cache hits, scans every
`capability-attestation-usage.json` under it. The footprint therefore covers
every directory and every record file with one of the owner's record names,
at any depth. `credential-isolation/` and `child-output/` are never entered.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from .canonical import sha256_hex
from .strictjson import StrictJSONError, loads

# Record files `dispatch_outcome.py` reads through DispatchArtifactPaths.
RECORD_BASENAMES = frozenset({
    "agent-assignment.json",
    "assignment-failure.json",
    "behavior-receipt.json",
    "capability-attestation-usage.json",
    "capability-attestation.json",
    "dispatch-feedback.json",
    "dispatch-metrics.json",
    "node-inventory.json",
    "precondition-failure.json",
    "remote-execution-result.json",
    "remote-node-attempts.jsonl",
    "remote-node-fallback-decisions.jsonl",
    "request-summary.json",
    "result.json",
    "route-attempts.jsonl",
    "state.json",
    "subject-manifest.json",
})
SKIPPED_DIRS = frozenset({"credential-isolation", "child-output"})
LEDGER_SUFFIXES = (".reservation.json", ".result.json", ".outcome.json")
MANIFEST_SCHEMA = "taskmaster.corpus-manifest/v1"

MAX_FOOTPRINT_ENTRIES = 20_000
MAX_FOOTPRINT_BYTES = 64 * 1024 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_SMALL_FILE_BYTES = 1024 * 1024


class SourceError(Exception):
    """A source read failure carrying the observation error code to record."""

    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class Footprint:
    dirs: tuple[str, ...]          # relative POSIX paths, sorted
    files: dict[str, str]          # relative POSIX path -> sha256
    data: dict[str, bytes]         # relative POSIX path -> bytes
    skipped_dirs: tuple[str, ...] = ()  # never entered; unsafe for the owner's unrestricted rglob

    def signature(self) -> tuple:
        return self.dirs, tuple(sorted(self.files.items())), self.skipped_dirs

    def run_dirs(self, run_id: str) -> list[str]:
        return [d for d in self.dirs if d.rsplit("/", 1)[-1] == run_id]

    def records_in(self, run_dir: str) -> dict[str, str]:
        """Run records and its owner ledger, keyed relative to the run directory."""
        prefix = run_dir + "/"
        return {p[len(prefix):]: p for p in self.files if p.startswith(prefix) and (
            "/" not in p[len(prefix):] or
            (p[len(prefix):].startswith("owner-attempts/") and p[len(prefix):].count("/") == 1))}


def _is_link(st: os.stat_result) -> bool:
    attrs = getattr(st, "st_file_attributes", 0)
    return stat.S_ISLNK(st.st_mode) or bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def footprint(root: Path) -> Footprint:
    """Enumerate and hash the owner's read set under `root`."""
    try:
        st = os.stat(root, follow_symlinks=False)
    except OSError:
        raise SourceError("required_source_failed", "run store is not readable") from None
    if _is_link(st) or not stat.S_ISDIR(st.st_mode):
        raise SourceError("required_source_failed", "run store is not a plain directory")

    dirs: list[str] = []
    files: dict[str, str] = {}
    data: dict[str, bytes] = {}
    skipped: list[str] = []
    total = 0
    discovered = 0
    stack = [(root, "")]
    while stack:
        current, rel = stack.pop()
        try:
            with os.scandir(current) as scan:
                entries = sorted(islice(scan, MAX_FOOTPRINT_ENTRIES + 1), key=lambda e: e.name)
        except OSError:
            raise SourceError("required_source_failed", "run store directory is not readable") from None
        discovered += len(entries)
        if discovered > MAX_FOOTPRINT_ENTRIES:
            raise SourceError("required_input_invalid", "run store footprint over bounds")
        for entry in entries:
            entry_rel = f"{rel}/{entry.name}" if rel else entry.name
            try:
                est = entry.stat(follow_symlinks=False)
            except OSError:
                raise SourceError("required_source_failed", "run store entry is not readable") from None
            if _is_link(est):
                raise SourceError("required_input_invalid", "run store contains a link or reparse point")
            if stat.S_ISDIR(est.st_mode):
                if entry.name in SKIPPED_DIRS:
                    skipped.append(entry_rel)
                    continue
                dirs.append(entry_rel)
                stack.append((Path(entry.path), entry_rel))
            elif entry.name in RECORD_BASENAMES or (
                current.name == "owner-attempts" and entry.name.endswith(LEDGER_SUFFIXES)
            ):
                if not stat.S_ISREG(est.st_mode):
                    raise SourceError("required_input_invalid", "record is not a regular file")
                if est.st_size > MAX_RECORD_BYTES:
                    raise SourceError("required_input_invalid", "record file over bounds")
                try:
                    with open(entry.path, "rb") as handle:
                        raw = handle.read(MAX_RECORD_BYTES + 1)
                except OSError:
                    raise SourceError("required_source_failed", "record file is not readable") from None
                if len(raw) > MAX_RECORD_BYTES:
                    raise SourceError("required_input_invalid", "record file over bounds")
                total += len(raw)
                files[entry_rel] = sha256_hex(raw)
                data[entry_rel] = raw
            if len(dirs) + len(files) + len(skipped) > MAX_FOOTPRINT_ENTRIES or total > MAX_FOOTPRINT_BYTES:
                raise SourceError("required_input_invalid", "run store footprint over bounds")
    return Footprint(tuple(sorted(dirs)), files, data, tuple(sorted(skipped)))


def read_small(path: Path, missing_code: str = "required_source_failed") -> bytes:
    try:
        st = os.stat(path, follow_symlinks=False)
        if _is_link(st) or not stat.S_ISREG(st.st_mode):
            raise SourceError(missing_code, "not a regular file")
        with open(path, "rb") as handle:
            raw = handle.read(MAX_SMALL_FILE_BYTES + 1)
    except SourceError:
        raise
    except OSError:
        raise SourceError(missing_code, "file is not readable") from None
    if len(raw) > MAX_SMALL_FILE_BYTES:
        raise SourceError("required_input_invalid", "file over bounds")
    return raw


def load_manifest(path: Path) -> tuple[str | None, dict[str, str]]:
    """Saved-snapshot manifest: (captured_at, {relpath: sha256})."""
    from . import timefmt
    from .identity import is_sha256_hex

    raw = read_small(path)
    try:
        doc = loads(raw, max_bytes=MAX_SMALL_FILE_BYTES)
    except StrictJSONError:
        raise SourceError("required_input_invalid", "manifest is not strict JSON") from None
    if not isinstance(doc, dict) or set(doc) != {"schema", "captured_at", "files"} \
            or doc["schema"] != MANIFEST_SCHEMA:
        raise SourceError("required_input_invalid", "manifest schema mismatch")
    captured = doc["captured_at"]
    if captured is not None and not timefmt.is_valid(captured):
        raise SourceError("required_input_invalid", "manifest captured_at is not a valid time")
    files = doc["files"]
    if not isinstance(files, dict) or not all(isinstance(k, str) and is_sha256_hex(v) for k, v in files.items()):
        raise SourceError("required_input_invalid", "manifest files table is malformed")
    return captured, files


def manifest_for(root: Path, captured_at: str | None) -> dict:
    """Build a manifest document from a store's current footprint."""
    fp = footprint(root)
    return {"schema": MANIFEST_SCHEMA, "captured_at": captured_at, "files": dict(sorted(fp.files.items()))}


def parse_record(raw: bytes) -> dict | None:
    """Best-effort strict parse of one JSON record; None if it is not an object."""
    try:
        doc = loads(raw, max_bytes=MAX_RECORD_BYTES, allow_floats=True)
    except StrictJSONError:
        return None
    return doc if isinstance(doc, dict) else None
