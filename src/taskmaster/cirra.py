"""Read-only adapter for Cirra's loopback audit route (work-view concept, sections 5 and 11).

`live` reads the route with GET requests to 127.0.0.1 and nothing else;
`saved_snapshot` serves recorded responses from disk, checked against a
`taskmaster.corpus-manifest/v1` manifest, which is how the adapter is tested
without a running Cirra. Both see the same envelopes: protocol, store schema
version, the audit store's generation, the record and its SHA-256. One read
spans several responses; a generation that moves between them is retried once
and then refused, never merged. Taskmaster never opens cirra.sqlite3 or an
audit packet, and copies Cirra's gate verdicts without recomputing them.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import schema
from .canonical import canonical, sha256_hex
from .config import Store, loopback_port
from .identity import IdentityError, is_sha256_hex, validate_id
from .sources import MAX_FOOTPRINT_BYTES, MAX_FOOTPRINT_ENTRIES, MAX_RECORD_BYTES, SourceError, load_manifest
from .strictjson import StrictJSONError, loads

PROTOCOL = "cirra.audit-reads.v1"
SCHEMA_VERSION = 3  # the Cirra store schema the envelopes are pinned to
INDEX_LIMIT = 200  # the route's largest page: more attempts for one head is over_bounds, never paged
TIMEOUT_SECONDS = 10
ENVELOPE_KEYS = frozenset({"protocol", "schema_version", "generation", "record", "sha256"})
RECORDINGS = {"healthz": "healthz.json", "audits-by-head": "audits-by-head/{}.json",
              "audit": "audits/{}.json", "gate": "gates/{}.json"}
# Observation error code -> health of the optional cirra source row.
HEALTH = {"optional_source_unavailable": "unavailable", "optional_source_invalid": "invalid",
          "over_bounds": "invalid", "owner_version_mismatch": "unsupported",
          "source_changed_during_read": "changed"}
_HEAD = re.compile(r"[0-9a-f]{40}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_TITLE_IDS = re.compile(r"\[((?:C\d{3}|X\d{2})(?:, ?(?:C\d{3}|X\d{2}))*)\]")


@dataclass
class AuditRead:
    """One consistent read: validated records by key and the raw-byte digest of every response used."""

    owner_version: str | None
    generation: str
    captured_at: str | None
    inputs: list[dict] = field(default_factory=list)
    by_head: dict[str, list[str]] = field(default_factory=dict)
    audits: dict[str, tuple[dict, str]] = field(default_factory=dict)
    gates: dict[str, tuple[dict, str] | None] = field(default_factory=dict)


class _Moved(Exception):
    """The store's generation differed between two responses of one read."""


def is_head(value: object) -> bool:
    return isinstance(value, str) and _HEAD.match(value) is not None


def finding_key(finding: dict) -> str:
    """Cirra's key for one finding: its sorted catalog ids, then the posix path (cirra.audits.finding_key)."""
    match = _TITLE_IDS.match(finding["title"])
    ids = sorted(part.strip() for part in match.group(1).split(",")) if match else []
    return ",".join(ids) + ":" + finding["path"].replace("\\", "/")


def item_id(repository_key: str, key: str) -> str:
    """The derived finding item: one defect followed across revisions of one repository."""
    return sha256_hex(canonical([repository_key, key]))


def observation_id(attempt_id: str, key: str) -> str:
    return sha256_hex(canonical([attempt_id, key]))


def recording_name(record_type: str, record_id: str) -> str:
    return RECORDINGS[record_type].format(record_id)


def route_path(record_type: str, record_id: str) -> str:
    if record_type == "healthz":
        return "/healthz"
    if record_type == "audits-by-head":
        return f"/audits?head={record_id}&limit={INDEX_LIMIT}"
    return f"/audits/{record_id}" if record_type == "audit" else f"/gates/{record_id}"


def read(store: Store, heads: list[str]) -> AuditRead:
    """Every attempt Cirra recorded for the given heads, with their gates, and nothing else."""
    reader = _Live(store) if store.mode == "live" else _Snapshot(store)
    for round_number in range(2):
        try:
            return _read_once(reader, sorted(set(heads)))
        except _Moved:
            if round_number == 1:
                raise SourceError("source_changed_during_read",
                                  "the audit store generation moved during the read") from None
    raise AssertionError("unreachable")


# --- transports ----------------------------------------------------------------

class _Live:
    captured_at = None

    def __init__(self, store: Store):
        self.port = loopback_port(store.url)

    def fetch(self, record_type: str, record_id: str) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=TIMEOUT_SECONDS)
        try:
            connection.request("GET", route_path(record_type, record_id))
            response = connection.getresponse()
            return response.status, response.read(MAX_RECORD_BYTES + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise SourceError("optional_source_unavailable", f"audit route: {exc}") from None
        finally:
            connection.close()


class _Snapshot:
    def __init__(self, store: Store):
        try:
            self.captured_at, expected = load_manifest(store.manifest)
        except SourceError as exc:
            code = "optional_source_unavailable" if exc.code == "required_source_failed" else "optional_source_invalid"
            raise SourceError(code, exc.reason) from None
        self.files = _read_tree(store.path)
        if {name: data[0] for name, data in self.files.items()} != expected:
            raise SourceError("optional_source_invalid", "recorded responses differ from their manifest")

    def fetch(self, record_type: str, record_id: str) -> tuple[int, bytes]:
        data = self.files.get(recording_name(record_type, record_id))
        return (200, data[1]) if data is not None else (404, b"")


def _read_tree(root: Path) -> dict[str, tuple[str, bytes]]:
    """Every regular file under root as {posix relpath: (sha256, bytes)}, bounded like a footprint."""
    try:
        if not stat.S_ISDIR(os.stat(root).st_mode):
            raise SourceError("optional_source_invalid", "recorded responses root is not a directory")
    except OSError as exc:
        raise SourceError("optional_source_unavailable", f"recorded responses: {exc.strerror}") from None
    files: dict[str, tuple[str, bytes]] = {}
    entries, total, stack = 0, 0, [Path(root)]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as scan:
                listing = [(entry.path, entry.stat(follow_symlinks=False)) for entry in scan]
        except OSError as exc:
            raise SourceError("optional_source_unavailable", f"recorded responses: {exc.strerror}") from None
        for path, st in listing:
            entries += 1
            if entries > MAX_FOOTPRINT_ENTRIES:
                raise SourceError("over_bounds", "too many recorded responses")
            if stat.S_ISDIR(st.st_mode):
                stack.append(Path(path))
                continue
            if not stat.S_ISREG(st.st_mode):
                raise SourceError("optional_source_invalid", "recorded responses contain a link or special file")
            total += st.st_size
            if st.st_size > MAX_RECORD_BYTES or total > MAX_FOOTPRINT_BYTES:
                raise SourceError("over_bounds", "recorded responses exceed the read bound")
            try:
                data = Path(path).read_bytes()
            except OSError as exc:
                raise SourceError("optional_source_unavailable", f"recorded responses: {exc.strerror}") from None
            files[Path(path).relative_to(root).as_posix()] = (sha256_hex(data), data)
    return files


# --- envelopes and records -----------------------------------------------------

def _digest(record: object) -> str:
    """Cirra's record digest (cirra.domain.digest): compact ASCII JSON with sorted keys."""
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _envelope(status: int, body: bytes) -> tuple[object, str, str] | None:
    """(record, raw_sha256, generation) of one 200 response; None for a 404."""
    if len(body) > MAX_RECORD_BYTES:
        raise SourceError("over_bounds", "a route response exceeds the record bound")
    if status == 404:
        return None
    if status == 503:
        raise SourceError("optional_source_unavailable", "the audit store is unavailable")
    if status != 200:
        raise SourceError("optional_source_invalid", f"unexpected route status {status}")
    try:
        doc = loads(body, max_bytes=MAX_RECORD_BYTES, allow_floats=True)
    except StrictJSONError:
        raise SourceError("optional_source_invalid", "a route response is not strict JSON") from None
    if not isinstance(doc, dict) or set(doc) != ENVELOPE_KEYS:
        raise SourceError("optional_source_invalid", "a route response is not a read envelope")
    if doc["protocol"] != PROTOCOL or doc["schema_version"] != SCHEMA_VERSION:
        raise SourceError("owner_version_mismatch", "the route protocol or store schema is not the pinned one")
    if not is_sha256_hex(doc["generation"]) or not is_sha256_hex(doc["sha256"]):
        raise SourceError("optional_source_invalid", "a route response carries no usable digests")
    try:
        digest = _digest(doc["record"])
    except (TypeError, ValueError):
        raise SourceError("optional_source_invalid", "a record cannot be digested") from None
    if digest != doc["sha256"]:
        raise SourceError("optional_source_invalid", "a record does not match its SHA-256")
    return doc["record"], sha256_hex(body), doc["generation"]


def _safe(value: object) -> bool:
    return isinstance(value, str) and _SAFE_ID.match(value) is not None


def _index(record: object, head: str) -> list[str]:
    if not isinstance(record, dict) or not isinstance(record.get("audits"), list):
        raise SourceError("optional_source_invalid", "the attempt index is malformed")
    if record.get("next") is not None:
        raise SourceError("over_bounds", "more attempts for one head than one bounded page")
    ids = []
    for row in record["audits"]:
        attempt_id = row.get("attempt_id") if isinstance(row, dict) else None
        if not _safe(attempt_id) or row.get("head") != head:
            raise SourceError("optional_source_invalid", "the attempt index lists an unusable attempt")
        ids.append(attempt_id)
    return sorted(set(ids))


def _detail(record: object, attempt_id: str, head: str) -> dict:
    """Shape checks only: tier, state, severities and gate values are copied by the builder as recorded."""
    if not isinstance(record, dict) or record.get("attempt_id") != attempt_id or record.get("head") != head:
        raise SourceError("optional_source_invalid", "an attempt record does not match its index entry")
    audit_id, comparison, report = record.get("audit_id"), record.get("comparison"), record.get("report")
    findings = report.get("findings", []) if isinstance(report, dict) else []
    keys = [comparison.get(name) or [] for name in ("new", "open", "resolved")] if isinstance(comparison, dict) else []
    try:
        validate_id(record.get("repository_key"), "repository_key")
        if not isinstance(findings, list) or not isinstance(keys, list) or not all(isinstance(k, list) for k in keys):
            raise IdentityError("report", "not_a_list")
        for finding in findings:
            if not isinstance(finding, dict):
                raise IdentityError("finding", "not_an_object")
            validate_id(finding.get("title"), "title")
            validate_id(finding.get("path"), "path")
        for key in (k for group in keys for k in group):
            validate_id(key, "finding_key")
    except IdentityError:
        raise SourceError("optional_source_invalid", "an attempt record carries an unusable report") from None
    if audit_id is not None and not _safe(audit_id) or comparison is not None and not isinstance(comparison, dict):
        raise SourceError("optional_source_invalid", "an attempt record carries an unusable audit id or comparison")
    return record


def _gate(record: object, audit_id: str) -> dict:
    if not isinstance(record, dict) or record.get("audit_id") != audit_id \
            or not (record.get("publication") is None or isinstance(record["publication"], dict)) \
            or not isinstance(record.get("waivers"), list) or not all(isinstance(w, dict) for w in record["waivers"]):
        raise SourceError("optional_source_invalid", "a gate record is malformed")
    return record


def _read_once(reader, heads: list[str]) -> AuditRead:
    inputs: dict[tuple[str, str], dict] = {}
    generation = None
    total = 0

    def get(record_type: str, record_id: str) -> tuple[object, str | None]:
        nonlocal generation, total
        status, body = reader.fetch(record_type, record_id)
        total += len(body)
        if total > MAX_FOOTPRINT_BYTES:
            raise SourceError("over_bounds", "the audit read exceeds the footprint byte bound")
        envelope = _envelope(status, body)
        if envelope is None:
            return None, None
        record, raw_sha, seen = envelope
        if generation is None:
            generation = seen
        elif seen != generation:
            raise _Moved()
        inputs[(record_type, record_id)] = {"record_type": record_type, "record_id": record_id, "raw_sha256": raw_sha}
        if len(inputs) > schema.MAX_INPUTS_PER_SOURCE:
            raise SourceError("over_bounds", "the audit read exceeds the inputs bound")
        return record, raw_sha

    health, _ = get("healthz", "healthz")
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise SourceError("optional_source_unavailable", "the route reports no healthy audit store")
    version = health.get("version")
    result = AuditRead(version if isinstance(version, str) else None, generation, reader.captured_at)
    for head in heads:
        index, _ = get("audits-by-head", head)
        if index is None:
            raise SourceError("optional_source_invalid", "no recorded index response for a head")
        result.by_head[head] = _index(index, head)
    for head, attempt_ids in result.by_head.items():
        for attempt_id in attempt_ids:
            if attempt_id in result.audits:
                continue
            detail, raw_sha = get("audit", attempt_id)
            if detail is None:
                raise SourceError("optional_source_invalid", "an indexed attempt has no detail record")
            result.audits[attempt_id] = (_detail(detail, attempt_id, head), raw_sha)
    for detail, _ in result.audits.values():
        audit_id = detail["audit_id"]
        if audit_id is None or audit_id in result.gates:
            continue
        gate, raw_sha = get("gate", audit_id)
        result.gates[audit_id] = None if gate is None else (_gate(gate, audit_id), raw_sha)
    result.inputs = sorted(inputs.values(), key=lambda i: schema.sort_key([i["record_type"], i["record_id"]]))
    return result
