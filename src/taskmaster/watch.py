"""Bounded local discovery, verified membership, and atomic consumer refresh.

Owner stores are never written. Only direct allowlisted records are captured;
private children and provider transcripts are never traversed.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import urllib.request
import uuid

from . import builder, config, publication, readmodel, report, sources, timefmt, registry as session_registry
from .canonical import canonical, objective_key, sha256_hex
from .identity import validate_id


class WatchError(ValueError):
    pass


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2, ensure_ascii=True) + '\n').encode('ascii'))


def atomic_bytes(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def owner_json(raw):
    # Native owner process ticks can exceed JS integers. Raw records remain
    # private; only validated identity fields and SHA-256 enter our metadata.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise WatchError('duplicate owner field')
            result[key] = value
        return result
    def nonfinite(_):
        raise WatchError('nonfinite owner value')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise WatchError('record must be an object')
    return value


def read_json(path, default=None):
    try:
        return owner_json(sources.read_small(Path(path)))
    except sources.SourceError:
        if not Path(path).exists() and default is not None:
            return default
        raise


def normalized(value):
    # Explicit paths only; no suffix, title, prefix or fuzzy matching.
    return value.replace('\\', '/').rstrip('/').lower() if os.name == 'nt' else value.rstrip('/')


def load_settings(path, cfg):
    path = Path(path).absolute()
    raw = read_json(path)
    allowed = {'schema_version', 'source', 'state_dir', 'export', 'interval_seconds', 'rules', 'dias_url', 'handoffs'}
    if set(raw) - allowed or type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
        raise WatchError('unsupported watch configuration')
    for key in ('source', 'state_dir', 'export'):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise WatchError('watch paths must be explicit')
        raw[key] = str((path.parent / raw[key]).absolute())
    interval = raw.get('interval_seconds', 30)
    if type(interval) is not int or not 5 <= interval <= 3600:
        raise WatchError('interval_seconds must be 5..3600')
    raw['interval_seconds'] = interval
    if 'dias_url' in raw and not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', raw['dias_url']):
        raise WatchError('Dias must use an explicit IPv4 loopback URL')
    objectives = {(o.namespace, o.id) for o in cfg.objectives}
    rules = raw.get('rules')
    if not isinstance(rules, list) or not 1 <= len(rules) <= 64:
        raise WatchError('watch requires 1..64 rules')
    seen = set()
    for rule in rules:
        required = {'namespace', 'id', 'workspace', 'record', 'anchors', 'project_ids'}
        if not isinstance(rule, dict) or not required <= set(rule) or set(rule) - required - {'workspace_keys', 'handoff_only'}:
            raise WatchError('invalid membership rule')
        if rule.get('handoff_only', True) is not True:
            raise WatchError('handoff_only may only be true')
        if not isinstance(rule['namespace'], str) or not isinstance(rule['id'], str):
            raise WatchError('invalid objective identity')
        key = (rule['namespace'], rule['id'])
        if key not in objectives or key in seen:
            raise WatchError('rule must name one unique configured objective')
        seen.add(key)
        if not isinstance(rule['workspace'], str) or not rule['workspace']:
            raise WatchError('exact workspace is required')
        if rule['record'] not in ('request-summary.json', 'subject-manifest.json'):
            raise WatchError('unsupported membership record')
        for field in ('anchors', 'project_ids'):
            if (not isinstance(rule[field], list) or len(rule[field]) > 64
                    or not all(isinstance(v, str) and v for v in rule[field])):
                raise WatchError('invalid rule selectors')
        if rule['record'] == 'subject-manifest.json' and not rule['anchors']:
            raise WatchError('review rule requires exact anchors')
        if rule['record'] == 'request-summary.json' and rule['anchors']:
            raise WatchError('dispatch rule does not accept review anchors')
        keys = rule.get('workspace_keys', [])
        if not isinstance(keys, list) or len(keys) > 64 or not all(isinstance(k, str) and re.fullmatch('[a-f0-9]{64}', k) for k in keys):
            raise WatchError('invalid session workspace keys')
    from . import handoffs
    try:
        return handoffs.configure(raw, path.parent)
    except ValueError as exc:
        raise WatchError(str(exc)) from exc


def plain(path, directory=False):
    info = path.lstat()
    if sources._is_link(info) or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise WatchError('non-plain source path')


def record_bytes(path):
    plain(path)
    with path.open('rb') as stream:
        raw = stream.read(sources.MAX_RECORD_BYTES + 1)
    if len(raw) > sources.MAX_RECORD_BYTES:
        raise WatchError('record over bounds')
    return raw


def capture(run):
    plain(run, True)
    names = set(sources.RECORD_BASENAMES)
    ledger = run / 'owner-attempts'
    if ledger.exists():
        plain(ledger, True)
        with os.scandir(ledger) as scan:
            for index, entry in enumerate(scan):
                if index >= sources.MAX_FOOTPRINT_ENTRIES:
                    raise WatchError('ledger over bounds')
                if entry.name.endswith(sources.LEDGER_SUFFIXES):
                    names.add('owner-attempts/' + entry.name)
    data = {}
    for name in sorted(names):
        path = run / name
        if path.exists() or path.is_symlink():
            data[name] = record_bytes(path)
    if sum(map(len, data.values())) > sources.MAX_FOOTPRINT_BYTES:
        raise WatchError('run over bounds')
    return data


def matches(rule, docs):
    doc = docs.get(rule['record'], {})
    workspace = doc.get('workspace')
    return (isinstance(workspace, str) and normalized(workspace) == normalized(rule['workspace'])
            and (rule['record'] != 'subject-manifest.json' or doc.get('anchor_paths') == rule['anchors']))


def verify_run(run_id, data, rule):
    parsed = {name: owner_json(raw) for name, raw in data.items() if name.endswith('.json')}
    for row in parsed.values():
        if row.get('run_id', run_id) != run_id:
            raise WatchError('conflicting run identity')
        if row.get('objective_id', rule['id']) != rule['id']:
            raise WatchError('conflicting objective binding')
    if not matches(rule, parsed):
        raise WatchError('membership changed during capture')
    result = parsed.get('result.json', {})
    state = parsed.get('state.json', {})
    if result.get('run_id') != run_id and state.get('run_id') != run_id:
        raise WatchError('run identity unrecorded')
    if rule['record'] == 'subject-manifest.json':
        if result.get('command_kind') != 'cross-review':
            raise WatchError('review result not yet bound')
        try:
            builder._verify_review_manifest({k: (sha256_hex(v), v) for k, v in data.items()}, result, run_id)
        except builder._Failure:
            raise WatchError('invalid review subject binding') from None
    else:
        hashes = {row['task_sha256'] for name, row in parsed.items()
                  if name in ('request-summary.json', 'result.json', 'agent-assignment.json', 'assignment-failure.json')
                  and row.get('task_sha256')}
        if len(hashes) != 1 or not re.fullmatch('[0-9a-f]{64}', next(iter(hashes))):
            raise WatchError('task identity inconsistent or missing')


def discover(settings, cache=None):
    root = Path(settings['source'])
    plain(root, True)
    runs, memberships, provenance, quarantine = {}, {}, [], []
    total = 0
    cache = {} if cache is None else cache
    next_cache = {}
    with os.scandir(root) as scan:
        for index, entry in enumerate(scan):
            if index >= sources.MAX_FOOTPRINT_ENTRIES:
                raise WatchError('source directory over bounds')
            if not entry.is_dir(follow_symlinks=False):
                continue
            run = Path(entry.path)
            try:
                plain(run, True)
                validate_id(entry.name, 'run_id')
                docs = {}
                headers = []
                for name in sorted({rule['record'] for rule in settings['rules']}):
                    path = run / name
                    if path.exists() or path.is_symlink():
                        plain(path)
                        info = path.stat()
                        headers.append([name, info.st_size, info.st_mtime_ns, info.st_ctime_ns])
                # Only negative discovery is cached. Admitted runs are always
                # reread and verified twice, never trusted by metadata alone.
                if cache.get(entry.name) == headers:
                    next_cache[entry.name] = headers
                    continue
                for name in {rule['record'] for rule in settings['rules']}:
                    path = run / name
                    if path.exists() or path.is_symlink():
                        raw = record_bytes(path)
                        total += len(raw)
                        if total > sources.MAX_FOOTPRINT_BYTES:
                            raise WatchError('scan over bounds')
                        docs[name] = owner_json(raw)
                # A handoff-only trail records notes and binds sessions; it never claims runs.
                candidates = [r for r in settings['rules'] if not r.get('handoff_only') and matches(r, docs)]
                if not candidates:
                    next_cache[entry.name] = headers
                    continue
                if len(candidates) != 1:
                    raise WatchError('ambiguous membership')
                rule = candidates[0]
                data = capture(run)
                total += sum(map(len, data.values()))
                if total > sources.MAX_FOOTPRINT_BYTES:
                    raise WatchError('scan over bounds')
                verify_run(entry.name, data, rule)
                if capture(run) != data:
                    raise WatchError('source changed during read')
                runs[entry.name] = data
                key = rule['namespace'] + '/' + rule['id']
                memberships.setdefault(key, []).append(entry.name)
                provenance.append({'objective': key, 'run_id': entry.name, 'record': rule['record'],
                                   'json_pointer': '/anchor_paths' if rule['anchors'] else '/workspace',
                                   'sha256': sha256_hex(data[rule['record']]),
                                   'records': {name: sha256_hex(raw) for name, raw in data.items()}})
            except (WatchError, ValueError, TypeError, OSError, RecursionError) as exc:
                quarantine.append({'run_id': entry.name, 'reason': str(exc) if isinstance(exc, WatchError) else 'invalid source record'})
            if total > sources.MAX_FOOTPRINT_BYTES:
                raise WatchError('scan over bounds')
    cache.clear()
    cache.update(next_cache)
    return runs, {key: sorted(ids) for key, ids in memberships.items()}, provenance, quarantine


def session_bindings(sessions, rules, overrides):
    available = {r['namespace'] + '/' + r['id'] for r in rules}
    result = []
    for session in sessions:
        run_id = session.get('runId')
        if not isinstance(run_id, str) or not re.fullmatch('[A-Za-z0-9._:-]{1,200}', run_id):
            continue
        projects = session.get('projectIds', [])
        if not isinstance(projects, list) or not all(isinstance(p, str) for p in projects):
            continue
        keys = session.get('workspaceKeys', [])
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            continue
        exact = {r['namespace'] + '/' + r['id'] for r in rules if set(r.get('workspace_keys', [])) & set(keys)}
        candidates = sorted(exact or {r['namespace'] + '/' + r['id'] for r in rules if set(r['project_ids']) & set(projects)})
        if run_id in overrides:
            target = overrides[run_id]
            objective = target if target in available else None
            provenance = 'operator' if target is None or target in available else 'invalid_override'
        else:
            objective = candidates[0] if len(candidates) == 1 else None
            provenance = 'detected' if objective else ('ambiguous' if candidates else 'unassigned')
        result.append({'run_id': run_id, 'objective': objective, 'provenance': provenance, 'candidates': candidates})
    return sorted(result, key=lambda row: row['run_id'])


def project_sessions(settings, now):
    state = Path(settings['state_dir'])
    registry = read_json(state / 'registrations.json', {'overrides': {}, 'history': []})
    rows, status, feed_sessions, ended, ended_bindings = [], 'unconfigured', [], [], []
    if settings.get('dias_url'):
        try:
            with urllib.request.urlopen(settings['dias_url'] + '/api/v1/trails/context', timeout=3) as response:
                raw = response.read(5 * 1024 * 1024 + 1)
            if len(raw) > 5 * 1024 * 1024:
                raise WatchError('session feed over bounds')
            feed = owner_json(raw)
            if feed.get('schemaVersion') != 1 or not isinstance(feed.get('sessions'), list):
                raise WatchError('invalid session feed')
            feed_sessions = feed['sessions']
            if len(feed_sessions) > 512 or any(not isinstance(row, dict) for row in feed_sessions):
                raise WatchError('invalid session feed')
            ids = [row.get('runId') for row in feed_sessions]
            if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
                raise WatchError('duplicate or invalid session identity')
            rows = session_bindings(feed_sessions, settings['rules'], registry['overrides'])
            ended = feed.get('endedSessions', [])
            if not isinstance(ended, list) or len(ended) > session_registry.MAX_SESSIONS or any(not isinstance(row, dict) for row in ended):
                raise WatchError('invalid session exits')
            exit_ids = [row.get('runId') for row in ended]
            if any(not isinstance(i, str) for i in exit_ids) or len(set(exit_ids)) != len(exit_ids):
                raise WatchError('duplicate or invalid session exit identity')
            for row in ended:
                if (session_registry.resume_identity(row['runId'], row.get('resume')) is None
                        or not timefmt.is_valid(row.get('observedAt')) or row['observedAt'] > now):
                    raise WatchError('invalid session exit metadata')
            ended_bindings = session_bindings(ended, settings['rules'], registry['overrides'])
            if len(ended_bindings) != len(ended):
                raise WatchError('invalid session exit matching context')
            status = 'current'
        except (OSError, ValueError):
            status = 'unavailable'
            rows, feed_sessions, ended, ended_bindings = [], [], [], []
    payload = {'schema': 'taskmaster.sessions/v1', 'evaluated_at': now,
               'max_age_seconds': 2 * settings['interval_seconds'] + 10, 'status': status, 'sessions': rows}
    history_path = state / 'session-registry.json'
    try:
        previous = read_session_registry(history_path)
        history = session_registry.reconcile(previous, rows, feed_sessions, registry['overrides'], now, status, payload['max_age_seconds'],
                                             ended=ended, ended_bindings=ended_bindings)
    except (ValueError, TypeError, KeyError) as exc:
        raise WatchError('session registry update failed; previous history retained') from exc
    atomic_json(history_path, history)
    atomic_json(Path(settings['export']).parent / 'registry.json', history)
    atomic_bytes(Path(settings['export']).parent / 'session-registry-report.md', session_registry.render(history).encode('utf-8'))
    atomic_json(Path(settings['export']).parent / 'sessions.json', payload)
    return payload


def read_session_registry(path):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(session_registry.MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > session_registry.MAX_BYTES:
        raise WatchError('session registry exceeds size limit')
    return session_registry.validate(owner_json(raw))


def tick(config_path, settings_path, *, now=None, owner_cache=None, recover_invalid_export=False):
    cfg = config.load(config_path)
    settings = load_settings(settings_path, cfg)
    state = Path(settings['state_dir'])
    state.mkdir(parents=True, exist_ok=True)
    with publication.writer_lock(state / 'cycle'):
        return _tick(cfg, settings, now or timefmt.now(), owner_cache, recover_invalid_export)


def _tick(cfg, settings, now, owner_cache=None, recover_invalid_export=False):
    state, destination = Path(settings['state_dir']), Path(settings['export'])
    previous = read_json(state / 'status.json', {})
    rule_map = {(r['namespace'], r['id']): r for r in settings['rules']}
    error = None
    started = time.monotonic()
    atomic_json(state / 'progress.json', {'phase': 'discovering', 'started_at': timefmt.now()})
    try:
        scope_digest = sha256_hex(canonical({'source': settings['source'], 'rules': settings['rules']}))
        cached = read_json(state / 'discovery-cache.json', {})
        cache = cached.get('entries', {}) if cached.get('scope_digest') == scope_digest else {}
        runs, memberships, provenance, quarantine = discover(settings, cache)
        atomic_json(state / 'discovery-cache.json', {'scope_digest': scope_digest, 'entries': cache})
        # A lost or newly conflicting established member is an observation
        # failure, not permission to silently drop it from the trail.
        for objective in cfg.objectives:
            if (objective.namespace, objective.id) not in rule_map:
                continue
            key = objective.namespace + '/' + objective.id
            seed = read_json(objective.annotation)['run_ids']
            expected = set(seed) | set(previous.get('memberships', {}).get(key, []))
            if not expected.issubset(memberships.get(key, [])):
                raise WatchError('previously verified member unavailable or no longer matches')
        fingerprint = sha256_hex(canonical({'memberships': memberships, 'provenance': sorted(provenance, key=lambda p: p['run_id'])}))
        generation = Path(previous['generation']) if previous.get('fingerprint') == fingerprint else state / 'captures' / uuid.uuid4().hex
        if previous.get('fingerprint') != fingerprint:
            for run_id, data in runs.items():
                for name, raw in data.items():
                    target = generation / 'store' / run_id / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
            for rule in settings['rules']:
                key = rule['namespace'] + '/' + rule['id']
                atomic_json(generation / (objective_key(rule['namespace'], rule['id']) + '.json'),
                            {'objective_id': rule['id'], 'run_ids': memberships.get(key, [])})
            atomic_json(generation / 'provenance.json', {'observed_at': now, 'source': settings['source'], 'runs': provenance})
    except (WatchError, sources.SourceError, ValueError, OSError) as exc:
        error = str(exc) if isinstance(exc, WatchError) else 'source unavailable or invalid'
        memberships = previous.get('memberships', {})
        generation = Path(previous.get('generation', str(state / 'unavailable')))
        fingerprint = previous.get('fingerprint')
        quarantine = [{'run_id': '*', 'reason': error}]
    objectives = []
    for objective in cfg.objectives:
        rule = rule_map.get((objective.namespace, objective.id))
        if rule is None:
            objectives.append(objective)
            continue
        # Stable logical binding, independent of immutable capture directory.
        # Changing source scope still invalidates retention as before.
        binding = sha256_hex(canonical({'configured_binding': objective.binding_sha256,
                                       'source': settings['source'], 'rule': {k: v for k, v in rule.items() if k not in ('project_ids', 'workspace_keys')}}))
        store = replace(objective.run_store, path=(generation / 'store') if error is None else state / 'unavailable', mode='live', manifest=None)
        annotation = generation / (objective_key(objective.namespace, objective.id) + '.json')
        objectives.append(replace(objective, run_store=store, annotation=annotation, binding_sha256=binding))
    destination.parent.mkdir(parents=True, exist_ok=True)
    discovery_seconds = round(time.monotonic() - started, 3)
    atomic_json(state / 'progress.json', {'phase': 'publishing', 'started_at': now, 'discovery_seconds': discovery_seconds})
    if owner_cache is not None:
        owner_cache['versions'] = {}  # Recheck the owner pin once per cycle.
    doc = publication.refresh(replace(cfg, objectives=objectives), destination, now, owner_cache=owner_cache,
                              recover_invalid_export=recover_invalid_export)
    atomic_bytes(destination.parent / 'trails-report.md', report.render(readmodel.show(doc, now)))
    with publication.writer_lock(state / 'registrations'):
        sessions = project_sessions(settings, timefmt.now())
    from . import handoffs
    handoff_status = handoffs.publish(settings, now, time.monotonic() - started)
    failed = sum(e['observation']['attempt_status'] == 'failed' for e in doc['entries'])
    status = {'schema_version': 1, 'checked_at': now, 'pid': os.getpid(), 'state': 'source_failed' if error else ('degraded' if failed else 'running'),
              'interval_seconds': settings['interval_seconds'], 'generation': str(generation), 'fingerprint': fingerprint,
              'memberships': memberships, 'quarantine': quarantine[:512], 'quarantine_count': len(quarantine),
              'snapshot_id': doc['snapshot_id'], 'failed_objectives': failed,
              'session_status': sessions['status'], 'handoff_status': handoff_status['status']}
    status['discovery_seconds'] = discovery_seconds
    status['cycle_seconds'] = round(time.monotonic() - started, 3)
    atomic_json(state / 'status.json', status)
    atomic_json(state / 'progress.json', {'phase': 'idle', 'finished_at': timefmt.now()})
    return status


def serve(config_path, settings_path):
    cfg = config.load(config_path)
    settings = load_settings(settings_path, cfg)
    state = Path(settings['state_dir'])
    state.mkdir(parents=True, exist_ok=True)
    with publication.writer_lock(state / 'daemon'):
        owner_cache = {}
        while True:
            started = time.monotonic()
            try:
                tick(config_path, settings_path, owner_cache=owner_cache)
            except Exception as exc:
                # Retry on next cycle, preserving the last export and its clock.
                atomic_json(state / 'last-error.json', {'at': timefmt.now(), 'error': type(exc).__name__})
            settings = load_settings(settings_path, config.load(config_path))
            time.sleep(max(1, settings['interval_seconds'] - (time.monotonic() - started)))
