import json

from tests.helpers import Workspace, make_run, write_json, NOW
from taskmaster import commands, config, workspaces, watch


class WorkspaceTrailTests(Workspace):
    def setUp(self):
        super().setUp()
        self.root = self.tmp/'project'
        self.root.mkdir()
        self.annotate([])
        cfg = self.config()
        self.settings = {'schema_version': 1, 'source': str(self.store), 'state_dir': str(self.tmp/'state'),
                         'export': str(self.tmp/'active'/'export.json'), 'interval_seconds': 30,
                         'rules': [{'namespace': 'ops', 'id': 'obj-1', 'workspace': str(self.root),
                                    'record': 'request-summary.json', 'anchors': [], 'project_ids': ['repo']}]}
        write_json(self.tmp/'watch.json', self.settings)
        cfg.write_text('watch = "watch.json"\n' + cfg.read_text())
        self.cfg = cfg

    def test_new_folder_gets_an_empty_trail_keyed_to_it(self):
        folder = self.tmp/'tali'
        folder.mkdir()
        result = commands.execute(self.cfg, f'/taskmaster workspace add "{folder}"')
        self.assertEqual(result, {'objective': 'tali/workspace', 'started': True, 'notes_bound': True})
        annotation = json.loads((self.tmp/'annotations'/'tali-workspace.json').read_text())
        self.assertEqual(annotation, {'objective_id': 'workspace', 'run_ids': []})
        cfg = config.load(self.cfg)
        settings = watch.load_settings(cfg.watch, cfg)
        self.assertEqual(workspaces.find(settings, folder), ['tali/workspace'])

    def test_folder_with_a_trail_only_gets_its_notes_bound(self):
        result = commands.execute(self.cfg, f'/taskmaster workspace add "{self.root}"')
        self.assertEqual(result, {'objective': 'ops/obj-1', 'started': False, 'notes_bound': True})
        before = self.cfg.read_text()
        again = commands.execute(self.cfg, f'/taskmaster workspace add "{self.root}"')
        self.assertFalse(again['notes_bound'])
        self.assertEqual(self.cfg.read_text(), before)

    def test_failure_leaves_configuration_unchanged(self):
        folder = self.tmp/'tali'
        folder.mkdir()
        write_json(self.tmp/'annotations'/'tali-workspace.json', {'objective_id': 'workspace', 'run_ids': []})
        toml, settings = self.cfg.read_bytes(), (self.tmp/'watch.json').read_bytes()
        with self.assertRaises(watch.WatchError):
            commands.execute(self.cfg, f'/taskmaster workspace add "{folder}"')
        self.assertEqual(self.cfg.read_bytes(), toml)
        self.assertEqual((self.tmp/'watch.json').read_bytes(), settings)

    def test_names_avoid_existing_namespaces_and_the_watcher_refreshes_an_empty_trail(self):
        first, second = self.tmp/'a'/'Ops Site', self.tmp/'b'/'ops-site'
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        run = make_run(self.store, 'run-1')
        write_json(run/'request-summary.json', {'task_sha256': 'a'*64, 'workspace': str(self.root)})
        self.assertEqual(workspaces.ensure(self.cfg, first)[0], 'ops-site/workspace')
        self.assertEqual(workspaces.ensure(self.cfg, second)[0], 'ops-site-2/workspace')
        claimed = make_run(self.store, 'run-2')
        write_json(claimed/'request-summary.json', {'task_sha256': 'a'*64, 'workspace': str(first)})
        status = watch.tick(self.cfg, self.tmp/'watch.json', now=NOW)
        self.assertEqual(status['state'], 'running')
        self.assertEqual(status['memberships'], {'ops/obj-1': ['run-1']})

    def test_handoff_only_must_be_true(self):
        self.settings['rules'][0]['handoff_only'] = False
        write_json(self.tmp/'watch.json', self.settings)
        cfg = config.load(self.cfg)
        with self.assertRaises(watch.WatchError):
            watch.load_settings(cfg.watch, cfg)
