import hashlib,unittest,json,tempfile
from pathlib import Path
from unittest.mock import patch
from taskmaster import remote_owner
class RemoteOwnerTests(unittest.TestCase):
 def test_windows_and_mac_workspace_map_to_same_source(self):
  owner={'source_root':r'C:\Users\Matt\Desktop\MyDocs','client_root':'/Users/matt/MyDocs','workspace_root':'/srv/context/workspaces'}
  a=remote_owner.source_workspace(owner,r'C:\Users\Matt\Desktop\MyDocs\taskmaster')
  b=remote_owner.source_workspace(owner,'/Users/matt/MyDocs/taskmaster')
  self.assertEqual(a[:2],b[:2]);self.assertIn(hashlib.sha256(b'c:/users/matt/desktop/mydocs/taskmaster').hexdigest(),b[2])
  for value in ('/etc',r'C:\Windows',r'C:\Users\Matt\Desktop\MyDocs\..\secrets'):
   with self.assertRaises(ValueError):remote_owner.source_workspace(owner,value)
 def test_current_session_identity_is_sent_not_remote_process_identity(self):
  with patch('taskmaster.commands.current_session',return_value='dias:example'),patch.object(remote_owner,'forward',return_value={}) as f:
   remote_owner.forward_command({},'/taskmaster use ops/one')
   self.assertEqual(f.call_args.args[1]['text'],'use ops/one --session dias:example')
 def test_transport_is_argv_not_shell(self):
  with self.assertRaises(ValueError):remote_owner.transport({'remote_owner':'ssh host sh -c something'})
  self.assertEqual(remote_owner.transport({'remote_owner':['ssh','host']}),['ssh','host'])
 def test_status_response_allows_timing_numbers(self):
  import subprocess,json
  def run(argv,**kw):
   kw['stdout'].write(json.dumps({'ok':True,'result':{'cycle_seconds':0.25}}).encode())
   return subprocess.CompletedProcess(argv,0)
  with patch.object(remote_owner.subprocess,'run',side_effect=run):
   self.assertEqual(remote_owner.forward({'remote_owner':['ssh','host']},{})['cycle_seconds'],0.25)
 def test_relative_workspace_is_resolved_on_client(self):
  from pathlib import Path
  with patch.object(remote_owner,'forward',return_value={}) as f:
   remote_owner.forward_command({},'/taskmaster workspace add .')
   self.assertIn(str(Path.cwd()),f.call_args.args[1]['text'])
 def test_hook_records_exact_bytes_when_collector_is_busy(self):
  import tempfile,json,base64
  from types import SimpleNamespace
  from pathlib import Path
  from tests.test_remote_context import NOTE,NOW
  from taskmaster import handoffs
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); owner=root/'owner.json';owner.write_text('{}')
   settings={'state_dir':d,'handoffs':[{'objective':'ops/one','provider':'claude','path':'/mirror/.claude/handoff.md'}]}
   with patch.object(remote_owner.config,'load',return_value=SimpleNamespace(watch='settings')),patch.object(remote_owner.watch,'load_settings',return_value=settings),patch.object(remote_owner,'ensure_workspace',return_value={'objective':'ops/one'}),patch.object(remote_owner,'refresh_inputs'),patch.object(remote_owner.timefmt,'now',return_value=NOW):
    result=remote_owner.handle('cfg',owner,{'action':'handoff','workspace':'/Users/matt/MyDocs/project','provider':'claude','data':base64.b64encode(NOTE).decode()})
   self.assertEqual(result['note_sha256'],hashlib.sha256(NOTE).hexdigest())
   self.assertEqual(handoffs.load_ledger(root/'handoff-ledger.json')['events'][0]['event_id'],result['event_id'])

class ClientPortabilityTests(unittest.TestCase):
 def test_forward_before_owner_path_validation(self):
  from types import SimpleNamespace
  from taskmaster import commands
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'settings.json';path.write_text(json.dumps({'remote_owner':['ssh','fixed-owner'],'handoffs':'owner-specific configuration'}))
   with patch.object(commands.config,'load',return_value=SimpleNamespace(watch=path)), patch.object(remote_owner,'forward_command',return_value={'state':'running'}) as f, patch.object(commands.watch,'load_settings',side_effect=AssertionError('owner paths must not be validated on client')):
    self.assertEqual(commands.execute('unused','status'),{'state':'running'})
    f.assert_called_once()
