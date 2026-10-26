"""Verified session-note observations; never acceptance or execution authority."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

from . import publication, registry, timefmt
from .canonical import canonical, sha256_hex

MAX_NOTE = 64 * 1024
MAX_PROJECTION = 256 * 1024
MAX_LEDGER = 4 * 1024 * 1024
MAX_EVENTS = 4096
NOTE_AGE = 7 * 86400
HASH = re.compile(r'[a-f0-9]{64}\Z')
SOURCE_STATES = ('saved', 'stale', 'invalid', 'unavailable', 'missing')
FOLDERS = {'codex': '.Codex', 'claude': '.claude'}
SECTIONS = ('What Was Done', 'Current State', 'Next Up', 'Gotchas')


def configure(settings, base):
    from . import watch
    bindings = settings.get('handoffs', [])
    if not isinstance(bindings, list) or len(bindings) > 128:
        raise ValueError('handoffs must be a bounded explicit source list')
    rules = {r['namespace']+'/'+r['id']: r for r in settings['rules']}
    seen = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {'objective', 'provider', 'path'}:
            raise ValueError('invalid handoff source')
        target, provider, path = binding['objective'], binding['provider'], binding['path']
        if not isinstance(target, str) or target not in rules or not isinstance(provider, str) or provider not in FOLDERS or not isinstance(path, str) or not path:
            raise ValueError('handoff source must name a configured objective and provider')
        if (target, provider) in seen:
            raise ValueError('duplicate handoff source')
        seen.add((target, provider))
        file = (Path(base)/path).absolute()
        root = watch.normalized(str(file.parent.parent))
        rule = rules[target]
        exact = (Path(rule['workspace']).is_absolute() and root == watch.normalized(rule['workspace']))
        hashed = hashlib.sha256(root.encode()).hexdigest() in rule.get('workspace_keys', [])
        if file.name != 'handoff.md' or file.parent.name != FOLDERS[provider] or not (exact or hashed):
            raise ValueError('handoff must use the exact configured objective workspace')
        binding['path'] = str(file)
    return settings


def aggregate(sources):
    return next((state for state in SOURCE_STATES if any(s['status'] == state for s in sources)), 'unconfigured')


def empty(now, status='unavailable', max_age=70):
    return {'schema': 'taskmaster.handoffs/v1', 'evaluated_at': now, 'max_age_seconds': max_age,
            'status': status, 'trails': []}


def validate(value):
    def require(condition):
        if not condition:
            raise ValueError('invalid handoff projection')
    require(isinstance(value, dict) and set(value) == {'schema', 'evaluated_at', 'max_age_seconds', 'status', 'trails'})
    require(value['schema'] == 'taskmaster.handoffs/v1' and timefmt.is_valid(value['evaluated_at']))
    require(type(value['max_age_seconds']) is int and 10 <= value['max_age_seconds'] <= 7210)
    require(value['status'] in ('current', 'unavailable', 'unconfigured'))
    require(isinstance(value['trails'], list) and len(value['trails']) <= 64)
    seen = set()
    for trail in value['trails']:
        require(isinstance(trail, dict) and set(trail) == {'objective', 'status', 'sources'})
        require(registry.objective(trail['objective']) and trail['objective'] not in seen)
        seen.add(trail['objective'])
        require(isinstance(trail['sources'], list) and len(trail['sources']) <= 2)
        providers = set()
        for source in trail['sources']:
            require(isinstance(source, dict) and set(source) == {'provider', 'status', 'note_sha256', 'note_updated_at', 'event_id'})
            require(isinstance(source['provider'], str) and source['provider'] in FOLDERS and source['provider'] not in providers)
            providers.add(source['provider'])
            require(source['status'] in SOURCE_STATES)
            if source['status'] in ('saved', 'stale'):
                require(all(isinstance(source[k], str) and HASH.fullmatch(source[k]) for k in ('note_sha256', 'event_id')))
                require(timefmt.is_valid(source['note_updated_at']) and source['note_updated_at'] <= value['evaluated_at'])
                age = timefmt.seconds_between(source['note_updated_at'], value['evaluated_at'])
                require(source['status'] == ('saved' if age <= NOTE_AGE else 'stale'))
            else:
                require(all(source[k] is None for k in ('note_sha256', 'note_updated_at', 'event_id')))
        require(trail['status'] == aggregate(trail['sources']))
    require(len(json.dumps(value, ensure_ascii=True).encode()) <= MAX_PROJECTION)
    return value


def read_projection(path, now):
    from . import watch
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_PROJECTION + 1)
        if len(raw) > MAX_PROJECTION:
            return empty(now)
        value = validate(watch.owner_json(raw))
        if not 0 <= timefmt.seconds_between(value['evaluated_at'], now) <= value['max_age_seconds']:
            return empty(now)
        return value
    except FileNotFoundError:
        return empty(now, 'unconfigured')
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return empty(now)


def note_timestamp(value, source_timezone=None):
    if re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', value):
        # Existing session notes use host-local wall time, not a fabricated UTC time.
        moment = datetime.strptime(value, '%Y-%m-%d %H:%M')
        if source_timezone:
            from zoneinfo import ZoneInfo
            moment = moment.replace(tzinfo=ZoneInfo(source_timezone))
        moment = moment.astimezone(timezone.utc)
    else:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            raise ValueError('handoff timestamp needs a timezone')
    return timefmt.fmt(moment)


def read_note_bytes(path):
    from . import watch
    file = Path(path)
    for parent in (file.parent.parent, file.parent):
        watch.plain(parent, directory=True)
    watch.plain(file)
    before = file.stat()
    with file.open('rb') as stream:
        raw = stream.read(MAX_NOTE + 1)
    after = file.stat()
    if len(raw) > MAX_NOTE or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('handoff note oversized or changed during read')
    return raw


def inspect_note(path, now):
    return inspect_bytes(read_note_bytes(path), now)


def inspect_bytes(raw, now, source_timezone=None):
    if len(raw) > MAX_NOTE:
        raise ValueError('handoff note oversized')
    text = raw.decode('utf-8-sig')
    headers = {}
    section_lines = {}
    section = None
    for line in text.splitlines():
        if line.startswith('## '):
            section = line[3:].strip()
            if section in section_lines:
                raise ValueError('duplicate handoff section')
            section_lines[section] = []
        elif section is not None:
            section_lines[section].append(line)
        else:
            for key in ('last_updated', 'session_summary'):
                if line.startswith(key + ':'):
                    if key in headers:
                        raise ValueError('duplicate handoff header')
                    headers[key] = line.split(':', 1)[1].strip()
    if not headers.get('session_summary') or any(not '\n'.join(section_lines.get(key, [])).strip() for key in SECTIONS):
        raise ValueError('required handoff fields missing')
    updated = note_timestamp(headers.get('last_updated', ''), source_timezone)
    age = timefmt.seconds_between(updated, now)
    if age < 0:
        raise ValueError('future handoff note')
    return {'note_sha256': hashlib.sha256(raw).hexdigest(), 'note_updated_at': updated,
            'status': 'saved' if age <= NOTE_AGE else 'stale'}


def append_event(ledger, binding, note, now):
    binding_hash = sha256_hex(canonical(binding))
    event = next((e for e in reversed(ledger['events']) if
                  (e['objective'], e['provider'], e['binding_sha256']) ==
                  (binding['objective'], binding['provider'], binding_hash)), None)
    if event is None or event['note_sha256'] != note['note_sha256']:
        event = {'previous_event_id': ledger['events'][-1]['event_id'] if ledger['events'] else None,
                 'objective': binding['objective'], 'provider': binding['provider'], 'binding_sha256': binding_hash,
                 'note_sha256': note['note_sha256'], 'note_updated_at': note['note_updated_at'], 'recorded_at': now}
        event['event_id'] = sha256_hex(canonical(event))
        ledger['events'].append(event)
    return event


def load_ledger(path):
    from . import watch
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_LEDGER + 1)
    except FileNotFoundError:
        return {'schema': 'taskmaster.handoff-ledger/v1', 'events': []}
    if len(raw) > MAX_LEDGER:
        raise ValueError('handoff ledger over bounds')
    value = watch.owner_json(raw)
    if set(value) != {'schema', 'events'} or value['schema'] != 'taskmaster.handoff-ledger/v1' or not isinstance(value['events'], list) or len(value['events']) > MAX_EVENTS:
        raise ValueError('invalid handoff ledger')
    previous = None
    for event in value['events']:
        keys = {'event_id', 'previous_event_id', 'objective', 'provider', 'binding_sha256', 'note_sha256', 'note_updated_at', 'recorded_at'}
        if not isinstance(event, dict) or set(event) != keys:
            raise ValueError('invalid handoff event')
        content = {k: v for k, v in event.items() if k != 'event_id'}
        if (event['previous_event_id'] != previous or event['event_id'] != sha256_hex(canonical(content))
                or not registry.objective(event['objective']) or event['provider'] not in FOLDERS
                or not all(isinstance(event[k], str) and HASH.fullmatch(event[k]) for k in ('binding_sha256', 'note_sha256'))
                or not all(timefmt.is_valid(event[k]) for k in ('note_updated_at', 'recorded_at'))
                or event['note_updated_at'] > event['recorded_at']):
            raise ValueError('invalid handoff event chain')
        previous = event['event_id']
    return value


def publish(settings, now, cycle_seconds=0.0):
    from . import watch
    state, destination = Path(settings['state_dir']), Path(settings['export']).parent
    state.mkdir(parents=True, exist_ok=True)
    # `now` is the cycle start. The projection must outlive the rest of this
    # cycle, the poll interval and the next cycle, so a slow cycle widens it.
    max_age = min(7210, 2 * settings['interval_seconds'] + 10 + 2 * math.ceil(cycle_seconds))
    result = empty(now, 'current' if settings.get('handoffs') else 'unconfigured', max_age)
    try:
        with publication.writer_lock(state/'handoffs'):
            snapshot = None
            if settings.get('context_file'):
                from . import remote_context
                snapshot = remote_context.read(settings, now)
            ledger = load_ledger(state/'handoff-ledger.json')
            projected = {}
            for binding in settings.get('handoffs', []):
                source = {'provider': binding['provider'], 'status': 'missing', 'note_sha256': None, 'note_updated_at': None, 'event_id': None}
                try:
                    if snapshot is None:
                        note = inspect_note(binding['path'], now)
                    else:
                        import base64
                        row = snapshot['notes'].get(binding['path'], {'status':'unavailable','data':None})
                        if row['status'] == 'present':
                            note = inspect_bytes(base64.b64decode(row['data'], validate=True), now, snapshot['timezone'])
                        else:
                            source['status'], note = row['status'], None
                except FileNotFoundError:
                    note = None
                except OSError:
                    source['status'], note = 'unavailable', None
                except (ValueError, OverflowError):
                    source['status'], note = 'invalid', None
                if note is not None:
                    event = append_event(ledger, binding, note, now)
                    source.update(note, event_id=event['event_id'])
                projected.setdefault(binding['objective'], []).append(source)
            if len(ledger['events']) > MAX_EVENTS or len((json.dumps(ledger, indent=2)+'\n').encode()) > MAX_LEDGER:
                raise ValueError('handoff ledger capacity reached')
            result['trails'] = [{'objective': r['namespace']+'/'+r['id'],
                                 'status': aggregate(projected.get(r['namespace']+'/'+r['id'], [])),
                                 'sources': projected.get(r['namespace']+'/'+r['id'], [])} for r in settings['rules']]
            validate(result)
            watch.atomic_json(state/'handoff-ledger.json', ledger)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, publication.PublicationError):
        result = empty(now, 'unavailable', max_age)
    watch.atomic_json(destination/'handoffs.json', result)
    watch.atomic_bytes(destination/'handoff-report.md', render(result).encode('utf-8'))
    return result


def render(value):
    validate(value)
    lines = ['taskmaster handoff report v1', f"evaluated_at: {value['evaluated_at']}", f"status: {value['status']}"]
    if value['status'] == 'current':
        for trail in value['trails']:
            label = 'Handoff saved' if trail['status'] == 'saved' else 'Handoff ' + trail['status']
            lines.append(f"trail: {trail['objective']} — {label}")
            for source in trail['sources']:
                lines.append(f"  {source['provider']}: {source['status']} updated_at={source['note_updated_at'] or 'unknown'}")
        lines.append('Saved notes do not establish that another session accepted or resumed work.')
    return '\n'.join(lines) + '\n'
