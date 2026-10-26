import base64,hashlib,json,tempfile,unittest
from pathlib import Path
from taskmaster import handoffs,watch,remote_context
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
try:
 ZoneInfo('America/New_York');HAS_SOURCE_ZONE=True
except ZoneInfoNotFoundError:
 HAS_SOURCE_ZONE=False
NOW='2026-10-19T22:00:00Z'
NOTE=b'last_updated: 2026-10-19T21:00:00Z\nsession_summary: saved\n## What Was Done\nDone\n## Current State\nReady\n## Next Up\nContinue\n## Gotchas\nNone\n'
class RemoteContextTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
  self.note=str(self.root/'workspace/.claude/handoff.md')
  self.s={'state_dir':str(self.root/'state'),'export':str(self.root/'out/export.json'),'interval_seconds':30,'context_file':str(self.root/'context.json'),'rules':[{'namespace':'ops','id':'one','workspace':str(self.root/'workspace'),'workspace_keys':['a'*64],'project_ids':[]}],'handoffs':[{'objective':'ops/one','provider':'claude','path':self.note}]}
  self.d={'schema':'taskmaster.remote-context/v1','observed_at':NOW,'max_age_seconds':45,'timezone':'America/New_York','dias_status':'current','context':{'schemaVersion':1,'sessions':[],'endedSessions':[]},'notes':{self.note:{'status':'present','data':base64.b64encode(NOTE).decode()}}}
 def save(self):Path(self.s['context_file']).write_text(json.dumps(self.d))
 @unittest.skipUnless(HAS_SOURCE_ZONE, 'remote owner requires an IANA timezone database; capture is tested separately')
 def test_note_revisions_and_stale_transport(self):
  self.save();a=handoffs.publish(self.s,NOW);b=handoffs.publish(self.s,NOW)
  self.assertEqual(a['trails'][0]['status'],'saved');self.assertEqual(a,b)
  path=self.root/'state/handoff-ledger.json';self.assertEqual(len(json.loads(path.read_text())['events']),1)
  self.d['notes'][self.note]['data']=base64.b64encode(NOTE+b'Changed\n').decode();self.save();handoffs.publish(self.s,NOW)
  self.assertEqual(len(json.loads(path.read_text())['events']),2);before=path.read_bytes()
  self.assertEqual(handoffs.publish(self.s,'2026-10-19T22:01:00Z')['status'],'unavailable');self.assertEqual(path.read_bytes(),before)
 @unittest.skipUnless(HAS_SOURCE_ZONE, 'remote owner requires an IANA timezone database; capture is tested separately')
 def test_missing_unavailable_invalid_clear_saved(self):
  for status in ('missing','unavailable','invalid'):
   self.d['notes'][self.note]={'status':status,'data':None};self.save()
   self.assertEqual(handoffs.publish(self.s,NOW)['trails'][0]['sources'][0]['status'],status)
 @unittest.skipUnless(HAS_SOURCE_ZONE, 'remote owner requires an IANA timezone database; capture is tested separately')
 def test_live_exit_and_outage_preserve_history(self):
  sid='example-session';rid='dias:codex-hook:'+hashlib.sha256(sid.encode()).hexdigest()
  row={'runId':rid,'workspaceKeys':['a'*64],'projectIds':[],'resume':{'provider':'codex','session_id':sid}}
  self.d['context']['sessions']=[row];self.save()
  self.assertEqual(watch.project_sessions(self.s,NOW)['sessions'][0]['objective'],'ops/one')
  self.d['observed_at']='2026-10-19T22:00:10Z';self.d['context']={'schemaVersion':1,'sessions':[],'endedSessions':[{**row,'observedAt':NOW}]};self.save()
  watch.project_sessions(self.s,'2026-10-19T22:00:10Z')
  history=json.loads((self.root/'state/session-registry.json').read_text());self.assertFalse(history['sessions'][0]['active']);self.assertEqual(history['sessions'][0]['session_id'],sid)
  self.assertEqual(watch.project_sessions(self.s,'2026-10-19T22:02:00Z')['status'],'unavailable')
  self.assertEqual(len(json.loads((self.root/'state/session-registry.json').read_text())['sessions']),1)
 @unittest.skipUnless(HAS_SOURCE_ZONE, 'remote owner requires an IANA timezone database; capture is tested separately')
 def test_future_and_bad_bytes_rejected(self):
  self.save()
  with self.assertRaises(ValueError):remote_context.read(self.s,'2026-10-19T21:59:59Z')
  self.d['notes'][self.note]['data']='not base64';self.save()
  with self.assertRaises(ValueError):remote_context.read(self.s,NOW)
 @unittest.skipUnless(HAS_SOURCE_ZONE, 'remote owner requires an IANA timezone database; capture is tested separately')
 def test_source_timezone(self):
  data=NOTE.replace(b'2026-10-19T21:00:00Z',b'2026-10-19 17:00')
  self.assertEqual(handoffs.inspect_bytes(data,NOW,'America/New_York')['note_updated_at'],'2026-10-19T21:00:00Z')

 def test_capture_does_not_require_windows_timezone_database(self):
  from unittest.mock import patch
  with patch.object(remote_context,'ZoneInfo',side_effect=KeyError('no IANA database')), patch.object(remote_context.urllib.request,'urlopen',side_effect=OSError('offline')):
   value=remote_context.capture({'dias_url':'http://127.0.0.1:4175','timezone':'America/New_York','notes':{}})
  self.assertEqual(value['timezone'],'America/New_York')
  self.assertEqual(value['dias_status'],'unavailable')
