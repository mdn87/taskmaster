"""Continuous discovery must preserve membership and operator corrections."""
import json
import hashlib
import io
import threading
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests.helpers import Workspace, make_run, write_json, NOW
from taskmaster import watch
from taskmaster.commands import execute


class WatchWorkspace(Workspace):
    def setUp(self):
        super().setUp()
        self.annotate(['seed'])
        self.config_path = self.config()
        self.settings = write_json(self.tmp / 'watch.json', {
            'schema_version': 1, 'source': str(self.store), 'state_dir': 'watch-state',
            'export': 'export.json', 'interval_seconds': 30,
            'rules': [{'namespace': 'ops', 'id': 'obj-1', 'workspace': '/repo/lane',
                       'record': 'request-summary.json', 'anchors': [], 'project_ids': ['lane']}]
        })

    def run_record(self, identity='seed', workspace='/repo/lane', **kwargs):
        path = make_run(self.store, identity, **kwargs)
        write_json(path / 'request-summary.json', {'task_sha256': 'a' * 64, 'workspace': workspace})
        return path


class WatchTests(WatchWorkspace):
    def test_new_run_is_admitted_only_by_exact_scope(self):
        self.run_record()
        first = watch.tick(self.config_path, self.settings, now=NOW)
        self.run_record('new')
        self.run_record('other', '/repo/lane-other')
        second = watch.tick(self.config_path, self.settings, now='2026-10-17T12:01:00Z')
        self.assertEqual(first['memberships']['ops/obj-1'], ['seed'])
        self.assertEqual(second['memberships']['ops/obj-1'], ['new', 'seed'])
        self.assertEqual(second['failed_objectives'], 0)
        self.assertTrue((self.tmp / 'trails-report.md').exists())

    def test_conflicting_owner_binding_is_quarantined(self):
        self.run_record()
        self.run_record('wrong', objective_id='someone-else')
        status = watch.tick(self.config_path, self.settings, now=NOW)
        self.assertEqual(status['memberships']['ops/obj-1'], ['seed'])
        self.assertEqual(status['quarantine'][0]['run_id'], 'wrong')

    def test_duplicate_rule_match_is_not_guessed(self):
        self.run_record()
        settings = json.loads(self.settings.read_text())
        settings['rules'].append(dict(settings['rules'][0], id='obj-2'))
        write_json(self.settings, settings)
        with self.assertRaises(watch.WatchError):
            watch.tick(self.config_path, self.settings, now=NOW)

    def test_private_subtrees_are_not_imported(self):
        path = self.run_record()
        write_json(path / 'child-output' / 'result.json', {'secret': 'must not copy'})
        status = watch.tick(self.config_path, self.settings, now=NOW)
        self.assertFalse(any('child-output' in p.as_posix() for p in Path(status['generation']).rglob('*')))

    def test_missing_previously_verified_run_retains_failed_trail(self):
        path = self.run_record()
        watch.tick(self.config_path, self.settings, now=NOW)
        path.rename(self.tmp / 'moved-seed')
        status = watch.tick(self.config_path, self.settings, now='2026-10-17T12:01:00Z')
        self.assertEqual(status['failed_objectives'], 1)
        doc = json.loads((self.tmp / 'export.json').read_text())
        self.assertIsNotNone(doc['entries'][0]['content_json'])
        self.assertEqual(doc['entries'][0]['observation']['attempt_status'], 'failed')

    def test_manual_session_choice_survives_detection_and_can_be_cleared(self):
        rule = {'namespace': 'ops', 'id': 'obj-1', 'project_ids': ['lane']}
        other = {'namespace': 'ops', 'id': 'obj-2', 'project_ids': ['other']}
        session = {'runId': 'dias:codex-hook:abc', 'projectIds': ['lane']}
        detected = watch.session_bindings([session], [rule, other], {})[0]
        self.assertEqual(detected['objective'], 'ops/obj-1')
        corrected = watch.session_bindings([session], [rule, other], {session['runId']: 'ops/obj-2'})[0]
        self.assertEqual(corrected['objective'], 'ops/obj-2')
        self.assertEqual(corrected['provenance'], 'operator')
        unbound = watch.session_bindings([session], [rule], {session['runId']: None})[0]
        self.assertIsNone(unbound['objective'])
        self.assertEqual(unbound['provenance'], 'operator')

    def test_ambiguous_session_projects_remain_unassigned(self):
        rows = watch.session_bindings([{'runId': 'session-1', 'projectIds': ['lane']}], [
            {'namespace': 'ops', 'id': 'a', 'project_ids': ['lane']},
            {'namespace': 'ops', 'id': 'b', 'project_ids': ['lane']}], {})
        self.assertIsNone(rows[0]['objective'])
        self.assertEqual(rows[0]['provenance'], 'ambiguous')

    def test_exact_workspace_key_disambiguates_shared_project(self):
        rows = watch.session_bindings([{'runId': 'session-1', 'projectIds': ['lugos'], 'workspaceKeys': ['a' * 64]}], [
            {'namespace': 'lugos', 'id': 'one', 'project_ids': ['lugos'], 'workspace_keys': ['a' * 64]},
            {'namespace': 'lugos', 'id': 'two', 'project_ids': ['lugos'], 'workspace_keys': ['b' * 64]}], {})
        self.assertEqual(rows[0]['objective'], 'lugos/one')

    def test_exit_feed_is_published_even_when_no_live_session_was_observed(self):
        sid = 'finished-between-polls'
        rid = 'dias:codex-hook:' + hashlib.sha256(sid.encode()).hexdigest()
        settings = {**json.loads(self.settings.read_text()), 'state_dir': str(self.tmp / 'state'),
                    'export': str(self.tmp / 'export.json'), 'dias_url': 'http://127.0.0.1:4175'}
        ended = {'runId': rid, 'projectIds': ['lane'], 'workspaceKeys': [],
                 'resume': {'provider': 'codex', 'session_id': sid}, 'observedAt': NOW}
        feed = {'schemaVersion': 1, 'sessions': [], 'endedSessions': [ended]}
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(feed).encode())):
            result = watch.project_sessions(settings, NOW)
        self.assertEqual(result['sessions'], [])
        row = json.loads((self.tmp / 'registry.json').read_text())['sessions'][0]
        self.assertEqual(row['session_id'], sid)
        self.assertEqual(row['objective'], 'ops/obj-1')
        self.assertFalse(row['active'])
        feed['endedSessions'].append(ended)
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(feed).encode())):
            self.assertEqual(watch.project_sessions(settings, NOW)['status'], 'unavailable')
        self.assertEqual(json.loads((self.tmp / 'registry.json').read_text())['sessions'][0], row)

    def test_slash_correction_persists_and_auto_restores_detection(self):
        class Feed(BaseHTTPRequestHandler):
            sessions = [{'runId': 'dias:codex-hook:test', 'projectIds': ['lane']}]
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'schemaVersion': 1, 'sessions': self.sessions}).encode())
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Feed)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        settings = json.loads(self.settings.read_text())
        settings['dias_url'] = f'http://127.0.0.1:{server.server_port}'
        write_json(self.settings, settings)
        self.config_path.write_text('watch = "watch.json"\n' + self.config_path.read_text())
        result = execute(self.config_path, '/taskmaster clear --session dias:codex-hook:test')
        self.assertIsNone(result['sessions'][0]['objective'])
        self.assertEqual(result['sessions'][0]['provenance'], 'operator')
        self.assertIsNone(execute(self.config_path, '/taskmaster sessions')['sessions'][0]['objective'])
        result = execute(self.config_path, '/taskmaster use ops/obj-1 --session dias:codex-hook:test')
        self.assertEqual(result['sessions'][0]['provenance'], 'operator')
        result = execute(self.config_path, '/taskmaster auto --session dias:codex-hook:test')
        self.assertEqual(result['sessions'][0]['objective'], 'ops/obj-1')
        self.assertEqual(result['sessions'][0]['provenance'], 'detected')
        saved = execute(self.config_path, '/taskmaster sessions --trail ops/obj-1')
        self.assertEqual(saved['schema'], 'taskmaster.registry/v1')
        self.assertEqual(len(saved['sessions']), 1)
        self.assertIsNone(saved['sessions'][0]['session_id'])
        self.assertEqual(len(saved['sessions'][0]['assignments']), 3)
        self.assertEqual(json.loads((self.tmp / 'registry.json').read_text()), saved)
        self.assertIn('resume ID unavailable', execute(self.config_path, '/taskmaster sessions --all --format report'))
        Feed.sessions = []
        historical = execute(self.config_path, '/taskmaster clear --session dias:codex-hook:test')
        self.assertFalse(historical['sessions'][0]['active'])
        self.assertIsNone(historical['sessions'][0]['objective'])
        self.assertEqual(len(execute(self.config_path, '/taskmaster sessions --trail ops/obj-1')['sessions']), 1)
        with self.assertRaises(watch.WatchError):
            execute(self.config_path, '/taskmaster use ops/obj-1 --session nonexistent')

    def test_real_overlapping_rules_quarantine_instead_of_enrolling(self):
        self.run_record()
        settings = json.loads(self.settings.read_text())
        settings['rules'].append(dict(settings['rules'][0], id='obj-2'))
        runs, memberships, _, quarantine = watch.discover(settings)
        self.assertEqual(runs, {})
        self.assertEqual(memberships, {})
        self.assertEqual(quarantine, [{'run_id': 'seed', 'reason': 'ambiguous membership'}])

    def test_owner_projection_cache_requires_identical_reverified_bytes(self):
        run = self.run_record()
        cache = {}
        watch.tick(self.config_path, self.settings, now=NOW, owner_cache=cache)
        calls = self.owner_calls().count('campaign outcome')
        watch.tick(self.config_path, self.settings, now='2026-10-17T12:01:00Z', owner_cache=cache)
        self.assertEqual(self.owner_calls().count('campaign outcome'), calls)
        result = json.loads((run / 'result.json').read_text())
        result['status'] = 'failed_transport'
        write_json(run / 'result.json', result)
        watch.tick(self.config_path, self.settings, now='2026-10-17T12:02:00Z', owner_cache=cache)
        self.assertEqual(self.owner_calls().count('campaign outcome'), calls + 1)

    def test_refresh_accepts_equivalent_export_path_and_explicit_recovery(self):
        self.run_record()
        self.config_path.write_text('watch = "watch.json"\n' + self.config_path.read_text())
        settings = json.loads(self.settings.read_text())
        (self.tmp / 'alias').mkdir()
        settings['export'] = 'alias/../export.json'
        write_json(self.settings, settings)
        (self.tmp / 'export.json').write_text('invalid previous export')
        command = [sys.executable, '-m', 'taskmaster', 'refresh', '--config', str(self.config_path),
                   '--export', str(self.tmp / 'export.json'), '--now', NOW]
        denied = subprocess.run(command, capture_output=True)
        self.assertEqual(denied.returncode, 4)
        recovered = subprocess.run(command + ['--recover-invalid-export'], capture_output=True)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)


class ServeLogTests(WatchWorkspace):
    """The daemon leaves a trail of its own lifetime so an unexplained exit is diagnosable."""

    def serve_until_sleep(self, tick_side_effect=None):
        class Stop(Exception):
            pass
        with patch.object(watch, 'tick', side_effect=tick_side_effect) as ticked, \
                patch.object(watch.time, 'sleep', side_effect=Stop):
            with self.assertRaises(Stop):
                watch.serve(self.config_path, self.settings)
        self.assertEqual(ticked.call_count, 1)
        return (self.tmp / 'watch-state' / 'watch.log').read_text(encoding='ascii').splitlines()

    def test_serve_logs_start_and_exit(self):
        lines = self.serve_until_sleep()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z started pid=\d+ interval=30s$')
        self.assertRegex(lines[1], r'Z stopping pid=\d+ Stop: $')

    def test_serve_logs_each_failed_tick(self):
        lines = self.serve_until_sleep(tick_side_effect=watch.WatchError('boom'))
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[1], r'Z tick failed pid=\d+ WatchError: boom$')
        self.assertEqual(json.loads((self.tmp / 'watch-state' / 'last-error.json').read_text())['error'], 'WatchError')
