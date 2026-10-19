import contextlib
import io
import json

from tests.helpers import Workspace, write_json
from tests.test_handoffs import NOTE
from taskmaster import handoff_hook, publication


class HandoffHookTests(Workspace):
    def setUp(self):
        super().setUp()
        self.root = self.tmp/'project'
        self.note = self.root/'.claude'/'handoff.md'
        self.note.parent.mkdir(parents=True)
        self.note.write_text(NOTE, encoding='utf-8')
        self.annotate([])
        cfg = self.config()
        settings = {'schema_version': 1, 'source': str(self.store), 'state_dir': str(self.tmp/'state'),
                    'export': str(self.tmp/'active'/'export.json'), 'interval_seconds': 30,
                    'rules': [{'namespace': 'ops', 'id': 'obj-1', 'workspace': str(self.root),
                               'record': 'request-summary.json', 'anchors': [], 'project_ids': ['repo']}],
                    'handoffs': [{'objective': 'ops/obj-1', 'provider': 'claude', 'path': str(self.note)}]}
        write_json(self.tmp/'watch.json', settings)
        cfg.write_text('watch = "watch.json"\n' + cfg.read_text())
        self.cfg = cfg

    def run_hook(self, payload):
        out = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode()))
        with contextlib.redirect_stdout(out), contextlib.ExitStack() as stack:
            import sys
            stack.enter_context(_swap(sys, 'stdin', stdin))
            self.assertEqual(handoff_hook.main(['--config', str(self.cfg)]), 0)
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def projection(self):
        return json.loads((self.tmp/'active'/'handoffs.json').read_text())

    def test_claude_write_records_the_note_at_once(self):
        result = self.run_hook({'tool_name': 'Write', 'tool_input': {'file_path': str(self.note)}})
        self.assertIn('Handoff saved', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.projection()['trails'][0]['status'], 'saved')
        ledger = json.loads((self.tmp/'state'/'handoff-ledger.json').read_text())
        self.assertEqual(len(ledger['events']), 1)

    def test_codex_patch_header_is_found_relative_to_cwd(self):
        patch = '*** Begin Patch\n*** Update File: .claude/handoff.md\n@@\n-x\n+y\n*** End Patch'
        result = self.run_hook({'tool_name': 'apply_patch', 'cwd': str(self.root), 'tool_input': {'command': patch}})
        self.assertIn('Handoff saved', result['hookSpecificOutput']['additionalContext'])

    def test_invalid_note_is_fed_back_and_not_claimed_saved(self):
        self.note.write_text(NOTE.replace('## What Was Done', '## What Was Done (PRs #10-#23)'), encoding='utf-8')
        result = self.run_hook({'tool_name': 'Edit', 'tool_input': {'file_path': str(self.note)}})
        self.assertEqual(result['decision'], 'block')
        self.assertIn('## What Was Done', result['reason'])
        self.assertFalse((self.tmp/'active'/'handoffs.json').exists())

    def test_unwatched_note_starts_a_workspace_trail_and_is_recorded(self):
        other = self.tmp/'elsewhere'/'.claude'/'handoff.md'
        other.parent.mkdir(parents=True)
        other.write_text(NOTE, encoding='utf-8')
        result = self.run_hook({'tool_name': 'Write', 'tool_input': {'file_path': str(other)}})
        message = result['hookSpecificOutput']['additionalContext']
        self.assertIn('Started Taskmaster trail elsewhere/workspace for this workspace.', message)
        self.assertIn('elsewhere/workspace: Handoff saved', message)
        trails = {t['objective']: t['status'] for t in self.projection()['trails']}
        self.assertEqual(trails['elsewhere/workspace'], 'saved')
        settings = json.loads((self.tmp/'watch.json').read_text())
        self.assertEqual(sorted(b['provider'] for b in settings['handoffs'] if b['objective'] == 'elsewhere/workspace'),
                         ['claude', 'codex'])
        self.assertIn('namespace = "elsewhere"', self.cfg.read_text())
        again = self.run_hook({'tool_name': 'Write', 'tool_input': {'file_path': str(other)}})
        self.assertNotIn('Started', again['hookSpecificOutput']['additionalContext'])
        self.assertEqual(json.loads((self.tmp/'watch.json').read_text()), settings)

    def test_other_files_are_ignored(self):
        self.assertIsNone(self.run_hook({'tool_name': 'Write', 'tool_input': {'file_path': str(self.root/'README.md')}}))
        self.assertIsNone(self.run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}}))

    def test_busy_watcher_is_reported_without_overwriting_the_projection(self):
        (self.tmp/'state').mkdir()
        with publication.writer_lock(self.tmp/'state'/'cycle'):
            result = self.run_hook({'tool_name': 'Write', 'tool_input': {'file_path': str(self.note)}})
        self.assertIn('mid-cycle', result['hookSpecificOutput']['additionalContext'])
        self.assertFalse((self.tmp/'active'/'handoffs.json').exists())


@contextlib.contextmanager
def _swap(owner, name, value):
    old = getattr(owner, name)
    setattr(owner, name, value)
    try:
        yield
    finally:
        setattr(owner, name, old)
