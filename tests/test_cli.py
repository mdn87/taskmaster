"""CLI exit codes and output channels, run as a real subprocess."""

import json
import os
import subprocess
import sys

from tests.helpers import NOW, REPO, Workspace, make_run


def run(*args):
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    return subprocess.run([sys.executable, "-m", "taskmaster", *args], capture_output=True, env=env, timeout=120)


class Cli(Workspace):
    def built_export(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        proc = run("build", "--config", str(self.config()), "--now", NOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        path = self.tmp / "export.json"
        path.write_bytes(proc.stdout)
        return path

    def test_build_show_verify(self):
        path = self.built_export()
        shown = run("show", "--export", str(path), "--namespace", "ops", "--id", "obj-1", "--json", "--now", NOW)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        view = json.loads(shown.stdout)
        self.assertEqual((view["schema"], view["objectives"][0]["id"]), ("taskmaster.show/v1", "obj-1"))
        report = run("show", "--export", str(path), "--all", "--format", "report", "--now", NOW)
        self.assertTrue(report.stdout.startswith(b"taskmaster trail report v1\n"))
        verified = run("verify-export", "--export", str(path))
        self.assertEqual((verified.returncode, json.loads(verified.stdout)["entries"]), (0, 1))

    def test_exit_codes(self):
        path = self.built_export()
        garbage = self.tmp / "garbage.json"
        garbage.write_bytes(b'{"schema":1')
        newer = self.tmp / "newer.json"
        newer.write_bytes(b'{"schema":"taskmaster.export/v2","schema_version":2}')
        cases = {
            "bad --now": (("show", "--export", str(path), "--all", "--now", "yesterday"), 2),
            "bad config": (("build", "--config", str(self.tmp / "behavior.json")), 2),
            "missing export": (("show", "--export", str(self.tmp / "nope.json"), "--all"), 3),
            "missing objective": (("show", "--export", str(path), "--namespace", "ops", "--id", "nope"), 3),
            "invalid export": (("verify-export", "--export", str(garbage)), 4),
            "show invalid": (("show", "--export", str(garbage), "--all", "--json"), 4),
            "show unsupported": (("show", "--export", str(newer), "--all", "--json"), 4),
            "unreadable export": (("verify-export", "--export", str(self.tmp)), 8),
        }
        for name, (args, code) in cases.items():
            with self.subTest(name):
                proc = run(*args)
                self.assertEqual(proc.returncode, code, proc.stderr)
                self.assertEqual(proc.stdout, b"")
                self.assertTrue(proc.stderr.startswith(b"taskmaster: "), proc.stderr)
                if name == "show invalid":
                    self.assertIn(b"invalid_export", proc.stderr)
                elif name == "show unsupported":
                    self.assertIn(b"unsupported_export", proc.stderr)

    def test_failed_objective_still_writes_export(self):
        proc = run("build", "--config", str(self.config()), "--now", NOW)
        self.assertEqual(proc.returncode, 5)
        self.assertIn(b"required_source_failed", proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["entries"][0]["observation"]["attempt_status"], "failed")

    def test_refresh_status_and_failed_objective(self):
        cfg = self.config()
        path = self.tmp / "export.json"
        proc = run("refresh", "--config", str(cfg), "--export", str(path), "--now", NOW)
        self.assertEqual(proc.returncode, 5, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["published"])
        self.assertIn(b"objective ops/obj-1 failed:", proc.stderr)
        self.assertEqual(run("verify-export", "--export", str(path)).returncode, 0)
        make_run(self.store, "r1")
        self.annotate(["r1"])
        proc = run("refresh", "--config", str(cfg), "--export", str(path), "--now", NOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["failed_objectives"], 0)
        self.assertEqual(proc.stderr, b"")

    def test_refresh_configuration_precedes_prior_and_publication(self):
        cfg = self.config()
        target = self.tmp / "no-parent" / "export.json"
        for extra in (("--now", "invalid"), ()):
            if not extra:
                cfg.write_text("invalid TOML")
            proc = run("refresh", "--config", str(cfg), "--export", str(target), *extra)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertFalse(target.parent.exists())

    def test_refresh_objective_removal_and_rebinding(self):
        path = self.built_export()
        cfg = self.tmp / "taskmaster.toml"
        initial = cfg.read_text()
        self.annotate(["r1"], "obj-2", "second.json")
        cfg.write_text(initial + '\n[[objectives]]\nnamespace="ops"\nid="obj-2"\nannotation="second.json"\nrun_store="runs"\n')
        proc = run("refresh", "--config", str(cfg), "--export", str(path), "--now", NOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(json.loads(path.read_bytes())["entries"]), 2)
        cfg.write_text(initial)
        proc = run("refresh", "--config", str(cfg), "--export", str(path), "--now", NOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(json.loads(path.read_bytes())["entries"]), 1)
        cfg.write_text(initial.replace('annotation = "annotation.json"', 'annotation = "absent.json"'))
        proc = run("refresh", "--config", str(cfg), "--export", str(path), "--now", NOW)
        self.assertEqual(proc.returncode, 5, proc.stderr)
        entry = json.loads(path.read_bytes())["entries"][0]
        self.assertIsNone(entry["content_json"])
        self.assertIn("config_changed", entry["observation"]["error_codes"])
        self.assertIn("required_source_failed", entry["observation"]["error_codes"])

    def test_refresh_escaped_entry_overflow_isolates_other_objective(self):
        from taskmaster import builder, config, merge, schema
        from taskmaster.canonical import canonical

        # Valid absent identities can contain backslashes. Double encoding these
        # IDs exceeds the entry budget without exceeding decoded content limits.
        self.annotate([f'{i:02d}-' + '\\' * 400 for i in range(16)])
        self.annotate(["good-run"], "good", "good.json")
        make_run(self.store, "good-run")
        cfg_path = self.config()
        with cfg_path.open("a", encoding="utf-8") as stream:
            stream.write('\n[[objectives]]\nnamespace="ops"\nid="good"\nannotation="good.json"\nrun_store="runs"\n')
        cfg = config.load(cfg_path)
        attempt = builder.attempt_objective(cfg, cfg.objectives[0], NOW)
        self.assertEqual(attempt.status, "ok")
        text = canonical(attempt.content).decode("ascii")
        self.assertLess(len(text), schema.MAX_CONTENT_BYTES)
        self.assertGreater(merge._content_frame_size("ops", "obj-1", text), schema.MAX_ENTRY_CONTENT_FRAME)
        path = self.tmp / "export.json"
        proc = run("refresh", "--config", str(cfg_path), "--export", str(path), "--now", NOW)
        self.assertEqual(proc.returncode, 5, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["failed_objectives"], 1)
        by_id = {entry["id"]: entry for entry in json.loads(path.read_bytes())["entries"]}
        self.assertIsNone(by_id["obj-1"]["content_json"])
        self.assertEqual(by_id["obj-1"]["observation"]["attempt_status"], "failed")
        self.assertIn("over_bounds", by_id["obj-1"]["observation"]["error_codes"])
        self.assertEqual(by_id["good"]["observation"]["attempt_status"], "ok")
        self.assertIsNotNone(by_id["good"]["content_json"])
        self.assertEqual(run("verify-export", "--export", str(path)).returncode, 0)

    def test_refresh_and_read_restarts_preserve_content_identity(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        cfg = self.config()
        path = self.tmp / "export.json"
        first = run("refresh", "--config", str(cfg), "--export", str(path), "--now", NOW)
        self.assertEqual(first.returncode, 0, first.stderr)
        before = json.loads(path.read_bytes())
        self.assertEqual(run("verify-export", "--export", str(path)).returncode, 0)
        # Every run starts and exits a new interpreter: no process-local cache
        # can satisfy the saved-export read or carry identity across refreshes.
        show_args = ("show", "--export", str(path), "--all", "--json", "--now", NOW)
        shown, restarted = run(*show_args), run(*show_args)
        self.assertEqual((shown.returncode, restarted.returncode), (0, 0))
        self.assertEqual(shown.stdout, restarted.stdout)
        self.assertEqual(json.loads(restarted.stdout)["snapshot_id"], before["snapshot_id"])
        after_time = "2026-10-17T12:01:00Z"
        refreshed = run("refresh", "--config", str(cfg), "--export", str(path), "--now", after_time)
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        after = json.loads(path.read_bytes())
        self.assertEqual(before["snapshot_id"], after["snapshot_id"])
        a, b = before["entries"][0], after["entries"][0]
        self.assertEqual(a["content_json"], b["content_json"])
        self.assertEqual(a["trail_sha256"], b["trail_sha256"])
        self.assertEqual(a["observation"]["content_generated_at"], b["observation"]["content_generated_at"])
        self.assertEqual(b["observation"]["last_attempt_at"], after_time)
        self.assertEqual(after["published_at"], after_time)

    def test_refresh_hash_invalid_recovery_requires_explicit_flag(self):
        path = self.built_export()
        doc = json.loads(path.read_bytes())
        # Still valid JSON and content structure, but the saved trail hash no
        # longer describes the exact immutable string bytes.
        doc["entries"][0]["content_json"] += " "
        invalid = json.dumps(doc).encode("ascii")
        path.write_bytes(invalid)
        args = ("refresh", "--config", str(self.tmp / "taskmaster.toml"), "--export", str(path),
                "--now", "2026-10-17T12:01:00Z")
        calls = self.owner_calls()
        refused = run(*args)
        self.assertEqual(refused.returncode, 4, refused.stderr)
        self.assertIn(b"existing_export_invalid", refused.stderr)
        self.assertEqual(path.read_bytes(), invalid)
        self.assertEqual(self.owner_calls(), calls)
        recovered = run(*args, "--recover-invalid-export")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        preserved = list(self.tmp.glob("export.json.*.invalid"))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_bytes(), invalid)
        rebuilt = json.loads(path.read_bytes())
        self.assertEqual(rebuilt["entries"][0]["observation"]["content_generated_at"], "2026-10-17T12:01:00Z")
        self.assertEqual(run("verify-export", "--export", str(path)).returncode, 0)
