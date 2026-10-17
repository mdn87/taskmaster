"""The delivered artifact, rather than a rerender, is the report contract."""

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taskmaster import morning_report as helper

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/batch2/agreement/export.json"
NOW = "2026-10-17T12:00:00Z"
BODY = b"Inbox empty.\n"


class MorningReport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "delivered.md"

    def test_unconfigured_is_identical_and_does_not_launch(self):
        with patch.object(helper, "render_once", side_effect=AssertionError("must not launch")):
            prepared = helper.prepare(None, NOW)
            helper.deliver(BODY, self.output, prepared)
        self.assertEqual(self.output.read_bytes(), BODY)
        # An unconfigured integration must not interpret ordinary inbox prose.
        quoted_markers = BODY + helper.BEGIN + helper.END
        helper.deliver(quoted_markers, self.output, prepared)
        self.assertEqual(self.output.read_bytes(), quoted_markers)

    def test_configured_empty_inbox_preserves_exact_golden_and_digest(self):
        prepared = helper.prepare(FIXTURE, NOW)
        golden = (FIXTURE.parent / "report.txt").read_bytes()
        self.assertEqual(prepared.block, golden)
        self.assertTrue(golden.endswith(b"\n") and not golden.endswith(b"\n\n"))
        helper.deliver(BODY, self.output, prepared)
        data = self.output.read_bytes()
        self.assertTrue(helper.verify_document(data, prepared))
        self.assertIn(hashlib.sha256(golden).hexdigest().encode(), data)
        self.assertTrue(data.endswith(BODY))
        self.assertEqual(data.count(helper.BEGIN), 1)

    def test_refresh_race_never_rerenders_or_rereads_export(self):
        source = self.output.with_name("export.json")
        source.write_bytes(FIXTURE.read_bytes())
        prepared = helper.prepare(source, NOW)
        source.write_bytes(b"invalid replacement")
        with patch.object(helper, "render_once", side_effect=AssertionError("no rerender")):
            helper.deliver(BODY, self.output, prepared)
        self.assertTrue(helper.verify_document(self.output.read_bytes(), prepared))

    def test_bad_blocks_fail_and_final_artifact_has_only_typed_line(self):
        prepared = helper.prepare(FIXTURE, NOW)
        valid = helper.compose(BODY, prepared)
        variants = [valid.replace(b"objectives: 12", b"objectives: 99"), BODY,
                    valid + valid, valid.replace(helper.END, b"broken"),
                    valid.replace(b"\n", b"\r\n")]
        original_write = Path.write_bytes
        for corrupted in variants:
            with self.subTest(corrupted=corrupted[:40]):
                self.assertFalse(helper.verify_document(corrupted, prepared))
                writes = []
                def corrupt_first(path, data):
                    writes.append(data)
                    return original_write(path, corrupted if len(writes) == 1 else data)
                with patch.object(Path, "write_bytes", corrupt_first):
                    helper.deliver(BODY, self.output, prepared)
                delivered = self.output.read_bytes()
                self.assertEqual(delivered, b"Taskmaster: report_verification_failed\n" + BODY)
                self.assertNotIn(helper.BEGIN, delivered)

    def test_renderer_failures_are_bounded_and_inbox_body_survives(self):
        for code in (3, 4, 8, 91):
            with self.subTest(code=code):
                with patch.object(helper, "render_once", return_value=(code, b"secret details")):
                    prepared = helper.prepare(FIXTURE, NOW)
                helper.deliver(BODY, self.output, prepared)
                data = self.output.read_bytes()
                self.assertTrue(data.startswith(b"Taskmaster: "))
                self.assertTrue(data.endswith(BODY))
                self.assertNotIn(b"secret", data)
                self.assertNotIn(helper.BEGIN, data)

    def test_launch_and_timeout_failures_are_status_lines(self):
        for exc in (FileNotFoundError("private path"), subprocess.TimeoutExpired("private", 1)):
            with patch.object(helper, "render_once", side_effect=exc):
                prepared = helper.prepare(FIXTURE, NOW)
            self.assertIsNone(prepared.block)
            self.assertIn(prepared.error, ("renderer_unavailable", "renderer_timeout"))

    def test_missing_invalid_unsupported_and_unreadable_exports(self):
        source = self.output.with_name("export.json")
        for content in (None, b"bad", b'{"schema":"taskmaster.export/v2","schema_version":2}'):
            if content is not None:
                source.write_bytes(content)
            prepared = helper.prepare(source, NOW)
            self.assertIsNone(prepared.block)
            self.assertIsNotNone(prepared.error)
        prepared = helper.prepare(Path(self.temp.name), NOW)
        self.assertEqual(prepared.error, "export_unreadable")

    def test_cli_binary_report(self):
        body = self.output.with_name("body.md")
        body.write_bytes(BODY)
        proc = subprocess.run([sys.executable, "-m", "taskmaster.morning_report",
                               "--export", str(FIXTURE), "--now", NOW,
                               "--body-file", str(body), "--output", str(self.output)],
                              capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(b"\r", self.output.read_bytes())
        self.assertIn(b"objectives: 12", self.output.read_bytes())
