"""Manual refreshes and handoffs must coexist with the supervised watcher."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
from datetime import timezone
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from taskmaster import builder, config, handoffs, publication, remote_owner, watch
from tests.helpers import NOW, REPO, Workspace, make_run, write_json
from tests.test_remote_context import NOTE, NOW as NOTE_NOW
from tests.test_watch import WatchWorkspace

LATER = '2026-10-17T12:01:00Z'


class CoordinatedRefreshTests(WatchWorkspace):
    def test_cli_refresh_wakes_a_real_long_interval_daemon(self):
        self.run_record()
        settings = watch.read_json(self.settings)
        settings['interval_seconds'] = 3600
        write_json(self.settings, settings)
        self.config_path.write_text('watch = "watch.json"\n' + self.config_path.read_text())
        env = {**os.environ, 'PYTHONPATH': str(REPO / 'src')}
        daemon = subprocess.Popen([sys.executable, '-m', 'taskmaster', 'watch', '--config', str(self.config_path)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            deadline = time.monotonic() + 10
            while not (self.tmp / 'watch-state' / 'status.json').exists():
                self.assertIsNone(daemon.poll(), 'watcher exited before publishing')
                self.assertLess(time.monotonic(), deadline, 'watcher never published')
                time.sleep(.05)
            requested = watch.timefmt.now()
            result = subprocess.run([sys.executable, '-m', 'taskmaster', 'slash', '--config', str(self.config_path),
                                     '/taskmaster refresh'], env=env, capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr)
            import json
            self.assertGreater(json.loads(result.stdout)['checked_at'], requested)
        finally:
            daemon.terminate()
            daemon.communicate(timeout=10)

    def test_new_request_wakes_a_long_interval_watcher(self):
        state = self.tmp / 'watch-state'
        write_json(state / 'refresh-request.json', {'requested_at': NOW})
        with patch.object(watch.timefmt, 'now', return_value=LATER), \
                patch.object(watch.time, 'sleep', side_effect=AssertionError('new request must wake the watcher')):
            watch.wait_for_refresh(state, NOW, 3600)

    def test_concurrent_requests_share_a_new_daemon_cycle(self):
        self.run_record()
        with patch.object(watch.timefmt, 'now', return_value=NOW):
            watch.tick(self.config_path, self.settings, now=NOW)
        before = self.owner_calls().count('campaign outcome')
        state = self.tmp / 'watch-state'
        readers = set()
        waiting = threading.Event()
        mutex = threading.Lock()
        original = watch.read_json

        def read(path, *args):
            result = original(path, *args)
            if Path(path) == state / 'status.json' and threading.current_thread().name.startswith('refresh'):
                with mutex:
                    readers.add(threading.get_ident())
                    if len(readers) == 2:
                        waiting.set()
            return result

        with publication.writer_lock(state / 'daemon'), patch.object(watch.timefmt, 'now', return_value=NOW), \
                patch.object(watch, 'read_json', side_effect=read), \
                ThreadPoolExecutor(2, thread_name_prefix='refresh') as pool:
            requests = [pool.submit(watch.request_refresh, self.config_path, self.settings, timeout=5) for _ in range(2)]
            self.assertTrue(waiting.wait(2), 'both callers should wait for fresh evidence')
            self.assertFalse(any(f.done() for f in requests), 'old status must not satisfy a new request')
            self.run_record('new')
            expected = watch.tick(self.config_path, self.settings, now=LATER)
            self.assertEqual([f.result(timeout=3) for f in requests], [expected, expected])
        self.assertEqual(self.owner_calls().count('campaign outcome'), before + 1)

    def test_busy_daemon_times_out_without_returning_old_success(self):
        self.run_record()
        watch.tick(self.config_path, self.settings, now=NOW)
        before = (self.tmp / 'export.json').read_bytes()
        with publication.writer_lock(self.tmp / 'watch-state' / 'daemon'), \
                patch.object(watch.timefmt, 'now', return_value=NOW):
            with self.assertRaisesRegex(publication.PublicationError, 'fresh watcher cycle'):
                watch.request_refresh(self.config_path, self.settings, timeout=.05)
        self.assertEqual((self.tmp / 'export.json').read_bytes(), before)

    def test_new_status_without_matching_export_is_not_success(self):
        self.run_record()
        status = watch.tick(self.config_path, self.settings, now=NOW)
        status['checked_at'] = LATER
        write_json(self.tmp / 'watch-state' / 'status.json', status)
        with publication.writer_lock(self.tmp / 'watch-state' / 'daemon'), \
                patch.object(watch.timefmt, 'now', return_value=NOW):
            with self.assertRaises(publication.PublicationError):
                watch.request_refresh(self.config_path, self.settings, timeout=.05)

    def test_without_daemon_a_request_publishes_directly(self):
        self.run_record()
        result = watch.request_refresh(self.config_path, self.settings)
        self.assertEqual(result['failed_objectives'], 0)
        self.assertEqual(result['memberships']['ops/obj-1'], ['seed'])


class RefreshVersionTests(Workspace):
    def test_owner_version_is_checked_once_per_build_not_per_objective(self):
        make_run(self.store, 'one')
        self.annotate(['one'])
        cfg = config.load(self.config())
        second = self.annotate(['one'], objective_id='obj-2', name='second.json')
        cfg = replace(cfg, objectives=[cfg.objectives[0], replace(cfg.objectives[0], id='obj-2', annotation=second)])
        entries = builder.build_entries(cfg, None, NOW)
        self.assertTrue(all(e['observation']['attempt_status'] == 'ok' for e in entries))
        self.assertEqual(self.owner_calls().count('--version'), 1)
        self.assertEqual(self.owner_calls().count('campaign outcome'), 2)
        self.set_behavior(version='unexpected')
        changed = builder.build_entries(cfg, None, LATER)
        self.assertTrue(all(e['observation']['attempt_status'] == 'failed' for e in changed))


class RegisteredHandoffTests(Workspace):
    def setUp(self):
        super().setUp()
        self.annotate([])
        self.cfg = self.config()
        self.state = self.tmp / 'state'
        self.settings = self.tmp / 'watch.json'
        write_json(self.settings, {
            'schema_version': 1, 'source': str(self.store), 'state_dir': str(self.state),
            'export': str(self.tmp / 'export.json'), 'interval_seconds': 30,
            'rules': [{'namespace': 'ops', 'id': 'obj-1', 'workspace': '/original',
                       'record': 'request-summary.json', 'anchors': [], 'project_ids': []}],
        })
        self.cfg.write_text('watch = "watch.json"\n' + self.cfg.read_text())
        self.collector = write_json(self.tmp / 'collector.json', {'notes': {}})
        self.owner = write_json(self.tmp / 'owner.json', {
            'source_root': 'C:/Work', 'client_root': '/Users/matt/Work',
            'workspace_root': str(self.tmp / 'mirrors'), 'collector_config': str(self.collector),
        })
        self.workspace = 'C:/Work/project'
        remote_owner.ensure_workspace(self.cfg, self.owner, self.workspace)

    def test_existing_handoff_is_acknowledged_during_a_refresh(self):
        before = (self.cfg.read_bytes(), self.settings.read_bytes(), self.collector.read_bytes())
        with publication.writer_lock(self.state / 'cycle'), \
                patch.object(remote_owner, 'refresh_inputs', side_effect=AssertionError('ack must not wait for collection')), \
                patch.object(remote_owner.timefmt, 'now', return_value=NOTE_NOW):
            result = remote_owner.handle(self.cfg, self.owner, {
                'action': 'handoff', 'workspace': self.workspace, 'provider': 'codex',
                'data': base64.b64encode(NOTE).decode(),
            })
        self.assertEqual(result['handoff_status'], 'recorded')
        self.assertEqual(result['note_sha256'], hashlib.sha256(NOTE).hexdigest())
        self.assertEqual(handoffs.load_ledger(self.state / 'handoff-ledger.json')['events'][0]['event_id'], result['event_id'])
        self.assertEqual((self.cfg.read_bytes(), self.settings.read_bytes(), self.collector.read_bytes()), before)

    def test_incomplete_binding_still_requires_registration_lock(self):
        collector = watch.read_json(self.collector)
        collector['notes'].clear()
        write_json(self.collector, collector)
        with publication.writer_lock(self.state / 'cycle'):
            with self.assertRaises(publication.PublicationError):
                remote_owner.ensure_workspace(self.cfg, self.owner, self.workspace)

    def test_old_collected_note_cannot_undo_an_acknowledged_revision(self):
        settings = watch.load_settings(self.settings, config.load(self.cfg))
        binding = next(b for b in settings['handoffs'] if b['provider'] == 'codex')
        settings['context_file'] = str(self.tmp / 'context.json')
        snapshot = {'schema': 'taskmaster.remote-context/v1', 'observed_at': NOTE_NOW,
                    'max_age_seconds': 45, 'timezone': 'UTC', 'dias_status': 'current',
                    'context': {'schemaVersion': 1, 'sessions': []},
                    'notes': {binding['path']: {'status': 'present', 'data': base64.b64encode(NOTE).decode()}}}
        write_json(Path(settings['context_file']), snapshot)
        with patch('taskmaster.remote_context.ZoneInfo', return_value=timezone.utc):
            handoffs.publish(settings, NOTE_NOW)
            changed = NOTE + b'Changed\n'
            with patch.object(remote_owner.timefmt, 'now', return_value='2026-10-19T22:00:10Z'):
                acknowledged = remote_owner.handle(self.cfg, self.owner, {
                    'action': 'handoff', 'workspace': self.workspace, 'provider': 'codex',
                    'data': base64.b64encode(changed).decode(),
                })
            result = handoffs.publish(settings, '2026-10-19T22:00:11Z')
            ledger = handoffs.load_ledger(self.state / 'handoff-ledger.json')
            self.assertEqual(len(ledger['events']), 2, 'old snapshot must not append a reversion')
            self.assertEqual(ledger['events'][-1]['event_id'], acknowledged['event_id'])
            source = next(t for t in result['trails'] if t['objective'] == binding['objective'])['sources'][0]
            self.assertEqual(source['status'], 'unavailable', 'acknowledgement is not current source evidence')
            snapshot['observed_at'] = '2026-10-19T22:00:12Z'
            snapshot['notes'][binding['path']]['data'] = base64.b64encode(changed).decode()
            write_json(Path(settings['context_file']), snapshot)
            result = handoffs.publish(settings, '2026-10-19T22:00:12Z')
            source = next(t for t in result['trails'] if t['objective'] == binding['objective'])['sources'][0]
            self.assertEqual((source['status'], source['event_id']), ('saved', acknowledged['event_id']))
            self.assertEqual(len(handoffs.load_ledger(self.state / 'handoff-ledger.json')['events']), 2)
            # A genuinely later source observation can record a real reversion.
            snapshot['observed_at'] = '2026-10-19T22:00:13Z'
            snapshot['notes'][binding['path']]['data'] = base64.b64encode(NOTE).decode()
            write_json(Path(settings['context_file']), snapshot)
            handoffs.publish(settings, '2026-10-19T22:00:13Z')
            self.assertEqual(len(handoffs.load_ledger(self.state / 'handoff-ledger.json')['events']), 3)


class BoundedLockTests(Workspace):
    def test_waits_for_short_contention_but_keeps_a_deadline(self):
        path = self.tmp / 'handoffs'
        acquired = threading.Event()
        release = threading.Event()

        def holder():
            with publication.writer_lock(path):
                acquired.set()
                release.wait(3)

        with ThreadPoolExecutor(1) as pool:
            held = pool.submit(holder)
            self.assertTrue(acquired.wait(1))
            try:
                with self.assertRaises(publication.PublicationError):
                    with publication.writer_lock(path, timeout=.05):
                        self.fail('must not enter a held lock')
                timer = threading.Timer(.05, release.set)
                timer.start()
                with publication.writer_lock(path, timeout=1):
                    self.assertTrue(release.is_set())
                timer.join()
            finally:
                release.set()
            held.result(timeout=1)
