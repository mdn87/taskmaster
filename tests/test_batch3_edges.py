"""Batch 3 edge cases against synthetic stores and the fake owner.

``fixtures/batch3/cases.json`` lists the cases; ``test_case_coverage`` maps each
case id to the methods here, or to earlier tests that already execute it.
"""

import json
import sys
import unittest

from taskmaster import config, sources

from tests.helpers import FAKE_OWNER, TASK, Workspace, make_run, write_json
from tests.test_builder import build, gaps, links, only

REWORDED = "b" * 64


def has_run(content):
    return {l["to"]["id"]: l["status"] for l in links(content, "has_run")}


class RunRecords(Workspace):
    def setUp(self):
        super().setUp()
        make_run(self.store, "r1")
        self.annotate(["r1"])

    def failed(self, record, raw):
        (self.store / "r1" / record).write_bytes(raw)
        entry, content = only(build(self.config()))
        self.assertIsNone(content)
        self.assertEqual(entry["observation"]["attempt_status"], "failed")
        return entry["observation"]["error_codes"]

    def test_malformed_record_fails_the_objective(self):
        for record in ("result.json", "state.json", "agent-assignment.json"):
            with self.subTest(record=record):
                make_run(self.store, "r1")
                if record == "result.json":
                    # The fake owner parses result.json itself, so it fails first, as the real owner does.
                    self.assertEqual(self.failed(record, b'{"run_id":'), ["required_source_failed"])
                else:
                    self.assertEqual(self.failed(record, b'{"run_id":'), ["required_input_invalid"])
                    self.assertEqual(self.failed(record, b'["not", "an", "object"]'), ["required_input_invalid"])

    def test_duplicate_key_record_fails_instead_of_hiding_a_field(self):
        # A duplicate objective_id must not be read as either value, hiding a membership conflict.
        raw = json.dumps({"run_id": "r1", "status": "completed", "task_sha256": TASK, "objective_id": "obj-1"})
        raw = raw[:-1] + ', "objective_id": "other"}'
        self.assertEqual(self.failed("result.json", raw.encode()), ["required_input_invalid"])
        make_run(self.store, "r1")
        self.assertEqual(self.failed("state.json", b'{"run_id":"r1","status":"completed","status":"failed"}'),
                         ["required_input_invalid"])

    def test_oversized_record_fails_before_parsing(self):
        raw = b'{"pad":"' + b"x" * sources.MAX_RECORD_BYTES + b'"}'
        self.assertEqual(self.failed("state.json", raw), ["required_input_invalid"])


class Membership(Workspace):
    def test_reworded_retries_resolve_only_by_explicit_membership(self):
        make_run(self.store, "r1")
        retry = make_run(self.store, "r2")
        unlisted = make_run(self.store, "r3")
        for run in (retry, unlisted):
            write_json(run / "result.json", {"run_id": run.name, "status": "completed", "task_sha256": REWORDED})
        self.annotate(["r1", "r2"])
        entry, content = only(build(self.config()))
        self.assertEqual(entry["observation"]["attempt_status"], "ok")
        self.assertEqual(has_run(content), {"r1": "resolved", "r2": "resolved"})
        tasks = {l["to"]["id"]: l["asserted"]["task_sha256"] for l in links(content, "has_run")}
        self.assertEqual(tasks, {"r1": TASK, "r2": REWORDED})
        self.assertNotIn("r3", json.dumps(content))

    def test_duplicate_ids_across_stores_never_cross(self):
        other = self.tmp / "other"
        make_run(self.store, "r1")
        make_run(other, "r1", status="failed")
        make_run(other, "r2")
        self.annotate(["r1", "r2"], objective_id="obj-1", name="a.json")
        self.annotate(["r1"], objective_id="obj-2", name="b.json")
        command = json.dumps([sys.executable, str(FAKE_OWNER), str(self.behavior)])
        cfg = self.tmp / "taskmaster.toml"
        cfg.write_text("\n".join([
            "schema_version = 1", "max_age_seconds = 3600", "[autowork]", f"command = {command}", 'version = "0.1.0"',
            "[[stores]]", 'alias = "runs"', 'owner = "autowork"', 'path = "store"', 'mode = "live"',
            "[[stores]]", 'alias = "other"', 'owner = "autowork"', 'path = "other"', 'mode = "live"',
            "[[objectives]]", 'namespace = "ops"', 'id = "obj-1"', 'annotation = "a.json"', 'run_store = "runs"',
            "[[objectives]]", 'namespace = "ops"', 'id = "obj-2"', 'annotation = "b.json"', 'run_store = "other"',
        ]) + "\n", encoding="utf-8")
        doc = build(cfg)
        entries = {(c := json.loads(e["content_json"]))["objective"]["id"]: c for e in doc["entries"]}
        first, second = entries["obj-1"], entries["obj-2"]
        # r2 exists only in the other store; it is missing here, not borrowed.
        self.assertEqual(has_run(first), {"r1": "resolved", "r2": "missing"})
        self.assertIn(("not_in_configured_store", "r2"), gaps(first))
        status = {o: {l["to"]["id"]: l["asserted"].get("dispatch_status") for l in links(c, "has_run")}
                  for o, c in entries.items()}
        self.assertEqual(status, {"obj-1": {"r1": "completed", "r2": None}, "obj-2": {"r1": "failed"}})
        stores = {o: {s["store_id"] for s in c["sources"] if s["owner"] == "autowork"} for o, c in entries.items()}
        self.assertEqual(stores, {"obj-1": {"runs"}, "obj-2": {"other"}})


class Identity(Workspace):
    def test_run_id_case_is_exact(self):
        make_run(self.store, "Run-1")
        self.annotate(["run-1"])
        _, content = only(build(self.config()))
        # Even on a case-insensitive filesystem the annotated id never resolves a differently cased run.
        self.assertEqual(has_run(content), {"run-1": "missing"})
        self.assertEqual(gaps(content), [("not_in_configured_store", "run-1")])

    def test_unicode_run_id_is_not_normalized(self):
        make_run(self.store, "ré")  # decomposed
        self.annotate(["ré"])  # composed
        _, content = only(build(self.config()))
        self.assertEqual(has_run(content), {"ré": "missing"})

    def test_unicode_label_is_published_verbatim(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        label = "Café über <b>x</b>"
        cfg = self.config(label=label)
        entry, _ = only(build(cfg))
        self.assertEqual(config.load(cfg).objectives[0].label, label)
        self.assertEqual(json.loads(entry["content_json"])["objective"]["label"], label)


class Privacy(Workspace):
    def test_unallowlisted_values_and_paths_never_reach_the_export(self):
        run = make_run(self.store, "r1")
        result = json.loads((run / "result.json").read_text(encoding="ascii"))
        write_json(run / "result.json", {**result, "secret_note": "SECRET-TOKEN-123", "prompt": "PRIVATE-PROMPT"})
        (run / "transcript.jsonl").write_text('{"text": "TRANSCRIPT-BODY"}\n', encoding="ascii")
        self.annotate(["r1"])
        text = json.dumps(build(self.config()))
        for private in ("SECRET-TOKEN-123", "PRIVATE-PROMPT", "TRANSCRIPT-BODY", str(self.tmp),
                        str(self.tmp).replace("\\", "/"), str(self.tmp).replace("\\", "\\\\")):
            self.assertNotIn(private, text)


if __name__ == "__main__":
    unittest.main()
