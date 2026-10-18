"""Durable session context, deliberately separate from execution evidence."""
import copy
import hashlib
import json
import re

from . import timefmt

MAX_BYTES = 4 * 1024 * 1024
MAX_SESSIONS = 4096
MAX_ASSIGNMENTS = 128
PROVENANCE = {'operator', 'detected', 'ambiguous', 'unassigned', 'invalid_override'}


def resume_identity(run_id, resume):
    if not isinstance(resume, dict) or set(resume) != {'provider', 'session_id'}:
        return None
    provider, session_id = resume['provider'], resume['session_id']
    if provider not in ('codex', 'claude') or not isinstance(session_id, str):
        return None
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', session_id):
        return None
    if run_id != 'dias:codex-hook:' + hashlib.sha256(session_id.encode()).hexdigest():
        return None
    return provider, session_id


def objective(value):
    return isinstance(value, str) and len(value) <= 512 and re.fullmatch(r'[A-Za-z0-9._:-]+/[A-Za-z0-9._:/-]+', value)


def validate(value):
    def require(condition):
        if not condition:
            raise ValueError('invalid session registry; existing history retained')
    require(isinstance(value, dict) and set(value) == {'schema', 'evaluated_at', 'max_age_seconds', 'status', 'sessions'})
    require(value['schema'] == 'taskmaster.registry/v1' and timefmt.is_valid(value['evaluated_at']))
    require(type(value['max_age_seconds']) is int and 10 <= value['max_age_seconds'] <= 7210)
    require(value['status'] in ('current', 'unavailable', 'unconfigured'))
    require(isinstance(value['sessions'], list) and len(value['sessions']) <= MAX_SESSIONS)
    seen = set()
    for row in value['sessions']:
        require(isinstance(row, dict) and set(row) == {'run_id', 'provider', 'session_id', 'objective', 'provenance', 'first_seen_at', 'last_seen_at', 'active', 'assignments'})
        require(isinstance(row['run_id'], str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,200}', row['run_id']) and row['run_id'] not in seen)
        seen.add(row['run_id'])
        require((row['provider'] is None and row['session_id'] is None) or resume_identity(row['run_id'], {'provider': row['provider'], 'session_id': row['session_id']}) is not None)
        require(row['objective'] is None or objective(row['objective']))
        require(row['provenance'] in PROVENANCE and type(row['active']) is bool)
        require(timefmt.is_valid(row['first_seen_at']) and timefmt.is_valid(row['last_seen_at']) and row['first_seen_at'] <= row['last_seen_at'])
        require(isinstance(row['assignments'], list) and len(row['assignments']) <= MAX_ASSIGNMENTS)
        open_assignments = []
        for assignment in row['assignments']:
            require(isinstance(assignment, dict) and set(assignment) == {'objective', 'provenance', 'registered_at', 'last_seen_at', 'ended_at'})
            require(objective(assignment['objective']) and assignment['provenance'] in PROVENANCE)
            require(timefmt.is_valid(assignment['registered_at']) and timefmt.is_valid(assignment['last_seen_at']) and assignment['registered_at'] <= assignment['last_seen_at'])
            require(assignment['ended_at'] is None or (timefmt.is_valid(assignment['ended_at']) and assignment['ended_at'] >= assignment['last_seen_at']))
            if assignment['ended_at'] is None:
                open_assignments.append(assignment)
        require(len(open_assignments) == (0 if row['objective'] is None else 1))
        require(not open_assignments or open_assignments[0]['objective'] == row['objective'])
    require(len((json.dumps(value, indent=2, ensure_ascii=True) + '\n').encode('ascii')) <= MAX_BYTES)
    return value


def reconcile(previous, bindings, feed, overrides, now, status, max_age, *, ended=(), ended_bindings=()):
    if previous is not None:
        validate(previous)
        if now < previous['evaluated_at']:
            raise ValueError('session registry clock moved backwards; existing history retained')
    rows = {row['run_id']: copy.deepcopy(row) for row in (previous or {}).get('sessions', [])}
    live = {row['run_id']: row for row in bindings} if status == 'current' else {}
    context = {row.get('runId'): row for row in feed}
    exits = {row['runId']: row for row in ended} if status == 'current' else {}
    exit_bindings = {row['run_id']: row for row in ended_bindings}
    observations = {}
    for run_id in live.keys() | exits.keys():
        event = context[run_id] if run_id in live else exits[run_id]
        seen_at = now if run_id in live else event['observedAt']
        if not timefmt.is_valid(seen_at) or seen_at > now:
            raise ValueError('invalid session exit time; existing history retained')
        identity = resume_identity(run_id, event.get('resume'))
        if run_id not in live and identity is None:
            raise ValueError('invalid session exit identity; existing history retained')
        is_new = run_id not in rows
        if run_id not in rows:
            rows[run_id] = {'run_id': run_id, 'provider': None, 'session_id': None, 'objective': None,
                            'provenance': 'unassigned', 'first_seen_at': seen_at, 'last_seen_at': seen_at,
                            'active': False, 'assignments': []}
        row = rows[run_id]
        if is_new or seen_at > row['last_seen_at'] or run_id in live:
            observations[run_id] = (live.get(run_id) or exit_bindings.get(run_id), seen_at)
            row['last_seen_at'] = max(row['last_seen_at'], seen_at)
        if identity is not None:
            if row['provider'] is not None and identity != (row['provider'], row['session_id']):
                raise ValueError('conflicting provider identity; existing history retained')
            row['provider'], row['session_id'] = identity
    for run_id, row in rows.items():
        row['active'] = run_id in live
        binding, observed_at = observations.get(run_id, (None, now))
        target, provenance = row['objective'], row['provenance']
        if binding is not None:
            target, provenance = binding['objective'], binding['provenance']
        elif run_id in overrides:
            target, provenance = overrides[run_id], 'operator'
        current = next((a for a in row['assignments'] if a['ended_at'] is None), None)
        if current and (target != current['objective'] or provenance != current['provenance']):
            current['ended_at'] = now
            current = None
        if target is not None and current is None:
            registered_at = now if run_id in overrides else observed_at
            current = {'objective': target, 'provenance': provenance, 'registered_at': registered_at, 'last_seen_at': registered_at, 'ended_at': None}
            row['assignments'].append(current)
        if current and run_id in observations:
            current['last_seen_at'] = max(current['last_seen_at'], observed_at)
        row['objective'], row['provenance'] = target, provenance
    return validate({'schema': 'taskmaster.registry/v1', 'evaluated_at': now, 'max_age_seconds': max_age,
                     'status': status, 'sessions': sorted(rows.values(), key=lambda row: row['run_id'])})


def for_trail(value, trail):
    return {**value, 'sessions': [row for row in value['sessions'] if any(a['objective'] == trail for a in row['assignments'])]}


def render(value):
    lines = ['taskmaster session registry v1', f"evaluated_at: {value['evaluated_at']}", f"status: {value['status']}"]
    for row in value['sessions']:
        lines.append(f"session: {row['provider'] or 'unknown'} {row['session_id'] or 'resume ID unavailable'}")
        lines.append(f"  card: {row['run_id']} active={'yes' if row['active'] else 'no'} current_trail={row['objective'] or 'unassigned'}")
        for a in row['assignments']:
            lines.append(f"  trail: {a['objective']} provenance={a['provenance']} registered_at={a['registered_at']} last_seen_at={a['last_seen_at']} ended_at={a['ended_at'] or 'current'}")
    return '\n'.join(lines) + '\n'
