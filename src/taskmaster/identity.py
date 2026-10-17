"""Identity, token and label validation plus display escaping (plan section 2)."""

from __future__ import annotations

import re
import unicodedata

MAX_ID_BYTES = 512
MAX_TOKEN_BYTES = 128
MAX_LABEL_BYTES = 160
MAX_POINTER_BYTES = 1280

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]*\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class IdentityError(ValueError):
    def __init__(self, what: str, reason: str):
        super().__init__(f"{what}: {reason}")
        self.what = what
        self.reason = reason


def _scalar_text(value: object, what: str, max_bytes: int) -> str:
    if not isinstance(value, str):
        raise IdentityError(what, "not_a_string")
    if value == "":
        raise IdentityError(what, "empty")
    for ch in value:
        category = unicodedata.category(ch)
        if category == "Cs":
            raise IdentityError(what, "unpaired_surrogate")
        if category == "Cc":
            raise IdentityError(what, "control_character")
    if len(value.encode("utf-8")) > max_bytes:
        raise IdentityError(what, "too_long")
    return value


def validate_id(value: object, what: str = "id") -> str:
    """An identity is kept exactly as decoded: no trimming or normalization."""
    return _scalar_text(value, what, MAX_ID_BYTES)


def validate_label(value: object, what: str = "label") -> str:
    return _scalar_text(value, what, MAX_LABEL_BYTES)


def validate_token(value: object, what: str = "token") -> str:
    if not isinstance(value, str) or not _TOKEN.match(value):
        raise IdentityError(what, "invalid_token")
    if len(value) > MAX_TOKEN_BYTES:
        raise IdentityError(what, "too_long")
    return value


def is_sha256_hex(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX64.match(value))


def validate_pointer(value: object, what: str = "json_pointer") -> str:
    if not isinstance(value, str):
        raise IdentityError(what, "not_a_string")
    if value and not value.startswith("/"):
        raise IdentityError(what, "invalid_pointer")
    if len(value.encode("utf-8")) > MAX_POINTER_BYTES:
        raise IdentityError(what, "too_long")
    if any(unicodedata.category(ch) in ("Cc", "Cs") for ch in value):
        raise IdentityError(what, "control_character")
    return value


def display(value: str) -> str:
    """Show format, line and paragraph separators as visible escapes.

    The identity itself is unchanged; this is only for human-facing text.
    """
    out = []
    for ch in value:
        if unicodedata.category(ch) in ("Cf", "Zl", "Zp"):
            code = ord(ch)
            out.append(f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}")
        elif ch == "\\":
            out.append("\\\\")
        else:
            out.append(ch)
    return "".join(out)
