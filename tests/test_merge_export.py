"""Pure entry merge, retention, bounds, freshness, reader acceptance and report rendering."""

import json
import unittest

from taskmaster import export, readmodel, report, schema
from taskmaster.canonical import canonical, objective_key, sha256_hex, snapshot_id
from taskmaster.merge import AttemptResult, SourceResult, merge_entry

from tests.helpers import FIXTURES

NOW = "2026-10-17T12:00:00Z"
LATER = "2026-10-17T13:00:00Z"
BINDING = "b" * 64
OTHER_BINDING = "c" * 64


def content(ns="ops", oid="obj-1", extra_refs=()):
    refs = [{"owner": "operator", "store_id": ns, "kind": "objective", "id": oid}]
    refs += [{"owner": "autowork", "store_id": "runs", "kind": "run", "id": r} for r in extra_refs]
    refs.sort(key=lambda r: schema.sort_key(schema.ref_tuple(r)))
    return {
        "schema_version": 1,
        "objective": {"namespace": ns, "id": oid, "label": None},
        "sources": [],
        "refs": refs,
        "links": [],
        "gaps": [],
        "coverage": [{"adapter": a, "status": "unsupported"} for a in sorted(schema.COVERAGE_ADAPTERS)],
    }


def sources(health="ok", checked=NOW, policy=None):
    rows = [SourceResult("operator", "ops", True, "live", "ok", (), checked),
            SourceResult("autowork", "runs", True, "live", health, () if health == "ok" else ("required_source_failed",),
                         checked if health == "ok" else None)]
    if policy is not None:
        rows.append(policy)
    return tuple(rows)


def ok(c=None, at=NOW):
    return AttemptResult("ok", c or content(), sources(checked=at))


def failed():
    return AttemptResult("failed", None, sources(health="failed"))


class Merge(unittest.TestCase):
    def test_first_success(self):
        e = merge_entry("ops", "obj-1", BINDING, None, ok(), NOW)
        obs = e["observation"]
        self.assertEqual((obs["attempt_status"], obs["last_success_at"], obs["content_generated_at"]), ("ok", NOW, NOW))
        self.assertEqual(e["trail_sha256"], sha256_hex(e["content_json"].encode()))

    def test_unchanged_trail_keeps_generation_time(self):
        first = merge_entry("ops", "obj-1", BINDING, None, ok(), NOW)
        second = merge_entry("ops", "obj-1", BINDING, first, ok(at=LATER), LATER)
        self.assertEqual(second["observation"]["content_generated_at"], NOW)
        self.assertEqual(second["observation"]["last_success_at"], LATER)
        self.assertEqual(second["content_json"], first["content_json"])

    def test_failure_retains_under_same_binding(self):
        first = merge_entry("ops", "obj-1", BINDING, None, ok(), NOW)
        second = merge_entry("ops", "obj-1", BINDING, first, failed(), LATER)
        obs = second["observation"]
        self.assertEqual(second["content_json"], first["content_json"])
        self.assertEqual((obs["attempt_status"], obs["has_successful_observation"], obs["last_success_at"]),
                         ("failed", True, NOW))
        self.assertEqual(obs["last_attempt_at"], LATER)
        self.assertIn("required_source_failed", obs["error_codes"])
        self.assertTrue(readmodel.evaluate(obs, 3600, LATER)["stale"])

    def test_binding_change_then_failure_drops_content(self):
        first = merge_entry("ops", "obj-1", BINDING, None, ok(), NOW)
        second = merge_entry("ops", "obj-1", OTHER_BINDING, first, failed(), LATER)
        self.assertIsNone(second["content_json"])
        self.assertIn("config_changed", second["observation"]["error_codes"])
        self.assertFalse(second["observation"]["has_successful_observation"])
        self.assertEqual([s["last_checked_at"] for s in second["observation"]["sources"]], [None, None])

    def test_unknown_evidence_time(self):
        e = merge_entry("ops", "obj-1", BINDING, None,
                        AttemptResult("ok", content(), (SourceResult("autowork", "runs", True, "saved_snapshot", "ok"),)),
                        NOW)
        self.assertIsNone(e["observation"]["last_success_at"])
        self.assertIn("evidence_time_unknown", e["observation"]["error_codes"])
        ev = readmodel.evaluate(e["observation"], 3600, NOW)
        self.assertTrue(ev["stale"])
        self.assertIsNone(ev["age_seconds"])

    def test_partial_optional_source(self):
        policy = SourceResult("policy", "scope", False, "live", "unavailable", ("optional_source_unavailable",))
        e = merge_entry("ops", "obj-1", BINDING, None,
                        AttemptResult("partial", content(), sources(policy=policy)), NOW)
        ev = readmodel.evaluate(e["observation"], 3600, NOW)
        self.assertTrue(ev["partial"])
        self.assertFalse(ev["stale"])
        self.assertEqual(ev["status_codes"], ["optional_source_unavailable"])

    def test_over_bounds_escaped_frame_isolated_to_one_objective(self):
        # Backslashes double once inside content_json and again inside the entry,
        # so decoded content under 64 KiB can still exceed the 80 KiB entry frame.
        big = content(extra_refs=["\\" * 30 + f"{i:04d}" for i in range(500)])
        text = schema.assert_canonical_content(big)
        self.assertLess(len(text), schema.MAX_CONTENT_BYTES)
        entry = merge_entry("ops", "obj-1", BINDING, None, AttemptResult("ok", big, sources()), NOW)
        self.assertIsNone(entry["content_json"])
        self.assertIn("over_bounds", entry["observation"]["error_codes"])
        neighbour = merge_entry("ops", "obj-2", BINDING, None, ok(content(oid="obj-2")), NOW)
        doc = export.verify(export.assemble([entry, neighbour], NOW, 3600))
        by_id = {e["id"]: e for e in doc["entries"]}
        self.assertEqual(by_id["obj-2"]["observation"]["attempt_status"], "ok")
        self.assertEqual(by_id["obj-1"]["observation"]["attempt_status"], "failed")


class Freshness(unittest.TestCase):
    def obs(self, **kw):
        e = merge_entry("ops", "obj-1", BINDING, None, ok(), NOW)
        return {**e["observation"], **kw}

    def test_age_and_max(self):
        self.assertEqual(readmodel.evaluate(self.obs(), 3600, LATER)["age_seconds"], 3600)
        self.assertFalse(readmodel.evaluate(self.obs(), 3600, LATER)["stale"])
        self.assertTrue(readmodel.evaluate(self.obs(), 3599, LATER)["stale"])

    def test_clock_skew(self):
        ev = readmodel.evaluate(self.obs(), 3600, "2026-10-17T11:00:00Z")
        self.assertTrue(ev["clock_skew"] and ev["stale"])
        self.assertEqual(ev["age_seconds"], 0)
        self.assertEqual(ev["status_codes"], ["clock_skew"])

    def test_combined_codes(self):
        first = merge_entry("ops", "obj-1", BINDING, None, ok(), NOW)
        second = merge_entry("ops", "obj-1", BINDING, first, failed(), LATER)
        ev = readmodel.evaluate(second["observation"], 3600, "2026-10-17T11:00:00Z")
        self.assertEqual(ev["status_codes"], ["clock_skew", "required_source_failed"])

    def test_never_successful(self):
        e = merge_entry("ops", "obj-1", BINDING, None, failed(), NOW)
        ev = readmodel.evaluate(e["observation"], 3600, NOW)
        self.assertTrue(ev["never_successful"] and ev["stale"])


class ReaderAcceptance(unittest.TestCase):
    def doc(self):
        entry = merge_entry("ops", "obj-1", BINDING, None, ok(), NOW)
        return json.loads(export.assemble([entry], NOW, 3600))

    def reencode(self, doc):
        return canonical(doc)

    def test_round_trip(self):
        data = export.assemble([merge_entry("ops", "obj-1", BINDING, None, ok(), NOW)], NOW, 3600)
        self.assertEqual(export.verify(data)["entries"][0]["id"], "obj-1")

    def test_wrong_objective_key(self):
        doc = self.doc()
        doc["entries"][0]["objective_key"] = "0" * 64
        with self.assertRaises(export.ExportError) as ctx:
            export.verify(self.reencode(doc))
        self.assertEqual((ctx.exception.code, ctx.exception.exit_code), ("invalid_export", 4))

    def test_wrong_snapshot(self):
        doc = self.doc()
        doc["snapshot_id"] = "0" * 64
        with self.assertRaises(export.ExportError):
            export.verify(self.reencode(doc))

    def test_tampered_content(self):
        doc = self.doc()
        doc["entries"][0]["content_json"] = doc["entries"][0]["content_json"].replace('"label":null', '"label":"x"')
        with self.assertRaises(export.ExportError):
            export.verify(self.reencode(doc))

    def test_non_canonical_hash_consistent_content_is_accepted(self):
        doc = self.doc()
        entry = doc["entries"][0]
        loose = json.dumps(json.loads(entry["content_json"]), indent=1, sort_keys=False).replace("\n", " ")
        entry["content_json"] = loose
        entry["trail_sha256"] = sha256_hex(loose.encode())
        doc["snapshot_id"] = snapshot_id([(entry["objective_key"], entry["trail_sha256"])])
        self.assertEqual(export.verify(self.reencode(doc))["entries"][0]["trail_sha256"], entry["trail_sha256"])

    def test_unsupported_and_garbage(self):
        doc = self.doc()
        doc["schema_version"] = 2
        with self.assertRaises(export.ExportError) as ctx:
            export.verify(self.reencode(doc))
        self.assertEqual(ctx.exception.code, "unsupported_export")
        with self.assertRaises(export.ExportError) as ctx:
            export.verify(b'{"schema":"x","schema":"y"}')
        self.assertEqual(ctx.exception.code, "invalid_export")

    def test_objective_key_is_recomputed(self):
        self.assertEqual(self.doc()["entries"][0]["objective_key"], objective_key("ops", "obj-1"))


class ReportGolden(unittest.TestCase):
    def test_golden(self):
        policy = SourceResult("policy", "scope", False, "live", "unavailable", ("optional_source_unavailable",))
        a = merge_entry("ops", "obj-1", BINDING, None, AttemptResult("partial", content(), sources(policy=policy)), NOW)
        b = merge_entry("ops", "obj-​hidden", BINDING, None, failed(), NOW)
        doc = export.verify(export.assemble([a, b], NOW, 3600))
        rendered = report.render(readmodel.show(doc, LATER))
        golden = (FIXTURES / "report" / "golden-report.txt").read_bytes()
        self.assertNotIn(b"\r", rendered)
        self.assertTrue(rendered.endswith(b"\n"))
        self.assertEqual(rendered, golden)


if __name__ == "__main__":
    unittest.main()
