"""Start a workspace trail for a project folder that no trail watches yet.

A workspace trail is an objective with id `workspace`, a watch rule keyed to the
exact folder, and the folder's `.Codex` and `.claude` handoff notes. Its annotation
starts empty: the watcher discovers the folder's Autowork runs through the rule.
Seeding runs here would make any run the watcher rejects fail every refresh.

Files are written in an order where every intermediate state still loads: the
annotation, then the objective in taskmaster.toml, then the watch rule and handoff
sources in the watch settings. Existing configuration is never rewritten, only
extended; a failed validation restores both files.
"""
import hashlib
import json
from pathlib import Path
import re

from . import config, handoffs, publication, watch

OBJECTIVE_ID = 'workspace'


class WorkspaceError(ValueError):
    pass


def _key(root):
    return hashlib.sha256(watch.normalized(str(root)).encode()).hexdigest()


def find(settings, root):
    """Objectives whose rule is keyed to exactly this folder."""
    key, exact = _key(root), watch.normalized(str(root))
    return [r['namespace'] + '/' + r['id'] for r in settings['rules']
            if key in r.get('workspace_keys', [])
            or (Path(r['workspace']).is_absolute() and watch.normalized(r['workspace']) == exact)]


def _namespace(root, taken):
    base = re.sub(r'[^a-z0-9._-]+', '-', root.name.lower()).strip('-.') or 'workspace'
    base = base[:100]
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f'{base}-{n}', n + 1
    return candidate


def _store(cfg):
    """The store existing workspace trails use, else the first live store."""
    for objective in cfg.objectives:
        if objective.id == OBJECTIVE_ID:
            return objective.run_store
    for objective in cfg.objectives:
        if objective.run_store.mode == 'live':
            return objective.run_store
    raise WorkspaceError('no live Autowork store is configured for a workspace trail')


def _toml_string(value):
    return json.dumps(value)  # A JSON string is a valid TOML basic string.


def ensure(config_path, root):
    """Return (objective, changed, started) for a workspace folder.

    `changed` is true when a trail was started or the folder's notes were newly bound.
    """
    config_path = Path(config_path).absolute()
    root = Path(root).absolute()
    if not root.is_dir():
        raise WorkspaceError(f'{root} is not a folder')
    cfg = config.load(config_path)
    if cfg.watch is None:
        raise WorkspaceError('Taskmaster has no watcher configured')
    settings_path = Path(cfg.watch).absolute()
    settings = watch.load_settings(settings_path, cfg)
    state = Path(settings['state_dir'])
    state.mkdir(parents=True, exist_ok=True)
    with publication.writer_lock(state / 'workspaces'):
        cfg = config.load(config_path)
        settings = watch.load_settings(settings_path, cfg)
        raw = watch.read_json(settings_path)
        existing = find(settings, root)
        if len(existing) > 1:
            raise WorkspaceError(f'{root} is claimed by several trails ({", ".join(existing)}); choose one in the watch settings')
        if existing:
            return existing[0], _add_sources(config_path, settings_path, raw, settings, existing[0], root), False
        if len(cfg.objectives) >= config.MAX_OBJECTIVES or len(raw['rules']) >= 64:
            raise WorkspaceError('Taskmaster already has the maximum of 64 trails; retire one before starting another')
        taken = {o.namespace for o in cfg.objectives if o.id == OBJECTIVE_ID}
        taken |= {o.run_store.alias for o in cfg.objectives}
        namespace = _namespace(root, taken)
        objective = namespace + '/' + OBJECTIVE_ID
        store = _store(cfg)
        annotation = settings_path.parent / 'annotations' / f'{namespace}-workspace.json'
        if annotation.exists():
            raise WorkspaceError(f'{annotation} already exists; not overwriting it')
        old_toml, old_settings = config_path.read_bytes(), settings_path.read_bytes()
        try:
            watch.atomic_json(annotation, {'objective_id': OBJECTIVE_ID, 'run_ids': []})
            try:
                rel = annotation.relative_to(config_path.parent).as_posix()
            except ValueError:
                rel = str(annotation)
            block = ('\n[[objectives]]\n'
                     f'namespace = {_toml_string(namespace)}\n'
                     f'id = {_toml_string(OBJECTIVE_ID)}\n'
                     f'annotation = {_toml_string(rel)}\n'
                     f'run_store = {_toml_string(store.alias)}\n'
                     f'label = {_toml_string(root.name[:200])}\n')
            text = old_toml.decode('utf-8')
            watch.atomic_bytes(config_path, (text + ('' if text.endswith('\n') else '\n') + block).encode('utf-8'))
            raw['rules'].append({'namespace': namespace, 'id': OBJECTIVE_ID, 'workspace': str(root),
                                 'record': 'request-summary.json', 'anchors': [], 'project_ids': [],
                                 'workspace_keys': [_key(root)]})
            for provider, folder in handoffs.FOLDERS.items():
                raw.setdefault('handoffs', []).append({'objective': objective, 'provider': provider,
                                                       'path': str(root / folder / 'handoff.md')})
            watch.atomic_json(settings_path, raw)
            watch.load_settings(settings_path, config.load(config_path))
        except Exception:
            watch.atomic_bytes(config_path, old_toml)
            watch.atomic_bytes(settings_path, old_settings)
            annotation.unlink(missing_ok=True)
            raise
        return objective, True, True


def _add_sources(config_path, settings_path, raw, settings, objective, root):
    """Bind the folder's handoff notes to its existing trail if they are missing."""
    bound = {(b['objective'], b['provider']) for b in settings.get('handoffs', [])}
    missing = [(p, f) for p, f in handoffs.FOLDERS.items() if (objective, p) not in bound]
    if not missing:
        return False
    old = settings_path.read_bytes()
    for provider, folder in missing:
        raw.setdefault('handoffs', []).append({'objective': objective, 'provider': provider,
                                               'path': str(root / folder / 'handoff.md')})
    watch.atomic_json(settings_path, raw)
    try:
        watch.load_settings(settings_path, config.load(config_path))
    except Exception:
        watch.atomic_bytes(settings_path, old)
        raise
    return True
