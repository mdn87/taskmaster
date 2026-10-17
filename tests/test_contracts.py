"""Canonical/hash vectors, strict parsing, identity and time contracts."""

import hashlib
import unittest

from taskmaster import timefmt
from taskmaster.canonical import MAX_SAFE_INT, CanonicalError, canonical, objective_key, snapshot_id
from taskmaster.identity import IdentityError, display, validate_id, validate_label, validate_token
from taskmaster.strictjson import StrictJSONError, loads


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CanonicalVectors(unittest.TestCase):
    def test_key_order_and_separators(self):
        self.assertEqual(canonical({"b": 1, "a": [], "c": None}), b'{"a":[],"b":1,"c":null}')

    def test_escapes(self):
        cases = {
            "\u007f": b'"\\u007f"',            # DEL
            "\u0085": b'"\\u0085"',            # C1 control
            "\U0001F600": b'"\\ud83d\\ude00"', # astral -> surrogate pair escape
            'a"b\\c': b'"a\\"b\\\\c"',
            "\n": b'"\\n"',
            "é": b'"\\u00e9"',
        }
        for value, expected in cases.items():
            with self.subTest(value=repr(value)):
                self.assertEqual(canonical(value), expected)

    def test_domain_rejections(self):
        for bad in (1.5, float("nan"), MAX_SAFE_INT + 1, -MAX_SAFE_INT - 1, "\ud800", {1: "x"}):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(CanonicalError):
                    canonical(bad)
        self.assertEqual(canonical([MAX_SAFE_INT, -MAX_SAFE_INT]), b"[9007199254740991,-9007199254740991]")

    def test_depth_limit(self):
        value = []
        for _ in range(70):
            value = [value]
        with self.assertRaises(CanonicalError):
            canonical(value)

    def test_hash_vectors(self):
        self.assertEqual(objective_key("ops", "obj-1"), sha(b'["ops","obj-1"]'))
        self.assertEqual(snapshot_id([]), sha(b"[]"))
        self.assertEqual(snapshot_id([("b" * 64, None), ("a" * 64, "c" * 64)]),
                         sha(('[["' + "a" * 64 + '","' + "c" * 64 + '"],["' + "b" * 64 + '",null]]').encode()))


class StrictParsing(unittest.TestCase):
    def reason(self, data: bytes, **kw) -> str:
        with self.assertRaises(StrictJSONError) as ctx:
            loads(data, max_bytes=kw.pop("max_bytes", 1024), **kw)
        return ctx.exception.reason

    def test_rejections(self):
        self.assertEqual(self.reason(b'{"a":1,"a":2}'), "duplicate_key")
        self.assertEqual(self.reason(b"-0"), "negative_zero")
        self.assertEqual(self.reason(b"9007199254740992"), "integer_out_of_range")
        self.assertEqual(self.reason(b"1.5"), "non_integer_number")
        self.assertEqual(self.reason(b"NaN"), "non_finite_number")
        self.assertEqual(self.reason(b'"\\ud800"'), "unpaired_surrogate")
        self.assertEqual(self.reason(b"[" * 40 + b"]" * 40), "nesting_too_deep")
        self.assertEqual(self.reason(b"\xef\xbb\xbf{}"), "byte_order_mark")
        self.assertEqual(self.reason(b'"\xff"'), "invalid_utf8")
        self.assertEqual(self.reason(b"[1,2]", max_bytes=3), "too_large")
        self.assertEqual(self.reason(b"{"), "malformed_json")

    def test_owner_floats(self):
        self.assertEqual(loads(b"[1.5]", max_bytes=10, allow_floats=True), [1.5])
        self.assertEqual(self.reason(b"[1e999]", allow_floats=True), "non_finite_number")

    def test_valid(self):
        self.assertEqual(loads(b'{"a":[1,"\\u00e9",null,true]}', max_bytes=100), {"a": [1, "é", None, True]})


class Identity(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(validate_id("run 1/é"), "run 1/é")
        for bad in ("", "a\x00", "a\u0085", "\ud800", "x" * 513, 5):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(IdentityError):
                    validate_id(bad)
        with self.assertRaises(IdentityError):
            validate_label("x" * 161)

    def test_tokens(self):
        self.assertEqual(validate_token("claude:sonnet-medium"), "claude:sonnet-medium")
        for bad in ("-lead", "has space", "", "é"):
            with self.subTest(bad=bad):
                with self.assertRaises(IdentityError):
                    validate_token(bad)

    def test_display_escapes_invisible_and_separators(self):
        self.assertEqual(display("a​b c\\d"), "a\\u200bb\\u2028c\\\\d")
        self.assertEqual(display("plain é"), "plain é")


class Times(unittest.TestCase):
    def test_strict_format(self):
        self.assertTrue(timefmt.is_valid("2026-10-17T12:00:00Z"))
        for bad in ("2026-10-17T12:00:00", "2026-10-17 12:00:00Z", "2026-13-01T00:00:00Z", "2026-10-17T12:00:00.5Z", 5):
            with self.subTest(bad=bad):
                self.assertFalse(timefmt.is_valid(bad))
        self.assertEqual(timefmt.seconds_between("2026-10-17T12:00:00Z", "2026-10-17T13:00:01Z"), 3601)


if __name__ == "__main__":
    unittest.main()
