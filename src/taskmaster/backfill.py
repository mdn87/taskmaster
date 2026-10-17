"""Explicit, bounded import of provider session metadata into local trail context."""
import copy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import uuid

from . import config, publication, registry, timefmt, watch

SESSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z')
MAX_FILE = 64 * 1024 * 1024
MAX_LINE = 4 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return timefmt.fmt(parsed.astimezone(timezone.utc))
    except (ValueError, TypeError, AttributeError):
        return None


def scopes(settings):
    paths, keys = {}, {}
    for rule in settings['rules']:
        target = rule['namespace'] + '/' + rule['id']
        if Path(rule['workspace']).is_absolute():
            paths.setdefault(watch.normalized(rule['workspace']), []).append(target)
        for key in rule.get('workspace_keys', []):
            keys.setdefault(key, []).append(target)
    return paths, keys


def targets(cwd, paths, keys):
    if not isinstance(cwd, str) or not cwd or not Path(cwd).is_absolute():
        return []
    normalized = watch.normalized(cwd)
    return sorted(set(paths.get(normalized, []) + keys.get(hashlib.sha256(normalized.encode()).hexdigest(), [])))


def claude_project(directory, paths, keys, budget=None):
    candidates, gaps = [], []
    budget = budget if budget is not None else [0]
    files = []
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= 2048:
                raise watch.WatchError('Claude project exceeds 2048 direct entries')
            if entry.name.endswith('.jsonl'):
                files.append(Path(entry.path))
                if len(files) > 512:
                    raise watch.WatchError('Claude project exceeds 512 direct session files')
    files.sort()
    for file in files:
        if not SESSION.fullmatch(file.stem):
            continue
        try:
            watch.plain(file)
        except (watch.WatchError, OSError):
            gaps.append({'source': str(file), 'reason': 'not a plain file'})
            continue
        stat = file.stat()
        if stat.st_size > MAX_FILE:
            gaps.append({'source': str(file), 'reason': 'session file exceeds 64 MiB'})
            continue
        if budget[0] + stat.st_size > MAX_TOTAL:
            raise watch.WatchError('backfill source budget exceeds 512 MiB')
        matches, conflict, digest = {}, False, hashlib.sha256()
        with file.open('rb') as stream:
            line_number, read_bytes = 0, 0
            while raw := stream.readline(MAX_LINE + 1):
                line_number += 1
                read_bytes += len(raw)
                budget[0] += len(raw)
                if budget[0] > MAX_TOTAL:
                    raise watch.WatchError('backfill source budget exceeds 512 MiB')
                digest.update(raw)
                if len(raw) > MAX_LINE or read_bytes > MAX_FILE:
                    conflict = True
                    break
                try:
                    record = watch.owner_json(raw)
                except (ValueError, RecursionError):
                    conflict = True
                    break
                if not isinstance(record, dict):
                    continue
                sid = record.get('sessionId')
                if sid is not None and sid != file.stem:
                    conflict = True
                    break
                at = timestamp(record.get('timestamp'))
                if sid != file.stem or at is None:
                    continue
                for target in targets(record.get('cwd'), paths, keys):
                    evidence = {'source': str(file.resolve()), 'line': line_number,
                                'sha256': hashlib.sha256(raw).hexdigest(),
                                'fields': ['sessionId', 'cwd', 'timestamp'], 'cwd': record['cwd']}
                    old = matches.get(target)
                    if old is None:
                        matches[target] = {'provider': 'claude', 'session_id': sid, 'objective': target,
                                           'first': at, 'last': at, 'evidence': [evidence]}
                    else:
                        old['first'], old['last'] = min(old['first'], at), max(old['last'], at)
                        old['evidence'] = [old['evidence'][0], evidence]
        after = file.stat()
        if conflict or (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            gaps.append({'source': str(file), 'reason': 'conflicting, malformed, oversized or changing metadata'})
            continue
        for candidate in matches.values():
            for evidence in candidate['evidence']:
                evidence['file_sha256'] = digest.hexdigest()
            candidates.append(candidate)
    return candidates, gaps


def codex_database(database, paths, keys, budget=None):
    candidates, gaps = [], []
    budget = budget if budget is not None else [0]
    watch.plain(Path(database))
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            connection.execute('PRAGMA query_only = ON')
            records = connection.execute('SELECT id,cwd,rollout_path,created_at,updated_at FROM threads').fetchmany(10001)
    except sqlite3.Error as exc:
        raise watch.WatchError('Codex metadata database is unreadable or unsupported') from exc
    if len(records) > 10000:
        raise watch.WatchError('Codex metadata exceeds 10000 threads')
    for sid, cwd, rollout, created, updated in records:
        matching = targets(cwd, paths, keys)
        if not matching or not isinstance(sid, str) or not SESSION.fullmatch(sid):
            continue
        try:
            file = Path(rollout)
            watch.plain(file)
            with file.open('rb') as stream:
                raw = stream.readline(MAX_LINE + 1)
            budget[0] += len(raw)
            if budget[0] > MAX_TOTAL:
                raise watch.WatchError('backfill source budget exceeds 512 MiB')
            record = watch.owner_json(raw)
            payload = record.get('payload', {})
            if len(raw) > MAX_LINE or not isinstance(payload, dict) or record.get('type') != 'session_meta' or payload.get('id') != sid or not isinstance(payload.get('cwd'), str) or watch.normalized(payload['cwd']) != watch.normalized(cwd):
                raise ValueError('metadata disagreement')
            first = timefmt.fmt(datetime.fromtimestamp(created, timezone.utc))
            last = timefmt.fmt(datetime.fromtimestamp(updated, timezone.utc))
            for target in matching:
                candidates.append({'provider': 'codex', 'session_id': sid, 'objective': target, 'first': first, 'last': last,
                    'evidence': [{'source': str(file.resolve()), 'line': 1, 'sha256': hashlib.sha256(raw).hexdigest(),
                                  'fields': ['payload.id', 'payload.cwd'], 'cwd': cwd, 'database': str(Path(database).resolve())}]})
        except (OSError, ValueError, TypeError, OverflowError):
            if budget[0] > MAX_TOTAL:
                raise watch.WatchError('backfill source budget exceeds 512 MiB')
            gaps.append({'session_id': sid, 'reason': 'Codex database and local rollout metadata not corroborated'})
    return candidates, gaps


def resolve(candidates):
    grouped, accepted, conflicts = {}, [], []
    for candidate in candidates:
        grouped.setdefault(candidate['session_id'], []).append(candidate)
    for sid, group in sorted(grouped.items()):
        if len({(c['provider'], c['objective']) for c in group}) != 1:
            conflicts.append({'session_id': sid, 'reason': 'multiple providers or matching objectives'})
            continue
        candidate = copy.deepcopy(group[0])
        candidate['first'] = min(c['first'] for c in group)
        candidate['last'] = max(c['last'] for c in group)
        candidate['evidence'] = [e for c in group for e in c['evidence']]
        accepted.append(candidate)
    return accepted, conflicts


def merge(previous, candidates, overrides, now, max_age):
    value = copy.deepcopy(previous) if previous is not None else {'schema': 'taskmaster.registry/v1', 'evaluated_at': now,
             'max_age_seconds': max_age, 'status': 'unavailable', 'sessions': []}
    registry.validate(value)
    rows = {r['run_id']: r for r in value['sessions']}
    for candidate in candidates:
        sid, provider, target = candidate['session_id'], candidate['provider'], candidate['objective']
        rid = 'dias:codex-hook:' + hashlib.sha256(sid.encode()).hexdigest()
        if registry.resume_identity(rid, {'provider': provider, 'session_id': sid}) is None:
            raise watch.WatchError('invalid backfill identity')
        if not timefmt.is_valid(candidate['first']) or not timefmt.is_valid(candidate['last']) or not candidate['first'] <= candidate['last'] <= now:
            raise watch.WatchError('invalid or future provider timestamps')
        row = rows.get(rid)
        if row is None:
            row = {'run_id': rid, 'provider': provider, 'session_id': sid, 'objective': overrides.get(rid, target),
                   'provenance': 'operator', 'first_seen_at': candidate['first'], 'last_seen_at': candidate['last'],
                   'active': False, 'assignments': []}
            rows[rid] = row
            if row['objective'] is not None and row['objective'] != target:
                row['assignments'].append({'objective': row['objective'], 'provenance': 'operator',
                                          'registered_at': now, 'last_seen_at': now, 'ended_at': None})
        elif row['provider'] not in (None, provider) or row['session_id'] not in (None, sid):
            raise watch.WatchError('backfill conflicts with existing provider identity')
        row['provider'], row['session_id'] = provider, sid
        row['first_seen_at'] = min(row['first_seen_at'], candidate['first'])
        if not any(a['objective'] == target for a in row['assignments']):
            # Preserve current/operator assignments and add verified history only.
            row['assignments'].append({'objective': target, 'provenance': 'operator', 'registered_at': candidate['first'],
                                      'last_seen_at': candidate['last'], 'ended_at': None if row['objective'] == target else now})
    value['sessions'] = sorted(rows.values(), key=lambda r: r['run_id'])
    return registry.validate(value)


def run(config_path, claude_projects, codex_db, apply=False):
    if not claude_projects and not codex_db:
        raise watch.WatchError('specify --claude-project or --codex-db')
    cfg = config.load(config_path)
    if cfg.watch is None:
        raise watch.WatchError('watch is not configured')
    settings = watch.load_settings(cfg.watch, cfg)
    paths, keys = scopes(settings)
    candidates, gaps, budget = [], [], [0]
    for project in claude_projects:
        watch.plain(Path(project), directory=True)
        found, skipped = claude_project(Path(project), paths, keys, budget)
        candidates.extend(found)
        gaps.extend(skipped)
    if codex_db:
        found, skipped = codex_database(codex_db, paths, keys, budget)
        candidates.extend(found)
        gaps.extend(skipped)
    candidates, conflicts = resolve(candidates)
    gaps.extend(conflicts)
    now = timefmt.now()
    result = {'applied': False, 'checked_at': now, 'candidates': candidates, 'gaps': gaps,
              'unmatched_objectives': sorted({r['namespace']+'/'+r['id'] for r in settings['rules']} - {c['objective'] for c in candidates})}
    if apply:
        state = Path(settings['state_dir'])
        state.mkdir(parents=True, exist_ok=True)
        with publication.writer_lock(state / 'registrations'):
            previous = watch.read_session_registry(state / 'session-registry.json')
            overrides = watch.read_json(state / 'registrations.json', {'overrides': {}})['overrides']
            merged = merge(previous, candidates, overrides, now, 2*settings['interval_seconds']+10)
            receipt = state / 'backfills' / (now.replace(':', '') + '-' + uuid.uuid4().hex)
            receipt.mkdir(parents=True)
            if previous is not None:
                watch.atomic_json(receipt / 'registry-before.json', previous)
            watch.atomic_json(receipt / 'verified-sources.json', result)
            watch.atomic_json(state / 'session-registry.json', merged)
            result['applied'], result['receipt'] = True, str(receipt)
            result['consumer_refresh'] = 'pending'
            watch.atomic_json(receipt / 'result.json', result)
            try:
                watch.project_sessions(settings, timefmt.now())
            except (OSError, ValueError, KeyError, TypeError):
                result['consumer_refresh'] = 'failed'
                watch.atomic_json(receipt / 'result.json', result)
                raise watch.WatchError(f'sessions imported; consumer refresh failed; receipt: {receipt}; retry refresh') from None
            result['consumer_refresh'] = 'complete'
            watch.atomic_json(receipt / 'result.json', result)
    return result
