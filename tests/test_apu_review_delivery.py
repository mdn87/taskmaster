import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from taskmaster import morning_report


class NightlyReviewDelivery(unittest.TestCase):
    def test_only_verified_file_consumes_ref_and_same_day_reuses_it(self):
        from taskmaster.apu_review import publish
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root / "export.json"
            report = root / "report.md"
            ref = "ar-" + "a" * 32
            summary = {"schema":"apu.behavior-review/v1","workspace":"dias",
                       "attempted_at":"2026-11-20T08:15:00Z","last_success_at":"2026-11-20T08:15:00Z",
                       "schedule_date":"2026-11-20","run_status":"ok",
                       "coverage":{"codex":1,"claude-code":0,"skipped":{},"unknown":0,"overflow":0},
                       "actions":[{"kind":"manual-handoff","ref":ref,"source":"verified"}]}
            publish(root / "apu-review.json", summary)
            body = root / "body.md"
            body.write_bytes(b"inbox body\n")
            command = ["--export",str(export),"--body-file",str(body),"--output",str(report),"--now"]
            with patch.object(morning_report,"render_once",return_value=(0,b"taskmaster trail report v1\n")):
                prepared = morning_report.prepare(export,"2026-11-20T12:00:00Z")
                self.assertIn(ref.encode(),prepared.block)
                self.assertFalse((root / "apu-deliveries.json").exists())
                self.assertEqual(morning_report.main(command+["2026-11-20T12:00:00Z"]),0)
                first = report.read_bytes()
                self.assertIn(ref.encode(),first)
                self.assertEqual(morning_report.main(command+["2026-11-20T16:00:00Z"]),0)
                self.assertEqual(report.read_bytes(),first)
                receipts=json.loads((root/"apu-deliveries.json").read_text())
                self.assertIn(ref,receipts["shown"])
                summary.update(attempted_at="2026-11-21T08:15:00Z",last_success_at="2026-11-21T08:15:00Z",
                               schedule_date="2026-11-21")
                publish(root/"apu-review.json",summary)
                self.assertEqual(morning_report.main(command+["2026-11-21T12:00:00Z"]),0)
                self.assertNotIn(ref.encode(),report.read_bytes())
                self.assertTrue(report.read_bytes().endswith(body.read_bytes()))



class ReviewReportCases(unittest.TestCase):
    now = "2026-11-20T12:00:00Z"
    tomorrow = "2026-11-21T12:00:00Z"
    base = b"taskmaster trail report v1\n"
    handoff = {"kind": "manual-handoff", "ref": "ar-" + "a" * 32, "source": "verified"}
    other = {"kind": "manual-handoff", "ref": "ar-" + "b" * 32, "source": "verified"}
    window = {"kind": "monitoring-window", "ref": "ar-" + "c" * 32, "source": "recorded"}
    evaluation = {"kind": "pilot-evaluation", "ref": "ar-" + "d" * 32, "source": "recorded"}

    def setUp(self):
        from taskmaster import apu_review
        self.apu = apu_review
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.export = self.root / "export.json"
        self.output = self.root / "report.md"
        self.sidecar = self.root / "apu-review.json"
        self.receipts = self.root / "apu-deliveries.json"
        self.body = b"Ordinary inbox item\r\n"
        renderer = patch.object(morning_report, "render_once", return_value=(0, self.base))
        self.renderer = renderer.start()
        self.addCleanup(renderer.stop)

    def summary(self, *actions, day="2026-11-20", cover=None):
        stamp = day + "T08:15:00Z"
        value = {"schema": "apu.behavior-review/v1", "workspace": "dias",
                 "attempted_at": stamp, "last_success_at": stamp, "schedule_date": day,
                 "run_status": "ok", "actions": list(actions),
                 "coverage": {"codex": 1, "claude-code": 0, "skipped": {}, "unknown": 0, "overflow": 0}}
        if cover:
            value["coverage"].update(cover)
        self.apu.publish(self.sidecar, value)
        return value

    def preview(self, now=None):
        return morning_report.prepare(self.export, now or self.now)

    def deliver(self, now=None, output=None):
        return morning_report.deliver(self.body, output or self.output, self.preview(now))

    def shown(self):
        return json.loads(self.receipts.read_bytes())["shown"]

    def test_quiet_full_audit_and_disabled_are_identical_to_ordinary_report(self):
        baseline = morning_report.compose(self.body, self.preview())
        for summary in (self.summary(), self.summary(cover={"unknown": 1}),
                        self.summary(cover={"skipped": {"no-completed-turn": 2}}),
                        {"schema": "taskmaster.apu-observation/v1", "status": "disabled"}):
            with self.subTest(summary=summary):
                self.apu.publish(self.sidecar, summary)
                self.assertTrue(self.deliver())
                self.assertEqual(self.output.read_bytes(), baseline)
                self.assertFalse(self.receipts.exists())

    def test_coverage_gaps_stale_and_failed_have_one_line_and_no_unverified_action(self):
        for cover, phrase in (({"codex": 0, "skipped": {"no-completed-turn": 1}}, b"No eligible sessions"),
                              ({"skipped": {"session-limit": 2}}, b"cap"),
                              ({"overflow": 2}, b"cap"),
                              ({"skipped": {"source-ingestion-failed": 1}}, b"partial")):
            with self.subTest(cover=cover):
                self.summary(cover=cover)
                block = self.preview().block
                self.assertEqual(block.count(b"APU review"), 1)
                self.assertIn(phrase.lower(), block.lower())
                self.assertNotIn(b"ar-", block)

        value = self.summary(day="2026-11-19")
        self.assertEqual(self.preview("2026-11-20T09:14:59Z").block, self.base)
        self.assertIn(b"was missed", self.preview("2026-11-20T09:15:00Z").block)
        self.assertIn(b"over 30 hours", self.preview("2026-11-20T14:15:01Z").block)
        value.update(run_status="failed", attempted_at="2026-11-20T08:16:00Z", schedule_date="2026-11-20")
        self.apu.publish(self.sidecar, value)
        block = self.preview().block
        self.assertIn(b"audit failed", block)
        self.assertIn(value["last_success_at"].encode(), block)
        self.assertNotIn(b"ar-", block)

    def test_summer_time_grace_is_one_hour_after_local_due_time(self):
        self.summary(day="2026-07-03")
        self.assertEqual(self.preview("2026-07-04T08:14:59Z").block, self.base)
        self.assertIn(b"was missed", self.preview("2026-07-04T08:15:00Z").block)

    def test_priority_stable_refs_and_qualified_action_on_one_line(self):
        self.summary(self.window, self.other, self.evaluation, self.handoff, cover={"skipped": {"source-changed": 1}})
        self.assertTrue(self.deliver())
        actual = self.output.read_bytes()
        self.assertEqual(actual.count(b"APU review"), 1)
        self.assertIn(self.handoff["ref"].encode(), actual)
        self.assertIn(b"partial", actual.lower())
        value = self.summary(self.window, self.evaluation, self.handoff, day="2026-11-21")
        self.assertTrue(self.deliver(self.tomorrow))
        self.assertIn(b"apu behavior review evaluate", self.output.read_bytes())
        self.assertNotIn(self.handoff["ref"].encode(), self.output.read_bytes())
        self.summary(*value["actions"], day="2026-11-22")
        self.assertTrue(self.deliver("2026-11-22T12:00:00Z"))
        self.assertIn(self.window["ref"].encode(), self.output.read_bytes())
        self.summary(*value["actions"], day="2026-11-23")
        self.assertEqual(self.preview("2026-11-23T12:00:00Z").block, self.base)

    def test_invalid_input_and_explicit_transport_health_do_not_expose_it_or_hide_inbox(self):
        for raw, phrase in ((b'{"private":"evidence"}', b"invalid"),
                            (b"x" * 8193, b"invalid"),
                            (b'{"schema":"taskmaster.apu-observation/v1","status":"missing"}', b"missing"),
                            (b'{"schema":"taskmaster.apu-observation/v1","status":"unavailable"}', b"unavailable")):
            with self.subTest(raw=raw[:40]):
                self.sidecar.write_bytes(raw)
                self.assertTrue(self.deliver())
                actual = self.output.read_bytes()
                self.assertIn(phrase, actual)
                self.assertIn(self.base, actual)
                self.assertTrue(actual.endswith(self.body))
                self.assertNotIn(b"private", actual)
                self.assertFalse(self.receipts.exists())

        value = self.summary(self.handoff, day="2026-11-21")
        self.assertIn(b"invalid", self.preview().block)
        self.receipts.write_bytes(b'{"private":"bad delivery state"}')
        self.assertTrue(self.deliver(self.tomorrow))
        self.assertIn(b"Delivery history is unavailable", self.output.read_bytes())
        self.assertNotIn(value["actions"][0]["ref"].encode(), self.output.read_bytes())
        self.assertTrue(self.output.read_bytes().endswith(self.body))

    def test_relative_export_still_finds_the_optional_file(self):
        from contextlib import chdir
        # The regular report CLI accepts a relative export path.
        self.summary(self.handoff)
        with chdir(self.root):
            actual = morning_report.prepare(Path("export.json"), self.now).block
        self.assertIn(self.handoff["ref"].encode(), actual)

    def test_failed_write_and_failed_readback_leave_the_ref_eligible(self):
        self.summary(self.handoff)
        with patch.object(Path, "write_bytes", side_effect=PermissionError("cannot write the report")):
            with self.assertRaises(OSError):
                self.deliver()
        self.assertFalse(self.receipts.exists())
        self.assertIn(self.handoff["ref"].encode(), self.preview().block)
        with patch.object(morning_report, "verify_document", return_value=False):
            self.assertFalse(self.deliver())
        self.assertEqual(self.output.read_bytes(), b"Taskmaster: report_verification_failed\n" + self.body)
        self.assertFalse(self.receipts.exists())
        self.assertTrue(self.deliver())
        self.assertIn(self.handoff["ref"], self.shown())

    def test_crash_after_output_before_receipt_recovers_a_different_day_and_output(self):
        import hashlib
        self.summary(self.handoff)
        with patch.object(self.apu, "confirm", side_effect=OSError("interrupted publication")):
            self.assertTrue(self.deliver())
        self.assertFalse(self.receipts.exists())
        expected = hashlib.sha256(self.output.read_bytes()).hexdigest()
        self.summary(self.handoff, day="2026-11-21")
        next_output = self.root / "next.md"
        self.assertTrue(self.deliver(self.tomorrow, next_output))
        self.assertNotIn(self.handoff["ref"].encode(), next_output.read_bytes())
        receipt = self.shown()[self.handoff["ref"]]
        self.assertEqual((receipt["date"], receipt["sha256"]), ("2026-11-20", expected))

    def test_unverified_pending_artifact_is_reoffered_and_rollback_rechecks_the_snapshot(self):
        self.summary(self.handoff)
        with patch.object(self.apu, "confirm", side_effect=OSError("interrupted publication")):
            self.assertTrue(self.deliver())
        self.output.write_bytes(self.handoff["ref"].encode())
        self.summary(self.handoff, day="2026-11-21")
        paused = self.preview(self.tomorrow)
        self.assertTrue(morning_report.deliver(self.body, self.output, paused))
        self.assertEqual(self.shown()[self.handoff["ref"]]["date"], "2026-11-21")
        self.apu.publish(self.sidecar, {"schema": "taskmaster.apu-observation/v1", "status": "disabled"})
        self.assertTrue(morning_report.deliver(self.body, self.output, paused))
        self.assertEqual(self.output.read_bytes(), morning_report.compose(self.body, morning_report.Prepared(block=self.base)))

    def test_configured_disabled_preview_observes_reactivation_at_delivery(self):
        self.apu.publish(self.sidecar, {"schema": "taskmaster.apu-observation/v1", "status": "disabled"})
        preview = self.preview()
        self.assertEqual(preview.block, self.base)
        self.summary(self.handoff)
        self.assertTrue(morning_report.deliver(self.body, self.output, preview))
        self.assertIn(self.handoff["ref"].encode(), self.output.read_bytes())
        self.assertIn(self.handoff["ref"], self.shown())

    def test_two_writers_share_the_verified_choice_even_if_the_producer_refreshes(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        self.summary(self.handoff)
        ready, resume = threading.Event(), threading.Event()
        original_stage = self.apu.stage

        def pause_first(*args):
            pending = original_stage(*args)
            if not ready.is_set():
                ready.set()
                if not resume.wait(5):
                    raise RuntimeError("first writer timed out")
            return pending

        other_output = self.root / "second.md"
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.apu, "stage", side_effect=pause_first):
            first = pool.submit(self.deliver)
            try:
                self.assertTrue(ready.wait(5))
                self.summary(self.other)
                second_preview = self.preview()
                self.assertIn(self.other["ref"].encode(), second_preview.block)
                second = pool.submit(morning_report.deliver, self.body, other_output, second_preview)
            finally:
                resume.set()
            self.assertTrue(first.result(timeout=6))
            self.assertTrue(second.result(timeout=6))
        self.assertEqual(self.output.read_bytes(), other_output.read_bytes())
        self.assertEqual(set(self.shown()), {self.handoff["ref"]})

    def test_normal_renderer_verifies_both_empty_and_populated_inbox(self):
        import hashlib
        fixture = Path(__file__).resolve().parents[1] / "fixtures/batch2/agreement/export.json"
        self.export.write_bytes(fixture.read_bytes())
        self.summary(self.handoff)
        # Exercise the same subprocess render and main() call as the live report.
        self.renderer.side_effect = self.real_renderer
        inbox = self.root / "inbox.md"
        for body in (b"", b"One item\r\n\xe2\x80\x94\n"):
            with self.subTest(body=body):
                inbox.write_bytes(body)
                code = morning_report.main(["--export", str(self.export), "--body-file", str(inbox),
                                            "--output", str(self.output), "--now", self.now])
                self.assertEqual(code, 0)
                actual = self.output.read_bytes()
                self.assertEqual(actual.count(self.handoff["ref"].encode()), 1)
                self.assertTrue(actual.endswith(body))
                self.assertTrue(morning_report.verify_document(actual, self.preview()))
                self.assertEqual(self.shown()[self.handoff["ref"]]["sha256"], hashlib.sha256(actual).hexdigest())

    real_renderer = staticmethod(morning_report.render_once)
