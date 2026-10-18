"""Session handoff context shares the report's immutable delivery boundary."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from taskmaster import morning_report


NOW = '2026-10-17T12:00:00Z'
BLOCK = b'taskmaster trail report v1\nobjectives: 1\n'
PROJECTION = {
    'schema': 'taskmaster.handoffs/v1', 'evaluated_at': NOW,
    'max_age_seconds': 70, 'status': 'current',
    'trails': [{'objective': 'cirra/coordination-layer', 'status': 'saved',
                'sources': [{'provider': 'codex', 'status': 'saved',
                             'note_sha256': 'a' * 64,
                             'note_updated_at': '2026-10-17T11:00:00Z',
                             'event_id': 'b' * 64}]}],
}


class HandoffMorningReport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.export = Path(self.temp.name) / 'export.json'
        self.export.write_bytes(b'unchanged execution evidence')
        self.sidecar = self.export.with_name('handoffs.json')
        self.renderer = patch.object(morning_report, 'render_once', return_value=(0, BLOCK))
        self.renderer.start()
        self.addCleanup(self.renderer.stop)

    def write(self, value):
        self.sidecar.write_text(json.dumps(value), encoding='utf-8')

    def test_saved_handoff_is_inside_verified_block_without_changing_export(self):
        self.write(PROJECTION)
        before = hashlib.sha256(self.export.read_bytes()).hexdigest()
        prepared = morning_report.prepare(self.export, NOW)
        self.assertTrue(prepared.block.startswith(BLOCK))
        self.assertIn(b'cirra/coordination-layer', prepared.block)
        self.assertIn(b'saved', prepared.block)
        delivered = morning_report.compose(b'Inbox empty.\n', prepared)
        self.assertTrue(morning_report.verify_document(delivered, prepared))
        self.assertFalse(morning_report.verify_document(delivered.replace(b'saved', b'missing'), prepared))
        self.assertEqual(hashlib.sha256(self.export.read_bytes()).hexdigest(), before)
        self.assertNotIn(str(self.sidecar).encode(), delivered)

    def test_absent_sidecar_preserves_existing_report_bytes(self):
        self.assertEqual(morning_report.prepare(self.export, NOW).block, BLOCK)

    def test_sidecar_cannot_repair_invalid_execution_renderer_output(self):
        self.write(PROJECTION)
        with patch.object(morning_report, 'render_once', return_value=(0, BLOCK.rstrip())):
            prepared = morning_report.prepare(self.export, NOW)
        self.assertEqual(prepared.error, 'renderer_invalid_output')
        self.assertIsNone(prepared.block)

    def test_unreadable_sidecar_preserves_execution_report_and_hides_diagnostics(self):
        with patch.object(Path, 'lstat', side_effect=PermissionError('private path')):
            prepared = morning_report.prepare(self.export, NOW)
        self.assertTrue(prepared.block.startswith(BLOCK))
        self.assertIn(b'handoff: unavailable', prepared.block)
        self.assertNotIn(b'private path', prepared.block)

    def test_invalid_stale_future_and_private_fields_cannot_claim_saved(self):
        stale = copy.deepcopy(PROJECTION)
        stale['evaluated_at'] = '2026-10-17T11:58:00Z'
        future = copy.deepcopy(PROJECTION)
        future['evaluated_at'] = '2026-10-17T12:01:00Z'
        private = copy.deepcopy(PROJECTION)
        private['trails'][0]['session_id'] = 'private-session-id'
        private['trails'][0]['note_body'] = 'private note prose'
        for value in ([], stale, future, private):
            with self.subTest(value=value):
                self.write(value)
                prepared = morning_report.prepare(self.export, NOW)
                self.assertIsNone(prepared.error)
                self.assertTrue(prepared.block.startswith(BLOCK))
                self.assertIn(b'unavailable', prepared.block)
                self.assertNotIn(b'saved', prepared.block)
                self.assertNotIn(b'private', prepared.block)

    def test_refresh_after_prepare_cannot_change_verified_handoff(self):
        self.write(PROJECTION)
        prepared = morning_report.prepare(self.export, NOW)
        self.sidecar.write_bytes(b'invalid replacement')
        output = self.export.with_name('report.md')
        self.assertTrue(morning_report.deliver(b'Inbox empty.\n', output, prepared))
        self.assertIn(b'saved', output.read_bytes())
        self.assertTrue(morning_report.verify_document(output.read_bytes(), prepared))
