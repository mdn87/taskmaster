"""Bounded, freshness-checked observations from a remote operator workstation.

SSH authenticates transport. This module does not expose a network listener or
move evidence authority: Taskmaster on the receiving host validates and projects.
"""
from __future__ import annotations
import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from zoneinfo import ZoneInfo
from . import timefmt
from .strictjson import loads

MAX_BYTES = 20 * 1024 * 1024
MAX_NOTE = 64 * 1024


def validate(value, now):
    if not isinstance(value, dict) or set(value) != {'schema','observed_at','max_age_seconds','timezone','dias_status','context','notes'}:
        raise ValueError('invalid remote context envelope')
    if value['schema'] != 'taskmaster.remote-context/v1' or type(value['max_age_seconds']) is not int or not 1 <= value['max_age_seconds'] <= 60:
        raise ValueError('invalid remote context schema or age')
    age = timefmt.seconds_between(value['observed_at'], now)
    if not 0 <= age <= value['max_age_seconds']:
        raise ValueError('remote context is not current')
    ZoneInfo(value['timezone'])
    if value['dias_status'] not in ('current','unavailable'):
        raise ValueError('invalid Dias observation status')
    if value['dias_status'] == 'current' and not isinstance(value['context'], dict):
        raise ValueError('invalid Dias context')
    if value['dias_status'] == 'unavailable' and value['context'] is not None:
        raise ValueError('unavailable Dias must not retain current context')
    if not isinstance(value['notes'], dict) or len(value['notes']) > 128:
        raise ValueError('invalid remote notes')
    for key, row in value['notes'].items():
        if not isinstance(key,str) or not key or not isinstance(row,dict) or set(row) != {'status','data'}:
            raise ValueError('invalid note observation')
        if row['status'] == 'present':
            raw = base64.b64decode(row['data'], validate=True)
            if len(raw) > MAX_NOTE:
                raise ValueError('remote note oversized')
        elif row['status'] not in ('missing','unavailable','invalid') or row['data'] is not None:
            raise ValueError('invalid note observation status')
    return value


def read(settings, now):
    with Path(settings['context_file']).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('remote context oversized')
    return validate(loads(data, max_bytes=MAX_BYTES), now)


def capture(request):
    """Observe only explicit standard handoff files and the local Dias feed."""
    from . import handoffs, watch
    if not isinstance(request,dict) or set(request) != {'dias_url','timezone','notes'}:
        raise ValueError('invalid capture request')
    import re
    if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}',request['dias_url']):
        raise ValueError('Dias must remain loopback-only')
    # Capture forwards the source zone; only the Linux owner interprets dates.
    # Windows Python may not have an IANA timezone database installed.
    if not isinstance(request['timezone'], str) or not re.fullmatch(r'[A-Za-z_]+(?:/[A-Za-z_+-]+)*', request['timezone']):
        raise ValueError('invalid source timezone')
    if not isinstance(request['notes'],dict) or len(request['notes']) > 128:
        raise ValueError('invalid note scope')
    result={'schema':'taskmaster.remote-context/v1','observed_at':timefmt.now(),'max_age_seconds':45,
            'timezone':request['timezone'],'dias_status':'unavailable','context':None,'notes':{}}
    try:
        with urllib.request.urlopen(request['dias_url']+'/api/v1/trails/context',timeout=3) as response:
            raw=response.read(5*1024*1024+1)
        context=loads(raw,max_bytes=5*1024*1024)
        if not isinstance(context,dict) or context.get('schemaVersion') != 1:
            raise ValueError('invalid Dias feed')
        result.update(dias_status='current',context=context)
    except (OSError,ValueError):
        pass
    for target, source in request['notes'].items():
        path=Path(source)
        if not path.is_absolute() or path.name != 'handoff.md' or path.parent.name not in handoffs.FOLDERS.values():
            raise ValueError('capture only accepts exact standard note paths')
        try:
            for parent in (path.parent.parent,path.parent):watch.plain(parent,directory=True)
            watch.plain(path)
            before=path.stat()
            with path.open('rb') as stream:raw=stream.read(MAX_NOTE+1)
            after=path.stat()
            if len(raw)>MAX_NOTE or (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
                raise ValueError('note changed or oversized')
            row={'status':'present','data':base64.b64encode(raw).decode('ascii')}
        except FileNotFoundError:row={'status':'missing','data':None}
        except OSError:row={'status':'unavailable','data':None}
        except ValueError:row={'status':'invalid','data':None}
        result['notes'][target]=row
    return result


def collect(path):
    from . import watch, publication
    cfg=watch.read_json(Path(path))
    destination=Path(cfg['destination'])
    destination.parent.mkdir(parents=True,exist_ok=True)
    with publication.writer_lock(destination.parent/'remote-collect'):
        request={k:cfg[k] for k in ('dias_url','timezone','notes')}
        started=time.monotonic()
        try:
            with tempfile.TemporaryFile() as output:
                result=subprocess.run(cfg['command'],input=json.dumps(request).encode(),stdout=output,
                                      stderr=subprocess.DEVNULL,timeout=20,check=False)
                if result.returncode:raise ValueError('capture transport failed')
                output.seek(0);raw=output.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:raise ValueError('capture oversized')
            snapshot=validate(loads(raw,max_bytes=MAX_BYTES),timefmt.now())
            if set(snapshot['notes']) != set(request['notes']):raise ValueError('capture scope mismatch')
            watch.atomic_json(destination,snapshot)
            status={'ok':True,'observed_at':snapshot['observed_at'],'error':None}
        except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
            # Explicit unavailable observations prevent old copies appearing fresh.
            snapshot={'schema':'taskmaster.remote-context/v1','observed_at':timefmt.now(),'max_age_seconds':45,
                      'timezone':cfg['timezone'],'dias_status':'unavailable','context':None,
                      'notes':{p:{'status':'unavailable','data':None} for p in cfg['notes']}}
            watch.atomic_json(destination,snapshot)
            status={'ok':False,'observed_at':snapshot['observed_at'],'error':type(exc).__name__}
        status['seconds']=round(time.monotonic()-started,3)
        watch.atomic_json(destination.with_name('context-transport-status.json'),status)
        return status


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('capture','collect'))
    p.add_argument('--config');p.add_argument('--once',action='store_true');a=p.parse_args(argv)
    if a.mode=='capture':
        raw=sys.stdin.buffer.read(MAX_BYTES+1)
        result=capture(loads(raw,max_bytes=MAX_BYTES))
        sys.stdout.buffer.write(json.dumps(result).encode());return 0
    while True:
        start=time.monotonic()
        try:result=collect(a.config)
        except Exception as exc:result={'ok':False,'error':type(exc).__name__}
        print(json.dumps(result),flush=True)
        if a.once:return 0 if result['ok'] else 1
        time.sleep(max(1,15-(time.monotonic()-start)))

if __name__=='__main__':raise SystemExit(main())
