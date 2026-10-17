"""Publication behavior through real processes and Windows sharing handles."""

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from taskmaster import config, export, publication
from taskmaster.canonical import canonical, sha256_hex, snapshot_id
from tests.helpers import NOW, REPO, Workspace, make_run
from tests.test_cli import run


class Publication(Workspace):
    def setup_export(self):
        make_run(self.store, "r1")
        self.annotate(["r1"])
        self.cfg = config.load(self.config())
        self.path = self.tmp / "export.json"
        return publication.refresh(self.cfg, self.path, NOW)

    def child(self, code, *args):
        child = subprocess.Popen([sys.executable, "-u", "-c", code, *map(str, args)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env={**os.environ, "PYTHONPATH": str(REPO / "src")})
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        self.addCleanup(cleanup)
        return child

    def test_first_and_unchanged_refresh(self):
        first = self.setup_export()
        second = publication.refresh(self.cfg, self.path, "2026-10-17T12:01:00Z")
        a, b = first["entries"][0], second["entries"][0]
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(a["content_json"], b["content_json"])
        self.assertEqual(a["observation"]["content_generated_at"], b["observation"]["content_generated_at"])
        self.assertNotEqual(a["observation"]["last_attempt_at"], b["observation"]["last_attempt_at"])
        self.assertEqual(export.load(self.path)[0], second)

    def test_writer_contention_and_process_death_release(self):
        self.setup_export()
        old = self.path.read_bytes()
        child = self.child("from pathlib import Path; import sys,time; from taskmaster.publication import writer_lock\nwith writer_lock(Path(sys.argv[1])):\n print('locked',flush=True); time.sleep(60)", self.path)
        self.assertEqual(child.stdout.readline().strip(), b"locked")
        result = run("refresh", "--config", str(self.cfg.path), "--export", str(self.path), "--now", NOW)
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertEqual(self.path.read_bytes(), old)
        child.kill()
        child.communicate(timeout=10)
        self.assertTrue(publication.lock_path(self.path).exists())
        lock_identity = publication.lock_path(self.path).stat().st_ino
        restarted = run("refresh", "--config", str(self.cfg.path), "--export", str(self.path), "--now", NOW)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.assertEqual(publication.lock_path(self.path).stat().st_ino, lock_identity)

    def test_interrupted_before_replace_preserves_live_file_and_temp(self):
        self.setup_export()
        old = self.path.read_bytes()
        child = self.child("from pathlib import Path; import sys,time; from taskmaster import publication,config\ndef pause(*args):\n print('before-replace',flush=True); time.sleep(60)\npublication.os.replace=pause\npublication.refresh(config.load(sys.argv[1]),Path(sys.argv[2]),'2026-10-17T12:01:00Z')", self.cfg.path, self.path)
        self.assertEqual(child.stdout.readline().strip(), b"before-replace")
        child.kill()
        child.communicate(timeout=10)
        abandoned = list(self.tmp.glob("export.json.*.tmp"))
        self.assertEqual(len(abandoned), 1)
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(run("show", "--export", str(self.path), "--all").returncode, 0)
        publication.refresh(self.cfg, self.path, NOW)
        self.assertTrue(abandoned[0].exists())

    def test_failed_replace_keeps_prior_and_records_diagnostic(self):
        self.setup_export()
        old = self.path.read_bytes()
        with patch.object(publication.os, "replace", side_effect=PermissionError("private cause")) as replace:
            with self.assertRaises(publication.PublicationError) as cm:
                publication.refresh(self.cfg, self.path, NOW)
        self.assertEqual(cm.exception.exit_code, 7)
        self.assertEqual(replace.call_count, 5)
        self.assertEqual(self.path.read_bytes(), old)
        logs = list((self.tmp / ".taskmaster-diagnostics").glob("*.json"))
        self.assertTrue(logs)
        self.assertIn("private cause", logs[-1].read_text())

    def test_invalid_recovery_preserves_exact_bytes_before_build(self):
        self.setup_export()
        invalid = b'{"broken":'
        self.path.write_bytes(invalid)
        calls = self.owner_calls()
        refused = run("refresh", "--config", str(self.cfg.path), "--export", str(self.path))
        self.assertEqual(refused.returncode, 4, refused.stderr)
        self.assertIn(b"existing_export_invalid", refused.stderr)
        self.assertEqual(self.owner_calls(), calls)
        self.assertEqual(self.path.read_bytes(), invalid)
        publication.refresh(self.cfg, self.path, NOW, recover_invalid_export=True)
        preserved = list(self.tmp.glob("export.json.*.invalid"))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_bytes(), invalid)
        export.load(self.path)

    def test_preservation_failure_and_unsupported_schema_do_not_build(self):
        self.setup_export()
        self.path.write_bytes(b"bad")
        calls = self.owner_calls()
        with patch.object(publication, "preserve_invalid", side_effect=OSError("cannot preserve")):
            with self.assertRaises(publication.PublicationError):
                publication.refresh(self.cfg, self.path, NOW, recover_invalid_export=True)
        self.assertEqual(self.path.read_bytes(), b"bad")
        self.assertEqual(self.owner_calls(), calls)
        newer = b'{"schema":"taskmaster.export/v2","schema_version":2}'
        self.path.write_bytes(newer)
        with self.assertRaises(export.ExportError) as cm:
            publication.refresh(self.cfg, self.path, NOW, recover_invalid_export=True)
        self.assertEqual(cm.exception.code, "unsupported_export")
        self.assertEqual(self.path.read_bytes(), newer)
        self.assertEqual(list(self.tmp.glob("*.invalid")), [])

    def test_noncanonical_retained_content_is_publishable(self):
        doc = self.setup_export()
        entry = doc["entries"][0]
        entry["content_json"] = json.dumps(json.loads(entry["content_json"]), indent=1, ensure_ascii=True)
        # Whitespace must be printable ASCII: spaces, not literal line breaks.
        entry["content_json"] = entry["content_json"].replace("\n", " ")
        entry["trail_sha256"] = sha256_hex(entry["content_json"].encode("ascii"))
        doc["snapshot_id"] = snapshot_id([(entry["objective_key"], entry["trail_sha256"])])
        self.path.write_bytes(canonical(doc))
        export.load(self.path)
        self.annotate(["r1"], objective_id="wrong-id")
        result = publication.refresh(self.cfg, self.path, NOW)
        self.assertEqual(result["entries"][0]["content_json"], entry["content_json"])
        self.assertEqual(result["entries"][0]["observation"]["attempt_status"], "failed")

    def test_boolean_schema_version_rejected(self):
        doc = self.setup_export()
        doc["schema_version"] = True
        with self.assertRaises(export.ExportError):
            export.verify(canonical(doc))

    def test_invalid_generated_export_is_publication_failure(self):
        self.setup_export()
        old = self.path.read_bytes()
        with patch.object(publication.builder, "build_entries", return_value=[{}]):
            with self.assertRaises(publication.PublicationError) as cm:
                publication.refresh(self.cfg, self.path, NOW)
        self.assertEqual(cm.exception.exit_code, 7)
        self.assertEqual(self.path.read_bytes(), old)

    @unittest.skipUnless(os.name == "nt", "actual Windows sharing semantics")
    def test_windows_conflicting_reader_persistent_and_transient(self):
        self.setup_export()
        old = self.path.read_bytes()
        code = """import ctypes,sys,time
from ctypes import wintypes
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
k.CreateFileW.restype=wintypes.HANDLE
k.CloseHandle.argtypes=[wintypes.HANDLE]
h=k.CreateFileW(sys.argv[1],0x80000000,int(sys.argv[3]),None,3,0,None)
if h==wintypes.HANDLE(-1).value: raise ctypes.WinError(ctypes.get_last_error())
print('held',flush=True)
time.sleep(float(sys.argv[2]))
k.CloseHandle(h)
"""
        child = self.child(code, self.path, 60, 3)
        self.assertEqual(child.stdout.readline().strip(), b"held")
        proc = run("refresh", "--config", str(self.cfg.path), "--export", str(self.path), "--now", NOW)
        self.assertEqual(proc.returncode, 7, proc.stderr)
        self.assertEqual(self.path.read_bytes(), old)
        child.kill()
        child.communicate(timeout=10)
        child = self.child(code, self.path, .8, 3)
        self.assertEqual(child.stdout.readline().strip(), b"held")
        publication.refresh(self.cfg, self.path, NOW)
        child.communicate(timeout=10)
        child = self.child(code, self.path, 60, 0)
        self.assertEqual(child.stdout.readline().strip(), b"held")
        calls = self.owner_calls()
        proc = run("refresh", "--config", str(self.cfg.path), "--export", str(self.path), "--now", NOW)
        self.assertEqual(proc.returncode, 8, proc.stderr)
        self.assertEqual(self.owner_calls(), calls)
        self.assertEqual(run("show", "--export", str(self.path), "--all").returncode, 8)
