import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from taskmaster import backfill
from tests.helpers import Workspace, write_json

SID = '12345678-1234-4321-8765-123456789abc'
NOW = '2026-10-17T21:00:00Z'

class BackfillTests(unittest.TestCase):
    def test_exact_metadata_workspace_and_identity_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / (SID + '.jsonl')
            cwd = str(Path(d) / 'repo')
            paths = {backfill.watch.normalized(cwd): ['ops/one']}
            records = [dict(sessionId=SID, cwd=cwd, timestamp='2026-10-15T12:00:00.000Z', type='user', message={'content': 'do not retain'}),
                       dict(sessionId=SID, cwd=cwd, timestamp='2026-10-15T12:01:00.000Z', type='assistant')]
            p.write_text('\n'.join(json.dumps(r) for r in records))
            candidates, gaps = backfill.claude_project(Path(d), paths, {})
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]['session_id'], SID)
            self.assertNotIn('do not retain', json.dumps(candidates))
            self.assertEqual(gaps, [])
            with patch.object(backfill, 'MAX_TOTAL', 16):
                with self.assertRaises(backfill.watch.WatchError):
                    backfill.claude_project(Path(d), paths, {})
            p.write_text(json.dumps(dict(records[0], sessionId='someone-else')))
            self.assertEqual(backfill.claude_project(Path(d), paths, {})[0], [])

    def test_ambiguous_membership_is_rejected(self):
        candidate = {'provider': 'claude', 'session_id': SID, 'objective': 'ops/one', 'first': NOW, 'last': NOW, 'evidence': []}
        accepted, conflicts = backfill.resolve([candidate, dict(candidate, objective='ops/two')])
        self.assertEqual(accepted, [])
        self.assertEqual(len(conflicts), 1)

    def test_import_is_idempotent_and_preserves_operator_clear(self):
        candidate = {'provider': 'claude', 'session_id': SID, 'objective': 'ops/one', 'first': '2026-10-15T12:00:00Z', 'last': '2026-10-15T12:01:00Z', 'evidence': []}
        value = backfill.merge(None, [candidate], {}, NOW, 70)
        self.assertFalse(value['sessions'][0]['active'])
        self.assertEqual(value['sessions'][0]['session_id'], SID)
        twice = backfill.merge(value, [candidate], {}, NOW, 70)
        self.assertEqual(value, twice)
        rid = value['sessions'][0]['run_id']
        cleared = backfill.merge(None, [candidate], {rid: None}, NOW, 70)
        self.assertIsNone(cleared['sessions'][0]['objective'])
        self.assertEqual(cleared['sessions'][0]['assignments'][0]['ended_at'], NOW)
        moved = backfill.merge(None, [candidate], {rid: 'ops/two'}, NOW, 70)
        self.assertEqual(moved['sessions'][0]['objective'], 'ops/two')
        self.assertEqual(moved['sessions'][0]['assignments'][-1]['objective'], 'ops/one')
        self.assertEqual(moved, backfill.merge(moved, [candidate], {rid: 'ops/two'}, NOW, 70))

    def test_cwd_prefix_and_invalid_timestamp_do_not_match(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/(SID+'.jsonl')
            cwd = str(Path(d) / 'repo')
            paths = {backfill.watch.normalized(cwd): ['ops/one']}
            p.write_text(json.dumps(dict(sessionId=SID,cwd=cwd+'-other',timestamp=NOW)))
            self.assertEqual(backfill.claude_project(Path(d), paths, {})[0], [])
            p.write_text(json.dumps(dict(sessionId=SID,cwd=cwd,timestamp='unknown')))
            self.assertEqual(backfill.claude_project(Path(d), paths, {})[0], [])

    def test_codex_requires_agreement_between_database_and_rollout(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cwd, rollout, db = str(root/'repo'), root/'rollout.jsonl', root/'state.sqlite'
            paths = {backfill.watch.normalized(cwd): ['ops/one']}
            with sqlite3.connect(db) as connection:
                connection.execute('CREATE TABLE threads (id,cwd,rollout_path,created_at,updated_at)')
                connection.execute('INSERT INTO threads VALUES (?,?,?,?,?)', (SID,cwd,str(rollout),1760000000,1760000060))
            connection.close()
            rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': SID, 'cwd': cwd}})+'\n')
            candidates, gaps = backfill.codex_database(db, paths, {})
            self.assertEqual(len(candidates), 1)
            self.assertEqual(gaps, [])
            with patch.object(backfill, 'MAX_TOTAL', 16):
                with self.assertRaises(backfill.watch.WatchError):
                    backfill.codex_database(db, paths, {})
            rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'wrong-id', 'cwd': cwd}})+'\n')
            candidates, gaps = backfill.codex_database(db, paths, {})
            self.assertEqual(candidates, [])
            self.assertEqual(len(gaps), 1)

    def test_duplicate_metadata_and_oversized_records_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/(SID+'.jsonl')
            p.write_text('{"sessionId":"wrong","sessionId":'+json.dumps(SID)+'}')
            self.assertEqual(len(backfill.claude_project(Path(d), {}, {})[1]), 1)
            with patch.object(backfill, 'MAX_LINE', 16):
                self.assertEqual(len(backfill.claude_project(Path(d), {}, {})[1]), 1)


class BackfillIntegrationTests(Workspace):
    def test_preview_does_not_write_apply_refreshes_and_retains_history(self):
        self.annotate([])
        cfg = self.config()
        cfg.write_text('watch = "watch.json"\n' + cfg.read_text())
        cwd = str(self.tmp/'repo')
        settings = {'schema_version': 1, 'source': str(self.store), 'state_dir': 'state',
                    'export': 'export.json', 'interval_seconds': 30,
                    'rules': [{'namespace': 'ops', 'id': 'obj-1', 'workspace': cwd,
                               'record': 'request-summary.json', 'anchors': [], 'project_ids': ['repo']}]}
        write_json(self.tmp/'watch.json', settings)
        project = self.tmp/'claude-project'
        project.mkdir()
        (project/(SID+'.jsonl')).write_text(json.dumps({'sessionId': SID, 'cwd': cwd, 'timestamp': NOW}))
        preview = backfill.run(cfg, [project], None)
        self.assertFalse(preview['applied'])
        self.assertFalse((self.tmp/'state').exists())
        applied = backfill.run(cfg, [project], None, apply=True)
        self.assertTrue(applied['applied'])
        self.assertTrue((Path(applied['receipt'])/'verified-sources.json').exists())
        value = json.loads((self.tmp/'registry.json').read_text())
        self.assertEqual(value['sessions'][0]['session_id'], SID)
        self.assertIn(SID, (self.tmp/'session-registry-report.md').read_text())
        self.assertFalse((self.tmp/'export.json').exists())
        backfill.run(cfg, [project], None, apply=True)
        again = json.loads((self.tmp/'registry.json').read_text())
        self.assertEqual(value['sessions'], again['sessions'])
        with patch.object(backfill.watch, 'project_sessions', side_effect=OSError('simulated publication failure')):
            with self.assertRaisesRegex(backfill.watch.WatchError, 'sessions imported; consumer refresh failed; receipt:'):
                backfill.run(cfg, [project], None, apply=True)
        outcomes = [json.loads(p.read_text()) for p in (self.tmp/'state'/'backfills').glob('*/result.json')]
        self.assertTrue(any(r['applied'] and r['consumer_refresh'] == 'failed' for r in outcomes))
        self.assertEqual(value['sessions'], json.loads((self.tmp/'state'/'session-registry.json').read_text())['sessions'])
