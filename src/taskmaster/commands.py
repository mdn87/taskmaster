"""Local slash-command controls; explicit session choices outrank detection."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shlex

from . import config, publication, timefmt, watch


def current_session():
    explicit = os.environ.get('DIAS_RUN_ID')
    if explicit:
        return explicit
    # Only provider-supplied identity, never a title or the latest visible card.
    # Dias's cooperative lifecycle adapter uses this prefix for both providers.
    for variable in ('CODEX_THREAD_ID', 'CLAUDE_SESSION_ID'):
        value = os.environ.get(variable)
        if value:
            return 'dias:codex-hook:' + hashlib.sha256(value.encode('utf-8')).hexdigest()
    raise watch.WatchError('current session identity unavailable; specify --session from /taskmaster sessions')


def execute(config_path, text):
    tokens = shlex.split(text)
    if tokens and tokens[0] in ('/taskmaster', 'taskmaster'):
        tokens.pop(0)
    if not tokens or tokens == ['help']:
        return {'commands': ['/taskmaster status', '/taskmaster trails', '/taskmaster sessions',
                             '/taskmaster use NAMESPACE/ID [--session DIAS_RUN_ID]',
                             '/taskmaster clear [--session DIAS_RUN_ID]',
                             '/taskmaster auto [--session DIAS_RUN_ID]', '/taskmaster refresh']}
    cfg = config.load(config_path)
    if cfg.watch is None:
        raise watch.WatchError('watch is not configured')
    settings = watch.load_settings(cfg.watch, cfg)
    state = Path(settings['state_dir'])
    command = tokens.pop(0)
    if command == 'refresh' and not tokens:
        return watch.tick(config_path, cfg.watch)
    if command == 'trails' and not tokens:
        return {'trails': [{'objective': r['namespace'] + '/' + r['id'],
                            'workspace': r['workspace'], 'project_ids': r['project_ids']} for r in settings['rules']]}
    if command == 'status' and not tokens:
        value = watch.read_json(state / 'status.json', {'state': 'not_started'})
        if 'checked_at' in value:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(value['checked_at'].replace('Z', '+00:00'))).total_seconds()
            value['heartbeat_age_seconds'] = max(0, int(age))
            if age > 2 * settings['interval_seconds'] + 10:
                value['state'] = 'watcher_not_refreshing'
        value['last_error'] = watch.read_json(state / 'last-error.json', {})
        value['progress'] = watch.read_json(state / 'progress.json', {})
        return value
    state.mkdir(parents=True, exist_ok=True)
    with publication.writer_lock(state / 'registrations'):
        if command == 'sessions' and not tokens:
            return watch.project_sessions(settings, timefmt.now())
        if command not in ('use', 'clear', 'auto'):
            raise watch.WatchError('unknown command; use /taskmaster help')
        target = tokens.pop(0) if command == 'use' and tokens else None
        if command == 'use' and target not in {r['namespace'] + '/' + r['id'] for r in settings['rules']}:
            raise watch.WatchError('unknown trail; use /taskmaster trails')
        if not tokens:
            session = current_session()
        elif len(tokens) == 2 and tokens[0] == '--session':
            session = tokens[1]
        else:
            raise watch.WatchError('expected --session DIAS_RUN_ID')
        if not re.fullmatch('[A-Za-z0-9._:-]{1,200}', session):
            raise watch.WatchError('invalid session id')
        before = watch.project_sessions(settings, timefmt.now())
        if before['status'] != 'current' or not any(row['run_id'] == session for row in before['sessions']):
            raise watch.WatchError('session is not reported by live Dias; use /taskmaster sessions')
        path = state / 'registrations.json'
        registry = watch.read_json(path, {'overrides': {}, 'history': []})
        prior = registry['overrides'].get(session, 'auto')
        if command == 'auto':
            registry['overrides'].pop(session, None)
        else:
            if session not in registry['overrides'] and len(registry['overrides']) >= 512:
                raise watch.WatchError('registration capacity reached')
            registry['overrides'][session] = target
        registry['history'] = (registry['history'] + [{'at': timefmt.now(), 'run_id': session,
                                                     'before': prior, 'after': 'auto' if command == 'auto' else target}])[-512:]
        watch.atomic_json(path, registry)
        return watch.project_sessions(settings, timefmt.now())
