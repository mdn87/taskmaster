"""Local slash-command controls; explicit session choices outrank detection."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shlex

from . import config, publication, timefmt, watch, registry as session_registry


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


def handoff_notes(settings, objective):
    """Local (provider, path) pairs for an objective's handoff notes that exist on this host."""
    rows = settings.get('handoffs') if isinstance(settings.get('handoffs'), list) else []
    return sorted((b['provider'], b['path']) for b in rows
                  if isinstance(b, dict) and b.get('objective') == objective
                  and isinstance(b.get('path'), str) and isinstance(b.get('provider'), str) and Path(b['path']).is_file())


def local_brief(cfg, tokens, cwd=None):
    """Render the brief here, never on the context owner: handoff paths are local (decision D3)."""
    from . import brief, export
    as_json = '--json' in tokens
    tokens = [t for t in tokens if t != '--json']
    settings_path = Path(cfg.watch)
    settings = watch.read_json(settings_path)
    if not isinstance(settings, dict) or not isinstance(settings.get('export'), str):
        raise watch.WatchError('watch settings name no export to brief from')
    rules = settings.get('rules') if isinstance(settings.get('rules'), list) else []
    if len(tokens) == 1 and tokens[0].count('/') == 1:
        objective = tokens[0]
    elif not tokens:
        here = watch.normalized(str(Path(cwd or os.getcwd()).absolute()))
        matches = sorted(r['namespace'] + '/' + r['id'] for r in rules
                         if isinstance(r, dict) and isinstance(r.get('workspace'), str)
                         and watch.normalized(r['workspace']) == here)
        if len(matches) != 1:
            raise watch.WatchError('this directory matches ' + (', '.join(matches) or 'no trail')
                                   + '; use /taskmaster brief NAMESPACE/ID')
        objective = matches[0]
    else:
        raise watch.WatchError('expected brief [NAMESPACE/ID] [--json]')
    doc, _ = export.load(settings_path.parent / settings['export'])
    namespace, objective_id = objective.split('/')
    value = brief.build(doc, namespace, objective_id, timefmt.now())
    if value is None:
        raise watch.WatchError(f'{objective} is not in the export')
    return value if as_json else brief.render(value, handoff_notes(settings, objective))


def execute(config_path, text):
    tokens = shlex.split(text)
    if tokens and tokens[0] in ('/taskmaster', 'taskmaster'):
        tokens.pop(0)
    if not tokens or tokens == ['help']:
        return {'commands': ['/taskmaster status', '/taskmaster trails', '/taskmaster sessions',
                             '/taskmaster sessions --all [--trail NAMESPACE/ID] [--format report]',
                             '/taskmaster use NAMESPACE/ID [--session DIAS_RUN_ID]',
                             '/taskmaster clear [--session DIAS_RUN_ID]',
                             '/taskmaster auto [--session DIAS_RUN_ID]', '/taskmaster handoffs', '/taskmaster refresh',
                             '/taskmaster brief [NAMESPACE/ID] [--json]',
                             '/taskmaster workspace add PATH']}
    cfg = config.load(config_path)
    if cfg.watch is None:
        raise watch.WatchError('watch is not configured')
    if tokens[0] == 'brief':
        return local_brief(cfg, tokens[1:])
    from .remote_owner import client_settings, forward_command
    client = client_settings(cfg.watch)
    if client is not None:
        return forward_command(client, text)
    settings = watch.load_settings(cfg.watch, cfg)
    state = Path(settings['state_dir'])
    command = tokens.pop(0)
    if command == 'refresh' and not tokens:
        return watch.tick(config_path, cfg.watch)
    if command == 'workspace' and len(tokens) == 2 and tokens[0] == 'add':
        from . import workspaces
        try:
            objective, changed, new = workspaces.ensure(config_path, tokens[1])
        except workspaces.WorkspaceError as exc:
            raise watch.WatchError(str(exc)) from exc
        return {'objective': objective, 'started': new, 'notes_bound': changed}
    if command == 'trails' and not tokens:
        return {'trails': [{'objective': r['namespace'] + '/' + r['id'],
                            'workspace': r['workspace'], 'project_ids': r['project_ids']} for r in settings['rules']]}
    if command == 'handoffs' and tokens in ([], ['--format', 'report']):
        from . import handoffs
        value = handoffs.read_projection(Path(settings['export']).parent/'handoffs.json', timefmt.now())
        return handoffs.render(value) if tokens else value
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
        if command == 'sessions':
            all_sessions, trail, output_format = False, None, 'json'
            while tokens:
                option = tokens.pop(0)
                if option == '--all':
                    all_sessions = True
                elif option == '--trail' and tokens:
                    trail, all_sessions = tokens.pop(0), True
                    if trail not in {r['namespace'] + '/' + r['id'] for r in settings['rules']}:
                        raise watch.WatchError('unknown trail; use /taskmaster trails')
                elif option == '--format' and tokens and tokens[0] == 'report':
                    output_format, all_sessions = tokens.pop(0), True
                else:
                    raise watch.WatchError('expected sessions [--all] [--trail NAMESPACE/ID] [--format report]')
            current = watch.project_sessions(settings, timefmt.now())
            if not all_sessions:
                return current
            history = watch.read_session_registry(state / 'session-registry.json')
            if trail:
                history = session_registry.for_trail(history, trail)
            return session_registry.render(history) if output_format == 'report' else history
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
        live = before['status'] == 'current' and any(row['run_id'] == session for row in before['sessions'])
        history = watch.read_session_registry(state / 'session-registry.json')
        known = history is not None and any(row['run_id'] == session for row in history['sessions'])
        if not live and (not known or command == 'auto'):
            raise watch.WatchError('automatic detection needs a live session; use/clear also accept registered historical sessions')
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
        result = watch.project_sessions(settings, timefmt.now())
        if not live:
            history = watch.read_session_registry(state / 'session-registry.json')
            return {**history, 'sessions': [row for row in history['sessions'] if row['run_id'] == session]}
        return result
