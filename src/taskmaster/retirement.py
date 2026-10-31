"""Recoverable capacity retirement, ordered by observed work activity, never creation."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tomllib

from . import handoffs, sources, timefmt, watch

IDLE_SECONDS = 7 * 86400
# Autowork dispatch outcomes and review lifecycle terminal states. Unknown stays protected.
TERMINAL = {'completed', 'failed_precondition', 'failed_transport', 'cancelled',
            'held_partial_write', 'held_policy_violation', 'held_verification_failed',
            'held_interrupted', 'succeeded', 'failed', 'failed_review_parse',
            'timed_out', 'stalled', 'canceled'}


def plan(cfg, settings, now):
    """Read-only candidate with activity evidence, or fail without changing registrations."""
    from .workspaces import WorkspaceError
    history = watch.read_session_registry(Path(settings['state_dir']) / 'session-registry.json')
    if (history is None or history['status'] != 'current'
            or not 0 <= timefmt.seconds_between(history['evaluated_at'], now) <= history['max_age_seconds']):
        raise WorkspaceError('capacity retirement needs current session observations; refresh Taskmaster first')
    # Remote notes are observations, not necessarily files on the owner host.
    snapshot = None
    if settings.get('context_file'):
        from . import remote_context
        snapshot = remote_context.read(settings, now)
    discovered, members, _, quarantine = watch.discover(settings)
    footprints = {}
    candidates = []
    watched = {r['namespace']+'/'+r['id'] for r in settings['rules']}
    discovery_dependent = {r['namespace']+'/'+r['id'] for r in settings['rules'] if not r.get('handoff_only')}
    for obj in cfg.objectives:
        key = obj.namespace+'/'+obj.id
        if len(settings['rules']) >= 64 and key not in watched:
            continue  # Removing an unwatched objective would not free a rule slot.
        evidence = []
        # A discovery failure cannot conceal a new member of an unwatched saved corpus.
        # Protect watched trails conservatively until incomplete discovery is resolved.
        unsafe = bool(quarantine and key in discovery_dependent)
        for row in history['sessions']:
            if row['active'] and row['objective'] == key:
                unsafe = True
            for a in row['assignments']:
                if a['objective'] == key:
                    evidence.append({'source': 'session assignment', 'at': max(a['last_seen_at'], a['ended_at'] or a['last_seen_at'])})
        for binding in settings.get('handoffs', []):
            if binding['objective'] != key:
                continue
            try:
                if snapshot is None:
                    note = handoffs.inspect_note(binding['path'], now)
                else:
                    row = snapshot['notes'].get(binding['path'], {'status': 'unavailable'})
                    if row['status'] == 'missing':
                        continue
                    if row['status'] != 'present':
                        unsafe = True
                        continue
                    note = handoffs.inspect_bytes(base64.b64decode(row['data'], validate=True), now, snapshot['timezone'])
                evidence.append({'source': 'handoff '+binding['provider'], 'at': note['note_updated_at']})
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                unsafe = True
        try:
            annotation = watch.read_json(obj.annotation)
            run_ids = set(annotation['run_ids']) | set(members.get(key, []))
            previous = watch.read_json(Path(settings['state_dir'])/'status.json', {})
            run_ids |= set(previous.get('memberships', {}).get(key, []))
            if run_ids:
                store = obj.run_store.path
                if store not in footprints:
                    footprints[store] = sources.footprint(store)
                fp = footprints[store]
                for run_id in run_ids:
                    if run_id in discovered and run_id in members.get(key, []):
                        records = discovered[run_id]
                        run_path = Path(settings['source'])/run_id
                        names = records.keys()
                    else:
                        dirs = fp.run_dirs(run_id)
                        if len(dirs) != 1:
                            unsafe = True
                            continue
                        run_path = store/dirs[0]
                        record_map = fp.records_in(dirs[0])
                        records = {name: fp.data[path] for name, path in record_map.items()}
                        names = records.keys()
                    state = json.loads(records.get('state.json', b'{}'))
                    result = json.loads(records.get('result.json', b'{}'))
                    if state.get('status', result.get('status')) not in TERMINAL:
                        unsafe = True
                        continue
                    # Record mtime is conservative: copying evidence may make old work
                    # look newer, but creation/run-ID/refresh timestamps never make it stale.
                    for name in names:
                        stamp = timefmt.fmt(datetime.fromtimestamp((run_path/name).stat().st_mtime, timezone.utc))
                        evidence.append({'source': f'run {run_id}/{name} mtime', 'at': stamp})
        except (OSError, ValueError, KeyError, TypeError, sources.SourceError):
            unsafe = True
        if unsafe or not evidence:
            continue
        latest = max(e['at'] for e in evidence)
        if timefmt.seconds_between(latest, now) >= IDLE_SECONDS:
            candidates.append({'objective': key, 'last_activity_at': latest, 'activity': evidence})
    if not candidates:
        raise WorkspaceError('no safely observed inactive trail has been stale for seven days; nothing retired')
    return min(candidates, key=lambda c: (c['last_activity_at'], c['objective']))


def remove_objective(text, objective):
    """Remove only the selected objective and its chip mappings, preserving other TOML."""
    pieces = re.split(r'(?=^\[\[?[^\n]+\]\]?\s*$)', text, flags=re.M)
    kept = []
    for piece in pieces:
        stripped = piece.lstrip()
        if stripped.startswith('[[objectives]]'):
            row = tomllib.loads(piece)['objectives'][0]
            if row['namespace']+'/'+row['id'] == objective:
                continue
        elif stripped.startswith('[[chips]]'):
            row = tomllib.loads(piece)['chips'][0]
            if row['namespace']+'/'+row['objective'] == objective:
                continue
        kept.append(piece)
    return ''.join(kept)


def prepare(cfg, settings, raw, old_toml, old_settings, now):
    """Archive rollback inputs and return edited config/settings; no live write here."""
    candidate = plan(cfg, settings, now)
    key = candidate['objective']
    obj = next(o for o in cfg.objectives if o.namespace+'/'+o.id == key)
    receipt = {**candidate, 'retired_at': now,
               'config_before': base64.b64encode(old_toml).decode(),
               'settings_before': base64.b64encode(old_settings).decode(),
               'annotation_path': str(obj.annotation),
               'annotation': base64.b64encode(obj.annotation.read_bytes()).decode(),
               'evidence_retained': True}
    import hashlib
    import uuid
    archive = Path(settings['state_dir'])/'retired-trails'/(now.replace(':','')+'-'+hashlib.sha256(key.encode()).hexdigest()[:16]+'-'+uuid.uuid4().hex[:8]+'.json')
    if archive.exists():
        raise ValueError('retirement archive already exists; refusing to overwrite')
    watch.atomic_json(archive, receipt)
    edited = json.loads(json.dumps(raw))
    edited['rules'] = [r for r in edited['rules'] if r['namespace']+'/'+r['id'] != key]
    edited['handoffs'] = [b for b in edited.get('handoffs', []) if b['objective'] != key]
    return remove_objective(old_toml.decode('utf8'), key), edited, archive
