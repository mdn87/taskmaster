import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from taskmaster import apu_contract, apu_transport, publication, remote_context


SUMMARY = {
    "schema": "apu.behavior-review/v1", "workspace": "dias",
    "attempted_at": "2026-11-20T12:00:00Z", "last_success_at": "2026-11-20T12:00:00Z",
    "schedule_date": "2026-11-20", "run_status": "ok",
    "coverage": {"codex": 1, "claude-code": 0, "skipped": {}, "unknown": 0, "overflow": 0},
    "actions": [{"kind": "manual-handoff", "ref": "ar-" + "e" * 32, "source": "verified"}],
}
REQUEST = {"capture": "apu-review/v1"}


def failure(status):
    return {"schema": "taskmaster.apu-observation/v1", "status": status}


# This fixture exercises the same two subprocess invocations as an SSH capture.
# Ordinary context and the optional secondary request can fail independently.
CAPTURE = """
import json, pathlib, sys
from datetime import datetime, timezone
root = pathlib.Path(sys.argv[1])
request = json.load(sys.stdin)
with (root / 'calls.jsonl').open('a', encoding='utf-8') as stream:
    print(json.dumps(request), file=stream)
if 'capture' in request:
    if (root / 'fail-apu').exists():
        sys.exit(2)
    if (root / 'hang-apu').exists():
        import time
        time.sleep(60)
    sys.stdout.buffer.write((root / 'reply').read_bytes())
else:
    if (root / 'fail-primary').exists():
        sys.exit(1)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    print(json.dumps({'schema': 'taskmaster.remote-context/v1', 'observed_at': now,
                      'max_age_seconds': 45, 'timezone': 'UTC', 'dias_status': 'unavailable',
                      'context': None, 'notes': {}}))
"""


class ApuTransportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.private = self.root / "state" / "behavior" / "review" / "summary.json"
        self.private.parent.mkdir(parents=True)
        self.mirror = self.root / "export" / "apu-review.json"
        self.cfg = {
            "destination": str(self.root / "context" / "observation.json"), "timezone": "UTC",
            "dias_url": "http://127.0.0.1:4175", "notes": {},
            "command": [sys.executable, "-c", CAPTURE, str(self.root)],
        }
        self.config_path = self.root / "collect.json"
        self.private.write_text(json.dumps(SUMMARY), encoding="utf-8")
        (self.root / "reply").write_bytes(self.private.read_bytes())

    def collect(self):
        raw = json.dumps(self.cfg).encode()
        if not self.config_path.exists() or self.config_path.read_bytes() != raw:
            self.config_path.write_bytes(raw)
        # Primary workstation context is normally validated on the Linux owner.
        with patch.object(remote_context, "ZoneInfo", return_value=None):
            return remote_context.collect(self.config_path)

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]

    def enable(self):
        self.cfg["apu_review"] = {"output": str(self.mirror), "interval_seconds": 300}

    def test_capture_reads_only_the_fixed_summary(self):
        with patch.dict(os.environ, {"APU_HOME": str(self.root / "state")}):
            self.assertEqual(remote_context.capture(REQUEST), SUMMARY)
            self.assertEqual(remote_context.capture({**REQUEST, "path": str(self.root / "secret")}), failure("invalid"))
            self.private.write_text(json.dumps(failure("disabled")), encoding="utf-8")
            self.assertEqual(remote_context.capture(REQUEST), failure("invalid"))
            self.private.unlink()
            self.assertEqual(remote_context.capture(REQUEST), failure("missing"))
            self.private.mkdir()
            self.assertEqual(remote_context.capture(REQUEST), failure("invalid"))
        with patch.dict(os.environ, {"APU_HOME": "relative"}):
            self.assertEqual(remote_context.capture(REQUEST), failure("unavailable"))

    def test_real_capture_command_checks_request_size_and_emits_only_json(self):
        command = [sys.executable, "-m", "taskmaster.remote_context", "capture"]
        env = {**os.environ, "APU_HOME": str(self.root / "state")}
        req = json.dumps(REQUEST).encode()
        for supplied, expected in ((req, SUMMARY), (req.ljust(apu_contract.MAX_BYTES + 1), failure("invalid")),
                                   (json.dumps({**REQUEST, "notes": {}}).encode(), failure("invalid"))):
            result = subprocess.run(command, input=supplied, capture_output=True, env=env, check=True)
            self.assertEqual(json.loads(result.stdout), expected)
            self.assertLessEqual(len(result.stdout), apu_contract.MAX_BYTES)
            self.assertEqual(result.stderr, b"")

    def test_raw_bytes_checked_on_both_sides(self):
        raw = self.private.read_bytes()
        exact = raw.ljust(apu_contract.MAX_BYTES, b" ")
        duplicate = raw.replace(b'"workspace": "dias"', b'"workspace": "leak", "workspace": "dias"')
        inputs = [(exact, SUMMARY), (exact + b" ", failure("invalid")), (duplicate, failure("invalid")),
                  (json.dumps({**SUMMARY, "transcript": "private text"}).encode(), failure("invalid"))]
        with patch.dict(os.environ, {"APU_HOME": str(self.root / "state")}):
            for supplied, expected in inputs:
                with self.subTest(size=len(supplied)):
                    self.private.write_bytes(supplied)
                    self.assertEqual(remote_context.capture(REQUEST), expected)
                    self.assertEqual(apu_transport.parse_wire(supplied), expected)
        for status in ("missing", "invalid", "unavailable", "disabled"):
            self.assertEqual(apu_transport.parse_wire(json.dumps(failure(status)).encode()), failure(status))
        for doc in (failure("contains a session id"), {**failure("missing"), "path": "private"}):
            self.assertEqual(apu_transport.parse_wire(json.dumps(doc).encode()), failure("invalid"))
        underflow = {**SUMMARY, "attempted_at": "0001-01-01T00:00:00Z"}
        self.assertEqual(apu_transport.parse_wire(json.dumps(underflow).encode()), failure("invalid"))
        with patch.object(apu_contract, "ZoneInfo", side_effect=ZoneInfoNotFoundError("local detail")):
            self.assertEqual(apu_transport.parse_wire(raw), failure("unavailable"))

    def test_relative_source_override_and_windows_default(self):
        env = {"LOCALAPPDATA": str(self.root)}
        with patch.dict(os.environ, env, clear=True), patch.object(sys, "platform", "win32"):
            required = self.root / "apu" / "behavior" / "review" / "summary.json"
            required.parent.mkdir(parents=True)
            required.write_bytes(self.private.read_bytes())
            self.assertEqual(remote_context.capture(REQUEST), SUMMARY)
        with patch.dict(os.environ, {"LOCALAPPDATA": "relative"}, clear=True), patch.object(sys, "platform", "win32"):
            self.assertEqual(remote_context.capture(REQUEST), failure("unavailable"))

    def test_optional_collection_is_independent_and_catches_up(self):
        self.assertTrue(self.collect()["ok"])
        self.assertFalse(self.mirror.exists())
        self.assertEqual(len(self.calls()), 1)
        self.enable()
        self.assertTrue(self.collect()["ok"])
        self.assertEqual(json.loads(self.mirror.read_bytes()), SUMMARY)
        self.assertEqual(self.calls()[-1], REQUEST)
        self.assertTrue(self.collect()["ok"])
        self.assertEqual(self.calls().count(REQUEST), 1)
        # Catch up after an outage, including a backwards host-clock correction.
        for offset in (-400, 400):
            timestamp = time.time() + offset
            os.utime(self.mirror, (timestamp, timestamp))
            self.assertTrue(self.collect()["ok"])
        self.assertEqual(self.calls().count(REQUEST), 3)
        snapshot = json.loads(Path(self.cfg["destination"]).read_bytes())
        self.assertEqual(snapshot["schema"], remote_context.V1)
        self.assertEqual(set(snapshot), remote_context.KEYS)

    def test_optional_failures_cannot_poison_primary_and_reverse(self):
        self.enable()
        (self.root / "reply").write_bytes(b'{"secret":"private/rollout.jsonl"}' + b" " * apu_contract.MAX_BYTES)
        self.assertTrue(self.collect()["ok"])
        self.assertEqual(json.loads(self.mirror.read_bytes()), failure("invalid"))
        (self.root / "fail-apu").touch()
        os.utime(self.mirror, (1, 1))
        self.assertTrue(self.collect()["ok"])
        self.assertEqual(json.loads(self.mirror.read_bytes()), failure("unavailable"))
        (self.root / "fail-apu").unlink()
        (self.root / "fail-primary").touch()
        (self.root / "reply").write_bytes(self.private.read_bytes())
        os.utime(self.mirror, (1, 1))
        self.assertFalse(self.collect()["ok"])
        self.assertEqual(json.loads(self.mirror.read_bytes()), SUMMARY)

    def test_sidecar_timeout_and_competing_writer_leave_primary_usable(self):
        self.enable()
        self.mirror.parent.mkdir()
        with publication.writer_lock(self.mirror):
            self.assertTrue(self.collect()["ok"])
            self.assertFalse(self.mirror.exists())
            self.assertNotIn(REQUEST, self.calls())
        (self.root / "hang-apu").touch()
        with patch.object(apu_transport, "CAPTURE_TIMEOUT", .5):
            self.assertTrue(self.collect()["ok"])
        self.assertEqual(apu_transport.read_sidecar(self.mirror), failure("unavailable"))
        self.assertEqual(json.loads(Path(self.cfg["destination"]).read_bytes())["schema"], remote_context.V1)

    def test_configuration_never_writes_arbitrary_paths_or_breaks_primary(self):
        self.enable()
        other = self.root / "other.json"
        self.cfg["apu_review"]["output"] = str(other)
        self.assertTrue(self.collect()["ok"])
        self.assertFalse(other.exists())
        self.cfg["apu_review"]["output"] = str(self.mirror)
        for interval in (True, 0, 86401, "300"):
            with self.subTest(interval=interval):
                self.cfg["apu_review"]["interval_seconds"] = interval
                self.assertTrue(self.collect()["ok"])
                self.assertEqual(json.loads(self.mirror.read_bytes()), failure("invalid"))
        self.assertNotIn(REQUEST, self.calls())
        self.cfg["destination"] = str(self.mirror)
        self.enable()
        self.assertTrue(self.collect()["ok"])
        self.assertNotIn(REQUEST, self.calls())
        self.assertEqual(json.loads(self.mirror.read_bytes())["schema"], remote_context.V1)

    def test_explicit_disable_clears_old_summary_and_enabling_catches_up(self):
        self.enable()
        self.assertTrue(self.collect()["ok"])
        self.cfg["apu_review"]["enabled"] = False
        self.assertTrue(self.collect()["ok"])
        self.assertEqual(json.loads(self.mirror.read_bytes()), failure("disabled"))
        self.assertEqual(self.calls().count(REQUEST), 1)
        self.cfg["apu_review"]["enabled"] = True
        self.assertTrue(self.collect()["ok"])
        self.assertEqual(json.loads(self.mirror.read_bytes()), SUMMARY)
        self.assertEqual(self.calls().count(REQUEST), 2)

    def test_disabled_peer_observation_respects_refresh_interval(self):
        self.enable()
        (self.root / "reply").write_bytes(json.dumps(failure("disabled")).encode())
        self.assertTrue(self.collect()["ok"])
        self.assertTrue(self.collect()["ok"])
        self.assertEqual(self.calls().count(REQUEST), 1)
        self.assertEqual(apu_transport.read_sidecar(self.mirror), failure("disabled"))

    def test_mirror_reader_distinguishes_no_install_and_observed_missing(self):
        self.assertEqual(apu_transport.read_sidecar(self.mirror), failure("missing"))
        self.assertEqual(apu_transport.read_sidecar(self.mirror, absent="disabled"), failure("disabled"))
        apu_transport.write_sidecar(self.mirror, failure("missing"))
        self.assertEqual(apu_transport.read_sidecar(self.mirror, absent="disabled"), failure("missing"))
        self.mirror.write_bytes(b"broken payload")
        self.assertEqual(apu_transport.read_sidecar(self.mirror), failure("invalid"))

    def test_optional_bundle_field_roundtrips_and_contains_failures(self):
        def read_field(path):
            field = apu_transport.bundle_field(path)
            self.assertIsInstance(field, str)
            raw = base64.b64decode(field, validate=True)
            self.assertLessEqual(len(raw), apu_contract.MAX_BYTES)
            return apu_transport.parse_bundle_field(field)

        self.assertEqual(read_field(self.mirror), failure("disabled"))
        self.assertEqual(read_field(self.private), failure("invalid"))
        apu_transport.write_sidecar(self.mirror, SUMMARY)
        self.assertEqual(read_field(self.mirror), SUMMARY)
        self.mirror.write_bytes(b'{"private":"must never leave local storage"}')
        self.assertEqual(read_field(self.mirror), failure("invalid"))
        for value in (None, True, [], b"bytes", "", "?", "\u2603", "ab==", "e30="):
            self.assertEqual(apu_transport.parse_bundle_field(value), failure("invalid"))
        raw = json.dumps(SUMMARY).encode().ljust(apu_contract.MAX_BYTES)
        exact = base64.b64encode(raw).decode("ascii")
        too_large = base64.b64encode(raw + b" ").decode("ascii")
        self.assertEqual(len(exact), len(too_large))
        self.assertEqual(apu_transport.parse_bundle_field(exact), SUMMARY)
        self.assertEqual(apu_transport.parse_bundle_field(too_large), failure("invalid"))
        with patch.object(base64, "b64decode", side_effect=AssertionError("unbounded decode")):
            self.assertEqual(apu_transport.parse_bundle_field(exact + "abcd"), failure("invalid"))

    def test_mirror_cli_validates_and_replaces_previous_data(self):
        base = [sys.executable, "-m", "taskmaster.apu_transport"]
        def command(mode, raw=b""):
            result = subprocess.run([*base, mode, "--file", str(self.mirror)], input=raw,
                                    capture_output=True, check=True)
            self.assertEqual(result.stderr, b"")
            return json.loads(result.stdout)
        self.assertEqual(command("read"), failure("missing"))
        self.assertEqual(command("write", self.private.read_bytes()), SUMMARY)
        self.assertEqual(command("read"), SUMMARY)
        self.assertEqual(command("write", b"private path or user text"), failure("invalid"))
        self.assertEqual(command("read"), failure("invalid"))
        wrong = subprocess.run([*base, "read", "--file", str(self.private)], capture_output=True, check=False)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertEqual(json.loads(wrong.stdout), failure("invalid"))
        self.assertEqual(wrong.stderr, b"")
