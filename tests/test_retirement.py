import json, os
from datetime import datetime, timezone
from unittest.mock import patch
from tests import test_workspaces
from tests.helpers import make_run, write_json, NOW
from taskmaster import config, workspaces, watch, timefmt

class RetirementTests(test_workspaces.WorkspaceTrailTests):
    def setup_capacity(self):
        self.annotate(['old'])
        run=make_run(self.store,'old')
        write_json(run/'state.json', {'run_id':'old','status':'completed','updated_at':'2026-09-01T00:00:00Z'})
        write_json(run/'result.json', {'run_id':'old','status':'completed','completed_at':'2026-09-01T00:00:00Z'})
        stamp=timefmt.parse('2026-09-01T00:00:00Z').timestamp()
        for p in run.iterdir(): os.utime(p,(stamp,stamp))
        registry={'schema':'taskmaster.registry/v1','evaluated_at':NOW,'max_age_seconds':70,'status':'current','sessions':[]}
        write_json(self.tmp/'state'/'session-registry.json',registry)
        return registry

    def test_capacity_retires_last_active_not_first_created_and_keeps_evidence(self):
        self.setup_capacity()
        other=self.tmp/'older-project';other.mkdir()
        workspaces.ensure(self.cfg,other)
        note=other/'.Codex'/'handoff.md';note.parent.mkdir()
        note.write_text('last_updated: 2026-10-16T00:00:00Z\nsession_summary: Still active\n\n'+''.join('## '+h+'\nContent\n' for h in ['What Was Done','Current State','Next Up','Gotchas']))
        folder=self.tmp/'new';folder.mkdir()
        before=self.cfg.read_bytes()
        with patch.object(config,'MAX_OBJECTIVES',2),patch.object(timefmt,'now',return_value=NOW):
            self.assertEqual(workspaces.ensure(self.cfg,folder)[0],'new/workspace')
        ids=[o.namespace+'/'+o.id for o in config.load(self.cfg).objectives]
        self.assertNotIn('ops/obj-1',ids)
        self.assertIn('older-project/workspace',ids)
        self.assertTrue((self.store/'old'/'result.json').is_file())
        self.assertTrue((self.tmp/'annotation.json').is_file())
        archives=list((self.tmp/'state'/'retired-trails').glob('*.json'))
        self.assertEqual(len(archives),1)
        import base64
        archive=json.loads(archives[0].read_text())
        self.assertEqual(base64.b64decode(archive['config_before']),before)
        self.assertEqual(archive['objective'],'ops/obj-1')
        self.assertEqual(workspaces.ensure(self.cfg,folder)[0],'new/workspace')

    def test_active_or_unknown_activity_never_evicted(self):
        registry=self.setup_capacity()
        registry['sessions']=[{'run_id':'session','provider':None,'session_id':None,'objective':'ops/obj-1','provenance':'operator','first_seen_at':'2026-09-01T00:00:00Z','last_seen_at':NOW,'active':True,'assignments':[{'objective':'ops/obj-1','provenance':'operator','registered_at':'2026-09-01T00:00:00Z','last_seen_at':NOW,'ended_at':None}]}]
        write_json(self.tmp/'state'/'session-registry.json',registry)
        folder=self.tmp/'new';folder.mkdir()
        before=self.cfg.read_bytes()
        with patch.object(config,'MAX_OBJECTIVES',1),patch.object(timefmt,'now',return_value=NOW):
            with self.assertRaisesRegex(workspaces.WorkspaceError,'inactive'):
                workspaces.ensure(self.cfg,folder)
        self.assertEqual(self.cfg.read_bytes(),before)

    def test_stale_registry_refuses_retirement(self):
        registry=self.setup_capacity();registry['evaluated_at']='2026-10-16T00:00:00Z'
        write_json(self.tmp/'state'/'session-registry.json',registry)
        folder=self.tmp/'new';folder.mkdir()
        with patch.object(config,'MAX_OBJECTIVES',1),patch.object(timefmt,'now',return_value=NOW):
            with self.assertRaisesRegex(workspaces.WorkspaceError,'current session'):
                workspaces.ensure(self.cfg,folder)

    def test_recent_run_update_protects_an_old_trail(self):
        self.setup_capacity()
        stamp=timefmt.parse(NOW).timestamp()
        os.utime(self.store/'old'/'state.json',(stamp,stamp))
        folder=self.tmp/'new';folder.mkdir()
        with patch.object(config,'MAX_OBJECTIVES',1),patch.object(timefmt,'now',return_value=NOW):
            with self.assertRaisesRegex(workspaces.WorkspaceError,'inactive'):
                workspaces.ensure(self.cfg,folder)

    def test_missing_run_is_not_assumed_inactive(self):
        self.setup_capacity();self.annotate(['not-found'])
        folder=self.tmp/'new';folder.mkdir()
        with patch.object(config,'MAX_OBJECTIVES',1),patch.object(timefmt,'now',return_value=NOW):
            with self.assertRaisesRegex(workspaces.WorkspaceError,'inactive'):
                workspaces.ensure(self.cfg,folder)

    def test_registration_failure_restores_retired_configuration(self):
        self.setup_capacity()
        folder=self.tmp/'new';folder.mkdir()
        before=(self.cfg.read_bytes(),(self.tmp/'watch.json').read_bytes())
        original=watch.load_settings
        def fail_on_new(path,cfg):
            if any(o.namespace=='new' for o in cfg.objectives):
                raise watch.WatchError('injected validation failure')
            return original(path,cfg)
        with patch.object(config,'MAX_OBJECTIVES',1),patch.object(timefmt,'now',return_value=NOW),patch.object(watch,'load_settings',side_effect=fail_on_new):
            with self.assertRaisesRegex(watch.WatchError,'injected'):
                workspaces.ensure(self.cfg,folder)
        self.assertEqual((self.cfg.read_bytes(),(self.tmp/'watch.json').read_bytes()),before)
        archive=next((self.tmp/'state'/'retired-trails').glob('*.json'))
        self.assertTrue(json.loads(archive.read_text())['rolled_back'])

    def test_unrelated_quarantine_does_not_hide_unwatched_saved_candidate(self):
        self.setup_capacity()
        other=self.tmp/'other';other.mkdir()
        workspaces.ensure(self.cfg,other)
        raw=json.loads((self.tmp/'watch.json').read_text())
        raw['rules']=[r for r in raw['rules'] if r['namespace']!='ops']
        write_json(self.tmp/'watch.json',raw)
        folder=self.tmp/'new';folder.mkdir()
        with patch.object(config,'MAX_OBJECTIVES',2),patch.object(timefmt,'now',return_value=NOW),patch.object(watch,'discover',return_value=({}, {}, [], [{'run_id':'unrelated','reason':'not yet bound'}])):
            self.assertEqual(workspaces.ensure(self.cfg,folder)[0],'new/workspace')
        self.assertNotIn('ops/obj-1',[o.namespace+'/'+o.id for o in config.load(self.cfg).objectives])

    def test_finalizer_failure_rolls_back_and_retired_namespace_can_be_reused_safely(self):
        self.setup_capacity()
        folder=self.tmp/'new';folder.mkdir()
        before=(self.cfg.read_bytes(),(self.tmp/'watch.json').read_bytes())
        def fail(objective,archive):
            raise OSError('collector write failed')
        with patch.object(config,'MAX_OBJECTIVES',1),patch.object(timefmt,'now',return_value=NOW):
            with self.assertRaisesRegex(OSError,'collector'):
                workspaces.ensure(self.cfg,folder,on_registered=fail)
        self.assertEqual((self.cfg.read_bytes(),(self.tmp/'watch.json').read_bytes()),before)
        # Retained archived annotation forces a new namespace rather than overwrite.
        write_json(self.tmp/'annotations'/'retired-workspace.json', {'objective_id':'workspace','run_ids':[]})
        write_json(self.tmp/'state'/'retired-trails'/'old.json', {'objective':'retired/workspace'})
        retired=self.tmp/'retired';retired.mkdir()
        self.assertEqual(workspaces.ensure(self.cfg,retired)[0],'retired-2/workspace')

    def test_unrelated_quarantine_does_not_hide_handoff_only_candidate(self):
        self.setup_capacity()
        raw=json.loads((self.tmp/'watch.json').read_text())
        raw['rules'][0]['handoff_only']=True
        write_json(self.tmp/'watch.json',raw)
        folder=self.tmp/'new';folder.mkdir()
        with patch.object(config,'MAX_OBJECTIVES',1),patch.object(timefmt,'now',return_value=NOW),patch.object(watch,'discover',return_value=({}, {}, [], [{'run_id':'unrelated','reason':'not yet bound'}])):
            self.assertEqual(workspaces.ensure(self.cfg,folder)[0],'new/workspace')

    def test_remote_collector_failure_rolls_back_and_retry_prunes_only_retired_paths(self):
        from taskmaster import remote_owner
        import base64
        self.setup_capacity()
        old_note=str(self.root/'.Codex'/'handoff.md')
        raw=json.loads((self.tmp/'watch.json').read_text())
        raw['handoffs']=[{'objective':'ops/obj-1','provider':'codex','path':old_note}]
        write_json(self.tmp/'watch.json',raw)
        collector=write_json(self.tmp/'collector.json',{'notes':{old_note:'old-source','unrelated':'keep'}})
        owner=write_json(self.tmp/'owner.json',{'source_root':str(self.tmp),'client_root':str(self.tmp),'workspace_root':str(self.tmp/'mirrors'),'collector_config':str(collector)})
        before=(self.cfg.read_bytes(),(self.tmp/'watch.json').read_bytes(),collector.read_bytes())
        original=watch.atomic_json
        def fail_collector(path,value):
            if path==collector:
                raise OSError('collector unavailable')
            return original(path,value)
        with patch.object(config,'MAX_OBJECTIVES',1),patch.object(timefmt,'now',return_value=NOW),patch.object(watch,'atomic_json',side_effect=fail_collector):
            with self.assertRaisesRegex(OSError,'collector'):
                remote_owner.ensure_workspace(self.cfg,owner,str(self.tmp/'new'))
        self.assertEqual((self.cfg.read_bytes(),(self.tmp/'watch.json').read_bytes(),collector.read_bytes()),before)
        receipt=json.loads(next((self.tmp/'state'/'retired-trails').glob('*.json')).read_text())
        self.assertEqual(base64.b64decode(receipt['collector_before']),before[2])
        self.assertTrue(receipt['rolled_back'])
        with patch.object(config,'MAX_OBJECTIVES',1),patch.object(timefmt,'now',return_value=NOW):
            result=remote_owner.ensure_workspace(self.cfg,owner,str(self.tmp/'new'))
        self.assertEqual(result['objective'],'new/workspace')
        notes=json.loads(collector.read_text())['notes']
        self.assertNotIn(old_note,notes)
        self.assertEqual(notes['unrelated'],'keep')
        self.assertEqual(len(notes),3)
