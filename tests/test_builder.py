"""Source adapter and builder behavior against synthetic stores and the fake owner."""

import hashlib
import json
import os
import unittest

from taskmaster import builder, config, export, readmodel, sources
from taskmaster.merge import merge_entry

from tests.helpers import NOW, Workspace, make_run, tree_digest, write_json

LATER = "2026-10-17T13:00:00Z"


def build(cfg_path, now=NOW, prior=None):
    cfg = config.load(cfg_path)
    entries = builder.build_entries(cfg, prior, now)
    return export.verify(export.assemble(entries, now, cfg.max_age_seconds))


def only(doc):
    entry = doc["entries"][0]
    return entry, (json.loads(entry["content_json"]) if entry["content_json"] else None)


def links(content, relation):
    return [l for l in content["links"] if l["relation"] == relation]


def gaps(content):
    return sorted((g["code"], g["affected_ref"]["id"]) for g in content["gaps"])


class HappyPath(Workspace):
    def test_resolved_runs_and_gaps(self):
        make_run(self.store, "r1")
        make_run(self.store, "r2", assignment=False)
        self.annotate(["r1", "r2"])
        entry, c = only(build(self.config()))
        self.assertEqual(entry["observation"]["attempt_status"], "ok")
        self.assertEqual([l["status"] for l in links(c, "has_run")], ["resolved", "resolved"])
        self.assertEqual({l["from"]["id"]: l["status"] for l in links(c, "has_assignment")},
                         {"r1": "resolved", "r2": "missing"})
        self.assertEqual(gaps(c), [("assignment_unrecorded", "r2"), ("objective_binding_unrecorded", "r1"),
                                   ("objective_binding_unrecorded", "r2"), ("revision_unrecorded", "r1"),
                                   ("revision_unrecorded", "r2")])
        run = links(c, "has_run")[0]
        self.assertEqual(run["asserted"]["dispatch_status"], "completed")
        self.assertEqual([e["source_ref"]["record_type"] for e in run["evidence"]],
                         ["request-summary", "result", "state", "objective-annotation"])

    def test_deterministic_and_store_untouched(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        before = tree_digest(self.store)
        first = only(build(self.config()))[0]
        second = only(build(self.config(), now=LATER))[0]
        self.assertEqual(first["trail_sha256"], second["trail_sha256"])
        self.assertEqual(tree_digest(self.store), before)

    def test_missing_run_and_owner_scope(self):
        make_run(self.store, "r1")
        self.annotate(["r1", "ghost"])
        _, c = only(build(self.config()))
        self.assertEqual({l["to"]["id"]: l["status"] for l in links(c, "has_run")}, {"r1": "resolved", "ghost": "missing"})
        self.assertIn(("not_in_configured_store", "ghost"), gaps(c))

    def test_no_present_runs_skips_owner(self):
        self.set_behavior(mode="fail")
        self.annotate(["ghost"])
        entry, c = only(build(self.config()))
        self.assertEqual(entry["observation"]["attempt_status"], "ok")
        self.assertEqual(self.owner_calls(), [])

    def test_membership_conflict_and_source_commit(self):
        make_run(self.store, "r1", objective_id="someone-else", source_commit="abc123")
        make_run(self.store, "r2", objective_id="obj-1")
        self.annotate(["r1", "r2"])
        _, c = only(build(self.config()))
        self.assertEqual({l["to"]["id"]: l["status"] for l in links(c, "has_run")}, {"r1": "conflict", "r2": "resolved"})
        self.assertIn(("membership_conflict", "r1"), gaps(c))
        self.assertIn(("repository_identity_unrecorded", "r1"), gaps(c))
        self.assertNotIn(("objective_binding_unrecorded", "r2"), gaps(c))

    def test_chip_mapping(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        _, c = only(build(self.config(chips=[("proj", "chip-9")])))
        chip = links(c, "annotates_chip")[0]
        self.assertEqual((chip["status"], chip["to"]["id"]), ("resolved", "chip-9"))
        self.assertIn(("chip_existence_unchecked", "chip-9"), gaps(c))

    def test_probe_linked_run_counts_as_input(self):
        make_run(self.store, "r1")
        make_run(self.store, "outside")
        self.annotate(["r1"])
        self.set_behavior(linked={"r1": ["outside"]})
        _, c = only(build(self.config()))
        ids = {i["record_id"] for s in c["sources"] if s["owner"] == "autowork" for i in s["inputs"]}
        self.assertEqual(ids, {"r1", "outside"})


class Policy(Workspace):
    def setUp(self):
        super().setUp()
        self.policy = self.tmp / "policy.toml"
        self.policy.write_bytes(b"scope = 1\n")
        self.policy_sha = hashlib.sha256(self.policy.read_bytes()).hexdigest()

    def test_match_mismatch_absent(self):
        make_run(self.store, "r1", policy_sha=self.policy_sha)
        make_run(self.store, "r2", policy_sha="f" * 64)
        make_run(self.store, "r3")
        self.annotate(["r1", "r2", "r3"])
        entry, c = only(build(self.config(policy="policy.toml")))
        self.assertEqual(entry["observation"]["attempt_status"], "ok")
        by_run = {l["from"]["id"]: l["status"] for l in links(c, "uses_policy")}
        self.assertEqual(by_run, {"assignment-r1": "resolved", "assignment-r2": "unsupported", "assignment-r3": "missing"})
        self.assertIn(("policy_historical_lookup_unsupported", "f" * 64), gaps(c))
        self.assertIn(("policy_binding_absent", "assignment-r3"), gaps(c))

    def test_unreadable_policy_is_partial(self):
        make_run(self.store, "r1", policy_sha=self.policy_sha)
        self.annotate(["r1"])
        entry, c = only(build(self.config(policy="missing-policy.toml")))
        obs = entry["observation"]
        self.assertEqual((obs["attempt_status"], obs["error_codes"]), ("partial", ["optional_source_unavailable"]))
        self.assertEqual(links(c, "uses_policy")[0]["status"], "unavailable")
        self.assertEqual({x["adapter"]: x["status"] for x in c["coverage"]}["policy.current"], "unavailable")

    def test_policy_churn_is_partial(self):
        make_run(self.store, "r1", policy_sha=self.policy_sha)
        self.annotate(["r1"])
        self.set_behavior(mode="mutate-policy", policy_path=str(self.policy))
        entry, c = only(build(self.config(policy="policy.toml")))
        obs = entry["observation"]
        self.assertEqual(obs["attempt_status"], "partial")
        policy_row = [s for s in obs["sources"] if s["owner"] == "policy"][0]
        self.assertEqual((policy_row["health"], policy_row["codes"]), ("changed", ["source_changed_during_read"]))
        self.assertIsNotNone(c)


class Failures(Workspace):
    def failed_codes(self, cfg):
        entry, c = only(build(cfg))
        self.assertIsNone(c)
        self.assertEqual(entry["observation"]["attempt_status"], "failed")
        return entry["observation"]["error_codes"]

    def test_annotation_failures(self):
        make_run(self.store, "r1")
        self.assertEqual(self.failed_codes(self.config()), ["required_source_failed"])
        (self.tmp / "annotation.json").write_text("{not json", encoding="ascii")
        self.assertEqual(self.failed_codes(self.config()), ["required_input_invalid"])
        self.annotate(["r1"], objective_id="different")
        self.assertEqual(self.failed_codes(self.config()), ["annotation_identity_mismatch"])

    def test_owner_failures(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        for mode, code in (("fail", "required_source_failed"), ("schema3", "required_schema_unsupported"),
                           ("garbage", "required_input_invalid")):
            with self.subTest(mode=mode):
                self.set_behavior(mode=mode)
                self.assertEqual(self.failed_codes(self.config()), [code])
        self.set_behavior()
        self.assertEqual(self.failed_codes(self.config(version="9.9.9")), ["owner_version_mismatch"])

    def test_owner_schema_2_is_accepted(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        self.set_behavior(mode="schema2")
        entry, content = only(build(self.config()))
        self.assertNotEqual(entry["observation"]["attempt_status"], "failed")
        autowork = [s for s in content["sources"] if s["owner"] == "autowork"][0]
        self.assertEqual(autowork["owner_schema_version"], 2)

    def test_ambiguous_run(self):
        make_run(self.store, "r1")
        make_run(self.store, "r1", subdir="nested")
        self.annotate(["r1"])
        self.assertEqual(self.failed_codes(self.config()), ["ambiguous_run_identity"])

    def test_required_churn_retries_once_then_fails(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        self.set_behavior(mode="mutate-store")
        self.assertEqual(self.failed_codes(self.config()), ["source_changed_during_read"])
        outcome_calls = [c for c in self.owner_calls() if c.startswith("campaign outcome")]
        self.assertEqual(len(outcome_calls), 2)


class Retention(Workspace):
    """TM-R21-01: failures retain prior content only under the same binding."""

    def test_membership_edit_then_owner_failure_retains_stale(self):
        make_run(self.store, "r1")
        make_run(self.store, "r2")
        self.annotate(["r1", "r2"])
        first = build(self.config())
        self.annotate(["r1"])
        self.set_behavior(mode="fail")
        entry, c = only(build(self.config(), now=LATER, prior=first))
        obs = entry["observation"]
        self.assertEqual(entry["trail_sha256"], first["entries"][0]["trail_sha256"])
        self.assertEqual((obs["attempt_status"], obs["last_success_at"]), ("failed", NOW))
        self.assertTrue(readmodel.evaluate(obs, 3600, LATER)["stale"])

    def test_unreadable_annotation_retains(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        first = build(self.config())
        os.remove(self.tmp / "annotation.json")
        entry, _ = only(build(self.config(), now=LATER, prior=first))
        self.assertEqual(entry["trail_sha256"], first["entries"][0]["trail_sha256"])
        self.assertEqual(entry["observation"]["error_codes"], ["required_source_failed"])

    def test_locator_rebinding_drops_content(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        first = build(self.config())
        self.annotate(["r1"], name="moved.json")
        self.set_behavior(mode="fail")
        entry, c = only(build(self.config(annotation="moved.json"), now=LATER, prior=first))
        self.assertIsNone(c)
        self.assertEqual(entry["observation"]["error_codes"], ["config_changed", "required_source_failed"])


class SavedSnapshot(Workspace):
    def test_manifest_time_unknown_and_mismatch(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        write_json(self.tmp / "manifest.json", sources.manifest_for(self.store, None))
        entry, c = only(build(self.config(mode="saved_snapshot", manifest="manifest.json")))
        obs = entry["observation"]
        self.assertEqual((obs["attempt_status"], obs["error_codes"], obs["last_success_at"]),
                         ("ok", ["evidence_time_unknown"], None))
        self.assertTrue(readmodel.evaluate(obs, 3600, NOW)["stale"])
        (self.store / "r1" / "state.json").write_text("{}", encoding="ascii")
        entry, c = only(build(self.config(mode="saved_snapshot", manifest="manifest.json")))
        self.assertEqual(entry["observation"]["error_codes"], ["required_input_invalid"])

    def test_capture_time_is_evidence_time(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        write_json(self.tmp / "manifest.json", sources.manifest_for(self.store, "2026-10-10T06:30:00Z"))
        entry, _ = only(build(self.config(mode="saved_snapshot", manifest="manifest.json")))
        self.assertEqual(entry["observation"]["last_success_at"], "2026-10-10T06:30:00Z")


class Footprint(Workspace):
    def test_skipped_dirs_and_unlisted_files(self):
        make_run(self.store, "r1")
        (self.store / "r1" / "credential-isolation").mkdir()
        (self.store / "r1" / "credential-isolation" / "result.json").write_text("{}", encoding="ascii")
        (self.store / "r1" / "notes.txt").write_text("not a record", encoding="ascii")
        fp = sources.footprint(self.store)
        self.assertEqual(sorted(fp.files), ["r1/agent-assignment.json", "r1/request-summary.json", "r1/result.json",
                                            "r1/state.json"])
        self.assertEqual(fp.dirs, ("r1",))

    def test_links_rejected(self):
        make_run(self.store, "r1")
        try:
            os.symlink(self.store / "r1", self.store / "alias", target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation needs privilege on this host")
        with self.assertRaises(sources.SourceError) as ctx:
            sources.footprint(self.store)
        self.assertEqual(ctx.exception.code, "required_input_invalid")


if __name__ == "__main__":
    unittest.main()
