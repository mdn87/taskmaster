import copy
import json
from pathlib import Path

from tests.helpers import Workspace, write_json
from taskmaster import handoffs, watch

NOW = '2026-10-17T12:00:00Z'
NOTE = '''last_updated: 2026-10-17T11:00:00Z
session_summary: A private summary that must not be published.

## What Was Done
- Completed the current change.
## Current State
Tests passed.
## Next Up
- Resume the remaining work.
## Gotchas
- None.
'''


class HandoffTests(Workspace):
    def setUp(self):
        super().setUp()
        self.root = self.tmp/'project'
        self.note = self.root/'.Codex'/'handoff.md'
        self.note.parent.mkdir(parents=True)
        self.note.write_text(NOTE, encoding='utf-8')
        self.settings = {'state_dir': str(self.tmp/'state'), 'export': str(self.tmp/'active'/'export.json'),
                         'interval_seconds': 30, 'rules': [{'namespace': 'ops', 'id': 'one', 'workspace': str(self.root)}],
                         'handoffs': [{'objective': 'ops/one', 'provider': 'codex', 'path': str(self.note)}]}

    def test_saved_note_adds_one_event_and_projects_no_private_text(self):
        first = handoffs.publish(self.settings, NOW)
        second = handoffs.publish(self.settings, '2026-10-17T12:01:00Z')
        self.assertEqual(first['trails'][0]['status'], 'saved')
        ledger = json.loads((self.tmp/'state'/'handoff-ledger.json').read_text())
        self.assertEqual(len(ledger['events']), 1)
        self.assertEqual(first['trails'], second['trails'])
        self.assertNotIn('private summary', json.dumps(ledger))
        self.assertNotIn(str(self.root), json.dumps(first))
        self.assertIn('Handoff saved', handoffs.render(first))
        self.note.write_text(NOTE.replace('current change', 'second change'), encoding='utf-8')
        handoffs.publish(self.settings, '2026-10-17T12:02:00Z')
        ledger = json.loads((self.tmp/'state'/'handoff-ledger.json').read_text())
        self.assertEqual(len(ledger['events']), 2)
        self.assertEqual(ledger['events'][1]['previous_event_id'], ledger['events'][0]['event_id'])

    def test_slow_cycle_widens_the_freshness_window(self):
        base = 2 * self.settings['interval_seconds'] + 10
        self.assertEqual(handoffs.publish(self.settings, NOW)['max_age_seconds'], base)
        self.assertEqual(handoffs.publish(self.settings, NOW, 37.2)['max_age_seconds'], base + 76)
        self.assertEqual(handoffs.publish(self.settings, NOW, 10**6)['max_age_seconds'], 7210)

    def test_invalid_missing_and_stale_notes_do_not_claim_saved(self):
        handoffs.publish(self.settings, NOW)
        self.note.write_text(NOTE.replace('## Next Up', '## Missing section'), encoding='utf-8')
        self.assertEqual(handoffs.publish(self.settings, NOW)['trails'][0]['status'], 'invalid')

    def test_extreme_dates_and_duplicate_headers_cannot_abort_or_claim_saved(self):
        for timestamp in ('0001-01-01T00:00:00+01:00', '9999-12-31T23:59:59-01:00'):
            self.note.write_text(NOTE.replace('2026-10-17T11:00:00Z', timestamp), encoding='utf-8')
            self.assertEqual(handoffs.publish(self.settings, NOW)['trails'][0]['status'], 'invalid')
        self.note.write_text('last_updated: 2026-10-17T11:00:00Z\n'+NOTE, encoding='utf-8')
        self.assertEqual(handoffs.publish(self.settings, NOW)['trails'][0]['status'], 'invalid')
        self.note.rename(self.note.with_suffix('.saved'))
        self.assertEqual(handoffs.publish(self.settings, NOW)['trails'][0]['status'], 'missing')
        self.note.write_text(NOTE.replace('2026-10-17', '2026-10-01'), encoding='utf-8')
        self.assertEqual(handoffs.publish(self.settings, NOW)['trails'][0]['status'], 'stale')
        self.note.write_text(NOTE.replace('2026-10-17', '2026-10-18'), encoding='utf-8')
        self.assertEqual(handoffs.publish(self.settings, NOW)['trails'][0]['status'], 'invalid')

    def test_corrupt_ledger_is_retained_and_clears_current_flag(self):
        handoffs.publish(self.settings, NOW)
        ledger = self.tmp/'state'/'handoff-ledger.json'
        raw = ledger.read_bytes().replace(b'ops/one', b'ops/two')
        ledger.write_bytes(raw)
        value = handoffs.publish(self.settings, NOW)
        self.assertEqual(value['status'], 'unavailable')
        self.assertEqual(ledger.read_bytes(), raw)
        self.assertEqual(value['trails'], [])

    def test_reader_rejects_expired_and_private_extended_projection(self):
        handoffs.publish(self.settings, NOW)
        path = self.tmp/'active'/'handoffs.json'
        self.assertEqual(handoffs.read_projection(path, NOW)['status'], 'current')
        self.assertEqual(handoffs.read_projection(path, '2026-10-17T12:02:00Z')['status'], 'unavailable')
        value = json.loads(path.read_text())
        value['trails'][0]['sources'][0]['body'] = 'private'
        write_json(path, value)
        self.assertEqual(handoffs.read_projection(path, NOW)['status'], 'unavailable')

    def test_config_requires_exact_workspace_and_unique_provider(self):
        handoffs.configure(self.settings, self.tmp)
        wrong = copy.deepcopy(self.settings)
        wrong['handoffs'][0]['path'] = str(self.tmp/'other'/'.Codex'/'handoff.md')
        with self.assertRaises(ValueError):
            handoffs.configure(wrong, self.tmp)
        duplicate = copy.deepcopy(self.settings)
        duplicate['handoffs'].append(duplicate['handoffs'][0])
        with self.assertRaises(ValueError):
            handoffs.configure(duplicate, self.tmp)

    def test_watcher_publishes_handoff_when_note_changes(self):
        self.annotate([])
        cfg = self.config()
        settings = copy.deepcopy(self.settings)
        settings.update(schema_version=1, source=str(self.store))
        settings['rules'][0].update(id='obj-1', record='request-summary.json', anchors=[], project_ids=['repo'])
        settings['handoffs'][0]['objective'] = 'ops/obj-1'
        settings_path = write_json(self.tmp/'watch.json', settings)
        watch.tick(cfg, settings_path, now=NOW)
        path = self.tmp/'active'/'handoffs.json'
        self.assertEqual(handoffs.read_projection(path, NOW)['trails'][0]['status'], 'saved')
        self.note.write_text('incomplete save', encoding='utf-8')
        watch.tick(cfg, settings_path, now=NOW)
        self.assertEqual(handoffs.read_projection(path, NOW)['trails'][0]['status'], 'invalid')
