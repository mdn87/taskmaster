"""SSH stdio forwarding to one Taskmaster context owner; no shell interpolation."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shlex
import subprocess
import sys
import tempfile
from . import config, publication, timefmt, watch
from .strictjson import loads
MAX_BYTES=20*1024*1024


def transport(settings):
    value=settings['remote_owner']
    if isinstance(value,dict):value=value.get(os.name)
    if not isinstance(value,list) or not 1<=len(value)<=40 or any(not isinstance(v,str) or not v or '\x00' in v for v in value):
        raise ValueError('remote_owner needs an explicit argv for this platform')
    return value


def client_settings(path):
    """Clients validate transport only; source paths belong to the owner host."""
    raw=watch.read_json(Path(path))
    if raw.get('remote_owner') is None:
        return None
    transport(raw)
    return raw


def forward(settings, request):
    with tempfile.TemporaryFile() as output:
        result=subprocess.run(transport(settings),input=json.dumps(request).encode(),stdout=output,
                              stderr=subprocess.DEVNULL,timeout=100,check=False,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        output.seek(0);raw=output.read(MAX_BYTES+1)
    if result.returncode or len(raw)>MAX_BYTES:
        raise watch.WatchError('remote context owner unavailable; no local write performed')
    response=loads(raw,max_bytes=MAX_BYTES,allow_floats=True)
    if not isinstance(response,dict) or response.get('ok') is not True:
        raise watch.WatchError(response.get('error','remote context command failed') if isinstance(response,dict) else 'invalid owner response')
    return response['result']


def forward_command(settings,text):
    from . import commands
    tokens=shlex.split(text)
    if tokens and tokens[0] in ('/taskmaster','taskmaster'):tokens.pop(0)
    if tokens and tokens[0] in ('use','clear','auto') and '--session' not in tokens:
        tokens+=['--session',commands.current_session()]
    if len(tokens)==3 and tokens[:2]==['workspace','add'] and not PureWindowsPath(tokens[2]).is_absolute():
        tokens[2]=str(Path(tokens[2]).absolute())
    return forward(settings,{'action':'slash','text':shlex.join(tokens)})


def source_workspace(owner, value):
    """Translate only inventoried client workspace roots, retaining source hashes."""
    if not isinstance(value,str) or len(value)>4096 or '\x00' in value:
        raise ValueError('invalid workspace')
    win=PureWindowsPath(value)
    if win.is_absolute():
        source=win
        relative=source.relative_to(PureWindowsPath(owner['source_root']))
        original=str(source).replace('\\','/').rstrip('/').lower()
    else:
        posix=PurePosixPath(value)
        relative=posix.relative_to(PurePosixPath(owner['client_root']))
        source=PureWindowsPath(owner['source_root']).joinpath(*relative.parts)
        original=str(posix).rstrip('/')
    if '..' in relative.parts:raise ValueError('workspace escapes configured root')
    key=str(source).replace('\\','/').rstrip('/').lower()
    digest=hashlib.sha256(key.encode()).hexdigest()
    mirror=Path(owner['workspace_root'])/digest/source.name
    keys={digest,hashlib.sha256(original.encode()).hexdigest(),hashlib.sha256(watch.normalized(str(mirror)).encode()).hexdigest()}
    return str(source),mirror,keys


def ensure_workspace(config_path,owner_path,value):
    from . import workspaces,handoffs
    owner=watch.read_json(Path(owner_path))
    source,mirror,keys=source_workspace(owner,value)
    mirror.mkdir(parents=True,exist_ok=True)
    cfg=config.load(config_path);settings=watch.load_settings(cfg.watch,cfg)
    raw=watch.read_json(cfg.watch)
    matches=[r for r in raw['rules'] if keys.intersection(r.get('workspace_keys',[])) or str(r['workspace']).replace('\\','/').rstrip('/').lower()==source.replace('\\','/').rstrip('/').lower()]
    if len(matches)>1:raise ValueError('workspace matches multiple trails')
    if matches:
        matches[0]['workspace_keys']=sorted(keys|set(matches[0].get('workspace_keys',[])))
        watch.atomic_json(cfg.watch,raw)
    objective,changed,new=workspaces.ensure(config_path,mirror)
    raw=watch.read_json(cfg.watch)
    rule=next(r for r in raw['rules'] if r['namespace']+'/'+r['id']==objective)
    rule['workspace_keys']=sorted(keys|set(rule.get('workspace_keys',[])))
    watch.atomic_json(cfg.watch,raw)
    collector=watch.read_json(Path(owner['collector_config']))
    for binding in raw.get('handoffs',[]):
        if binding['objective']==objective:
            collector['notes'][binding['path']]=str(PureWindowsPath(source)/handoffs.FOLDERS[binding['provider']]/'handoff.md')
    watch.atomic_json(Path(owner['collector_config']),collector)
    return {'objective':objective,'started':new,'notes_bound':changed}


def refresh_inputs(owner_path):
    from . import remote_context
    owner=watch.read_json(Path(owner_path))
    try:remote_context.collect(owner['collector_config'])
    except publication.PublicationError:pass  # The supervised collector already owns this observation.


def handle(config_path,owner_path,request):
    from . import commands,handoffs
    if not isinstance(request,dict):raise ValueError('invalid owner request')
    cfg=config.load(config_path);settings=watch.load_settings(cfg.watch,cfg)
    if settings.get('remote_owner'):raise ValueError('owner cannot forward recursively')
    if request.get('action')=='slash' and set(request)=={'action','text'}:
        tokens=shlex.split(request['text'])
        if tokens and tokens[0] in ('/taskmaster','taskmaster'):tokens.pop(0)
        if len(tokens)==3 and tokens[:2]==['workspace','add']:
            with publication.writer_lock(Path(settings['state_dir'])/'remote-owner'):
                result=ensure_workspace(config_path,owner_path,tokens[2])
                refresh_inputs(owner_path)
                return result
        refresh_inputs(owner_path)
        return commands.execute(config_path,request['text'])
    if request.get('action')=='handoff' and set(request)=={'action','workspace','provider','data'}:
        if request['provider'] not in handoffs.FOLDERS:
            raise ValueError('invalid handoff provider')
        raw=base64.b64decode(request['data'],validate=True)
        owner=watch.read_json(Path(owner_path))
        now=timefmt.now()
        note=handoffs.inspect_bytes(raw,now,owner.get('timezone','America/New_York'))
        with publication.writer_lock(Path(settings['state_dir'])/'remote-owner'):
            result=ensure_workspace(config_path,owner_path,request['workspace'])
            cfg=config.load(config_path);settings=watch.load_settings(cfg.watch,cfg)
            binding=next(b for b in settings['handoffs'] if b['objective']==result['objective'] and b['provider']==request['provider'])
            state=Path(settings['state_dir'])
            with publication.writer_lock(state/'handoffs'):
                ledger=handoffs.load_ledger(state/'handoff-ledger.json')
                event=handoffs.append_event(ledger,binding,note,now)
                if len(ledger['events'])>handoffs.MAX_EVENTS or len((json.dumps(ledger,indent=2)+'\n').encode())>handoffs.MAX_LEDGER:
                    raise ValueError('handoff ledger capacity reached')
                watch.atomic_json(state/'handoff-ledger.json',ledger)
            # Acknowledge the exact revision, even if the periodic collector is busy.
            # Projection freshness remains owned by real source observations.
            result.update(handoff_status='recorded',event_id=event['event_id'],note_sha256=note['note_sha256'])
            refresh_inputs(owner_path)
            return result
    raise ValueError('unsupported owner action')


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--owner-config',required=True);a=p.parse_args(argv)
    try:
        raw=sys.stdin.buffer.read(MAX_BYTES+1)
        result=handle(a.config,a.owner_config,loads(raw,max_bytes=MAX_BYTES))
        response={'ok':True,'result':result}
    except Exception as exc:
        response={'ok':False,'error':f'{type(exc).__name__}: {exc}'}
    sys.stdout.buffer.write(json.dumps(response).encode())
    return 0

if __name__=='__main__':raise SystemExit(main())
