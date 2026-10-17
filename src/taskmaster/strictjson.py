"""Strict JSON reader shared by every Python surface (plan section 3).

Rejects duplicate keys, invalid UTF-8, a BOM, unpaired surrogates, NaN and
Infinity, fractions, exponents, -0 and integers outside the interoperable
range. Owner output may carry floats; `allow_floats=True` admits them so the
adapter can ignore them, and no float is ever copied into trail content.
"""

from __future__ import annotations

import json
from typing import Any

from .canonical import MAX_SAFE_INT

MAX_DEPTH = 32


class StrictJSONError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise StrictJSONError("duplicate_key")
        out[key] = value
    return out


def _parse_int(lexeme: str) -> int:
    if lexeme == "-0":
        raise StrictJSONError("negative_zero")
    value = int(lexeme)
    if not -MAX_SAFE_INT <= value <= MAX_SAFE_INT:
        raise StrictJSONError("integer_out_of_range")
    return value


def _reject_float(lexeme: str) -> float:
    raise StrictJSONError("non_integer_number")


def _reject_constant(lexeme: str) -> Any:
    raise StrictJSONError("non_finite_number")


def _owner_float(lexeme: str) -> float:
    value = float(lexeme)
    if value != value or value in (float("inf"), float("-inf")):
        raise StrictJSONError("non_finite_number")
    return value


def _walk(value: Any, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise StrictJSONError("nesting_too_deep")
    if isinstance(value, str):
        _check_scalars(value)
    elif isinstance(value, list):
        for item in value:
            _walk(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_scalars(key)
            _walk(item, depth + 1)


def _check_scalars(s: str) -> None:
    for ch in s:
        if 0xD800 <= ord(ch) <= 0xDFFF:
            raise StrictJSONError("unpaired_surrogate")


def loads(data: bytes, *, max_bytes: int, allow_floats: bool = False) -> Any:
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("strict JSON input must be bytes")
    if len(data) > max_bytes:
        raise StrictJSONError("too_large")
    if data[:3] == b"\xef\xbb\xbf":
        raise StrictJSONError("byte_order_mark")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        raise StrictJSONError("invalid_utf8") from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_int=_parse_int,
            parse_float=_owner_float if allow_floats else _reject_float,
            parse_constant=_reject_constant,
        )
    except StrictJSONError:
        raise
    except RecursionError:
        raise StrictJSONError("nesting_too_deep") from None
    except ValueError:
        raise StrictJSONError("malformed_json") from None
    _walk(value, 0)
    return value
