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
        cases = {
            "bad --now": (("show", "--export", str(path), "--all", "--now", "yesterday"), 2),
            "bad config": (("build", "--config", str(self.tmp / "behavior.json")), 2),
            "missing export": (("show", "--export", str(self.tmp / "nope.json"), "--all"), 3),
            "missing objective": (("show", "--export", str(path), "--namespace", "ops", "--id", "nope"), 3),
            "invalid export": (("verify-export", "--export", str(garbage)), 4),
            "unreadable export": (("verify-export", "--export", str(self.tmp)), 8),
        }
        for name, (args, code) in cases.items():
            with self.subTest(name):
                proc = run(*args)
                self.assertEqual(proc.returncode, code, proc.stderr)
                self.assertEqual(proc.stdout, b"")
                self.assertTrue(proc.stderr.startswith(b"taskmaster: "), proc.stderr)

    def test_failed_objective_still_writes_export(self):
        proc = run("build", "--config", str(self.config()), "--now", NOW)
        self.assertEqual(proc.returncode, 5)
        self.assertIn(b"required_source_failed", proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["entries"][0]["observation"]["attempt_status"], "failed")
