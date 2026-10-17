"""Cross-review membership must not inherit dispatch-only outcome semantics."""
import hashlib
import json

from tests.helpers import Workspace, write_json
from tests.test_builder import build, only, links


class ReviewMembership(Workspace):
    def review(self):
        run = self.store / 'review-1'
        base = {'kind': 'plan', 'anchor_kind': 'plan', 'anchor_paths': ['plan.md'],
                'target_selectors': [], 'exclusions': [], 'files': [
                    {'path': 'plan.md', 'byte_count': 4, 'sha256': 'a' * 64, 'selectors': ['plan.md']}]}
        digest = hashlib.sha256(json.dumps(base, sort_keys=True, ensure_ascii=False,
                                           separators=(',', ':')).encode('utf-8')).hexdigest()
        manifest = dict(base, version=1, manifest_sha256=digest, workspace='example')
        manifest['files'][0]['roles'] = ['plan']
        write_json(run / 'subject-manifest.json', manifest)
        write_json(run / 'result.json', {'run_id': 'review-1', 'command_kind': 'cross-review',
                                       'status': 'succeeded', 'manifest_sha256': digest})
        write_json(run / 'state.json', {'run_id': 'review-1', 'status': 'succeeded'})
        self.annotate(['review-1'])
        return run

    def test_verified_membership_has_no_dispatch_claims(self):
        self.review()
        entry, content = only(build(self.config()))
        self.assertEqual(entry['observation']['attempt_status'], 'ok')
        run = links(content, 'has_run')[0]
        self.assertEqual(run['status'], 'resolved')
        self.assertEqual(run['asserted'], {})
        self.assertIn('subject-manifest', [e['source_ref']['record_type'] for e in run['evidence']])
        self.assertEqual(next(c['status'] for c in content['coverage']
                              if c['adapter'] == 'autowork.outcome'), 'unsupported')

    def test_changed_subject_with_old_digest_is_rejected(self):
        run = self.review()
        path = run / 'subject-manifest.json'
        doc = json.loads(path.read_text())
        doc['anchor_paths'] = ['different.md']
        write_json(path, doc)
        entry, _ = only(build(self.config()))
        self.assertEqual(entry['observation']['attempt_status'], 'failed')
        self.assertIn('required_input_invalid', entry['observation']['error_codes'])

    def test_result_must_bind_exact_manifest(self):
        run = self.review()
        doc = json.loads((run / 'result.json').read_text())
        doc['manifest_sha256'] = 'b' * 64
        write_json(run / 'result.json', doc)
        entry, _ = only(build(self.config()))
        self.assertEqual(entry['observation']['attempt_status'], 'failed')

