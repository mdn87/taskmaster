"""Canonical serialization C(v) and the identity hashes (plan section 3)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

MAX_SAFE_INT = 2**53 - 1


class CanonicalError(ValueError):
    """A value lies outside C's permitted domain."""


def _check_str(s: str) -> None:
    for ch in s:
        if 0xD800 <= ord(ch) <= 0xDFFF:
            raise CanonicalError("unpaired surrogate in string")


def check_domain(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise CanonicalError("nesting too deep")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        _check_str(value)
        return
    if isinstance(value, int):
        if not -MAX_SAFE_INT <= value <= MAX_SAFE_INT:
            raise CanonicalError("integer outside the interoperable range")
        return
    if isinstance(value, float):
        raise CanonicalError("floats are outside the canonical domain")
    if isinstance(value, list):
        for item in value:
            check_domain(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise CanonicalError("object keys must be ASCII strings")
            check_domain(item, depth + 1)
        return
    raise CanonicalError(f"unsupported type {type(value).__name__}")


def canonical(value: Any) -> bytes:
    """C(value): sorted keys, ASCII escapes, no whitespace, no NaN."""
    check_domain(value)
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def objective_key(namespace: str, objective_id: str) -> str:
    return sha256_hex(canonical([namespace, objective_id]))


def snapshot_id(pairs: Iterable[tuple[str, str | None]]) -> str:
    """Hash of the sorted [objective_key, trail_sha256_or_null] pairs."""
    rows = sorted([key, trail] for key, trail in pairs)
    return sha256_hex(canonical(rows))


def sort_key(semantic_key: Any) -> bytes:
    """Set-like arrays sort by the canonical encoding of their semantic key."""
    return canonical(semantic_key)
