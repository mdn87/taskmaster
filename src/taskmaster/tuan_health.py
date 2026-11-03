"""Tuan collection health carried by the remote-context envelope.

Tuan's collection-watch plan (W3) needs a check that does not depend on agent
hooks. The workstation side runs ``tuan doctor`` and keeps only a bounded
summary: attention names, per-tool gap counts and the oldest gap's age, the Tuan
version, or an error code. No session content, paths or identifiers leave the
workstation. The watcher publishes the latest observation as
``tuan-health.json`` beside the export, and the morning report renders it.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from . import timefmt

PROJECTION = 'taskmaster.tuan-health/v1'
TOOLS = ('codex', 'claude')
ERRORS = ('tuan_missing', 'store_pointer_missing', 'doctor_timeout', 'doctor_failed', 'doctor_invalid',
          'capture_unsupported')
DOCTOR_TIMEOUT = 10
STALE_AFTER = 6 * 3600
NAME = re.compile(r'[a-z_]{1,32}')
HOST = re.compile(r'[a-z0-9][a-z0-9-]{0,31}')
KEYS = {'observed_at', 'status', 'error', 'version', 'attention', 'definite', 'tools'}


def _pointer():
    home = Path.home()
    if os.name == 'nt':
        root = Path(os.environ.get('LOCALAPPDATA', home / 'AppData' / 'Local'))
    elif sys.platform == 'darwin':
        root = home / 'Library' / 'Application Support'
    else:
        root = Path(os.environ.get('XDG_DATA_HOME', home / '.local' / 'share'))
        return root / 'lugos' / 'tuan' / 'store.path'
    return root / 'Lugos' / 'tuan' / 'store.path'


def _error(code):
    return {'observed_at': timefmt.now(), 'status': 'error', 'error': code, 'version': None,
            'attention': [], 'definite': [], 'tools': {}}


def observe(store=None, run=subprocess.run, which=shutil.which):
    executable = which('tuan')
    if not executable:
        return _error('tuan_missing')
    if not store:
        try:
            store = _pointer().read_text(encoding='utf-8').strip()
        except OSError:
            store = ''
        if not store:
            return _error('store_pointer_missing')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        result = run([executable, 'doctor', '--store', str(store), '--format', 'json'], capture_output=True,
                     text=True, timeout=DOCTOR_TIMEOUT, stdin=subprocess.DEVNULL, **options)
    except subprocess.TimeoutExpired:
        return _error('doctor_timeout')
    except OSError:
        return _error('doctor_failed')
    try:
        report = json.loads(result.stdout) if result.stdout.strip() else None
    except ValueError:
        report = None
    if not isinstance(report, dict):
        return _error('doctor_failed' if result.returncode else 'doctor_invalid')
    attention, definite = report.get('attention'), report.get('definite_misconfiguration')
    if not isinstance(attention, list) or not isinstance(definite, list):
        return _error('doctor_invalid')
    tools = {}
    collection = report.get('collection') if isinstance(report.get('collection'), dict) else {}
    for tool, entry in (collection.get('tools') or {}).items():
        if tool in TOOLS and isinstance(entry, dict) and entry.get('present'):
            age = entry.get('oldest_gap_age_s')
            tools[tool] = {'gaps': int(entry.get('gaps') or 0), 'oldest_gap_age_s': int(age) if age is not None else None}
    install = report.get('install') if isinstance(report.get('install'), dict) else {}
    version = install.get('tuan_version')
    value = {'observed_at': timefmt.now(), 'status': 'current', 'error': None,
             'version': version if isinstance(version, str) else None,
             'attention': [n for n in attention if isinstance(n, str)][:32],
             'definite': [n for n in definite if isinstance(n, str)][:32], 'tools': tools}
    try:
        return validate(value)
    except ValueError:
        return _error('doctor_invalid')


def validate(value):
    def require(condition):
        if not condition:
            raise ValueError('invalid Tuan health observation')
    require(isinstance(value, dict) and set(value) == KEYS)
    require(timefmt.is_valid(value['observed_at']) and value['status'] in ('current', 'error'))
    require((value['error'] is None) == (value['status'] == 'current') and (value['error'] is None or value['error'] in ERRORS))
    require(value['version'] is None or (isinstance(value['version'], str) and 0 < len(value['version']) <= 64))
    for key in ('attention', 'definite'):
        require(isinstance(value[key], list) and len(value[key]) <= 32 and all(isinstance(n, str) and NAME.fullmatch(n) for n in value[key]))
    require(isinstance(value['tools'], dict) and set(value['tools']) <= set(TOOLS))
    for entry in value['tools'].values():
        require(isinstance(entry, dict) and set(entry) == {'gaps', 'oldest_gap_age_s'})
        require(type(entry['gaps']) is int and 0 <= entry['gaps'] <= 100000)
        require(entry['oldest_gap_age_s'] is None or (type(entry['oldest_gap_age_s']) is int and entry['oldest_gap_age_s'] >= 0))
    return value


def validate_projection(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'evaluated_at', 'hosts'}
            or value['schema'] != PROJECTION or not timefmt.is_valid(value['evaluated_at'])
            or not isinstance(value['hosts'], dict) or not 1 <= len(value['hosts']) <= 8):
        raise ValueError('invalid Tuan health projection')
    for label, host in value['hosts'].items():
        if (not isinstance(label, str) or not HOST.fullmatch(label) or not isinstance(host, dict)
                or set(host) != {'status', 'health'} or host['status'] not in ('current', 'unavailable')):
            raise ValueError('invalid Tuan health projection')
        if host['health'] is not None:
            validate(host['health'])
    return value


def _snapshot(path, now):
    """A collector snapshot judged by structure only; the observation's own age decides staleness."""
    from . import remote_context
    from .strictjson import loads
    with Path(path).open('rb') as stream:
        raw = stream.read(remote_context.MAX_BYTES + 1)
    value = loads(raw, max_bytes=remote_context.MAX_BYTES)
    if not isinstance(value, dict) or not timefmt.is_valid(value.get('observed_at')) or value['observed_at'] > now:
        raise ValueError('invalid snapshot time')
    return remote_context.validate(value, value['observed_at'])


def publish(settings, now):
    hosts = settings.get('tuan_hosts') or ({'workstation': settings['context_file']} if settings.get('context_file') else None)
    if not hosts:
        return None
    from . import watch
    projected = {}
    for label, path in hosts.items():
        try:
            snapshot = _snapshot(path, now)
            projected[label] = {'status': 'current', 'health': snapshot.get('tuan_health')}
        except (OSError, ValueError, KeyError):
            projected[label] = {'status': 'unavailable', 'health': None}
    value = validate_projection({'schema': PROJECTION, 'evaluated_at': now, 'hosts': projected})
    watch.atomic_json(Path(settings['export']).parent / 'tuan-health.json', value)
    return value


def render(value, now):
    """One line per host for the morning report; a missing or stale observation is a gap."""
    validate_projection(value)
    return ''.join(_line(label, host, now) for label, host in sorted(value['hosts'].items()))


def _line(label, host, now):
    prefix = f'Tuan collection ({label}): '
    health = host['health']
    if host['status'] != 'current':
        return prefix + 'unavailable (no readable collector snapshot)\n'
    if health is None:
        return prefix + 'no health observation yet\n'
    if timefmt.seconds_between(health['observed_at'], now) > STALE_AFTER:
        return prefix + f"health observation stale since {health['observed_at']}\n"
    if health['status'] == 'error':
        return prefix + f"check failed ({health['error']}) at {health['observed_at']}\n"
    names = list(dict.fromkeys(health['definite'] + health['attention']))
    if not names:
        return prefix + f"healthy at {health['observed_at']} (tuan {health['version'] or 'unknown'})\n"
    parts = []
    for name in names:
        if name == 'collection':
            gaps = [f"{tool} {e['gaps']} uncollected, oldest {(e['oldest_gap_age_s'] or 0) // 3600} h"
                    for tool, e in health['tools'].items() if e['gaps']]
            parts.append('collection (' + '; '.join(gaps) + ')' if gaps else 'collection')
        else:
            parts.append(name)
    return prefix + f"needs attention: {', '.join(parts)} at {health['observed_at']}\n"
