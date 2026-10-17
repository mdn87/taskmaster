"""Regressions for bounded owner reads and independent objective refreshes."""

import json
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from taskmaster import autowork, builder, config, export, sources
from tests.helpers import NOW, Workspace, make_run, write_json


class ObjectiveIsolation(Workspace):
    def test_directory_enumeration_stops_at_limit_plus_one(self):
        consumed = []

        def entries():
            for i in range(100):
                consumed.append(i)
                if len(consumed) > 4:
                    self.fail("directory enumeration continued past the allocation bound")
                yield SimpleNamespace(name=f"entry-{i}")

        @contextmanager
        def scan(_path):
            yield entries()

        with patch.object(sources, "MAX_FOOTPRINT_ENTRIES", 3), patch.object(sources.os, "scandir", scan):
            with self.assertRaises(sources.SourceError) as context:
                sources.footprint(self.store)
        self.assertEqual(context.exception.code, "required_input_invalid")
        self.assertEqual(len(consumed), 4)

    def configured(self, bad_ids):
        self.annotate(bad_ids)
        path = self.config()
        self.annotate(["good-run"], objective_id="good", name="good.json")
        make_run(self.store, "good-run")
        with path.open("a", encoding="utf-8") as stream:
            stream.write('\n[[objectives]]\nnamespace="ops"\nid="good"\nannotation="good.json"\nrun_store="runs"\n')
        return config.load(path)

    def assert_isolated(self, cfg, code):
        entries = builder.build_entries(cfg, None, NOW)
        doc = export.verify(export.assemble(entries, NOW, 86400))
        by_id = {e["id"]: e for e in doc["entries"]}
        self.assertEqual(by_id["obj-1"]["observation"]["attempt_status"], "failed")
        self.assertIn(code, by_id["obj-1"]["observation"]["error_codes"])
        self.assertEqual(by_id["good"]["observation"]["attempt_status"], "ok")

    def test_nested_annotation_id_fails_only_its_objective(self):
        self.assert_isolated(self.configured([{}]), "required_input_invalid")

    def test_reference_count_overflow_fails_only_its_objective(self):
        self.assert_isolated(self.configured([f"absent-{i}" for i in range(2048)]), "over_bounds")

    def test_annotation_churn_is_retried_then_retains(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        cfg = config.load(self.config())
        first = builder.build_entries(cfg, None, NOW)
        original = builder._observe
        calls = []

        def observe(*args):
            value = original(*args)
            calls.append(1)
            self.annotate(["r1", f"added-{len(calls)}"])
            return value

        with patch.object(builder, "_observe", side_effect=observe):
            second = builder.build_entries(cfg, {"entries": first}, NOW)
        self.assertEqual(len(calls), 2)
        self.assertEqual(second[0]["content_json"], first[0]["content_json"])
        self.assertEqual(second[0]["observation"]["attempt_status"], "failed")
        self.assertIn("source_changed_during_read", second[0]["observation"]["error_codes"])

    def test_private_tree_refused_before_owner_projection(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        (self.store / "r1" / "child-output").mkdir()
        entry = builder.build_entries(config.load(self.config()), None, NOW)[0]
        self.assertEqual(entry["observation"]["attempt_status"], "failed")
        self.assertEqual(self.owner_calls(), [])

    def test_ledger_files_affect_footprint_and_exported_evidence(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        ledger = self.store / "r1" / "owner-attempts"
        record = write_json(ledger / "worker.reservation.json", {"state": "before"})
        before = sources.footprint(self.store)
        write_json(record, {"state": "after"})
        after = sources.footprint(self.store)
        self.assertNotEqual(before.signature(), after.signature())
        entry = builder.build_entries(config.load(self.config()), None, NOW)[0]
        content = json.loads(entry["content_json"])
        digest = after.files["r1/owner-attempts/worker.reservation.json"]
        source = next(s for s in content["sources"] if s["owner"] == "autowork")
        self.assertIn(digest, [i["raw_sha256"] for i in source["inputs"]])
        run_link = next(l for l in content["links"] if l["relation"] == "has_run")
        self.assertIn(digest, [e["raw_sha256"] for e in run_link["evidence"]])

    def test_saved_manifest_rejects_changed_ledger_bytes(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        record = write_json(self.store / "r1" / "owner-attempts" / "worker.reservation.json", {})
        write_json(self.tmp / "manifest.json", sources.manifest_for(self.store, NOW))
        write_json(record, {"changed": True})
        cfg = config.load(self.config(mode="saved_snapshot", manifest="manifest.json"))
        entry = builder.build_entries(cfg, None, NOW)[0]
        self.assertEqual(entry["observation"]["error_codes"], ["required_input_invalid"])

    def test_single_policy_change_retries_to_a_stable_observation(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        policy = self.tmp / "policy.toml"
        policy.write_text("first", encoding="ascii")
        cfg = config.load(self.config(policy="policy.toml"))
        original = builder._observe
        calls = []

        def observe(*args):
            value = original(*args)
            calls.append(1)
            if len(calls) == 1:
                policy.write_text("second", encoding="ascii")
            return value

        with patch.object(builder, "_observe", side_effect=observe):
            entry = builder.build_entries(cfg, None, NOW)[0]
        self.assertEqual(len(calls), 2)
        self.assertEqual(entry["observation"]["attempt_status"], "ok")


class BoundedOwner(unittest.TestCase):
    def test_stdout_overflow_is_typed_failure(self):
        with patch.object(autowork, "MAX_OWNER_OUTPUT", 1024):
            with self.assertRaises(sources.SourceError) as context:
                autowork._run([sys.executable, "-c", "import sys;sys.stdout.write('x'*16384)"], 5)
        self.assertEqual(context.exception.code, "required_input_invalid")

    def test_stderr_overflow_is_typed_failure(self):
        with patch.object(autowork, "MAX_OWNER_OUTPUT", 1024):
            with self.assertRaises(sources.SourceError):
                autowork._run([sys.executable, "-c", "import sys;sys.stderr.write('x'*16384)"], 5)

    def test_timeout_is_typed_failure(self):
        with self.assertRaises(sources.SourceError) as context:
            autowork._run([sys.executable, "-c", "import time;time.sleep(5)"], 0.1)
        self.assertEqual(context.exception.code, "required_source_failed")


if __name__ == "__main__":
    unittest.main()
