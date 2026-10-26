"""Agent PostToolUse hook: record a just-written session handoff note in Taskmaster.

Reads the hook payload on stdin and finds a written `.claude/handoff.md` or
`.Codex/handoff.md`: a Claude Code editor tool's file, or a Codex patch header.
It validates the note with the watcher's own rules. When no trail watches that
file it starts a workspace trail for the note's folder (see `workspaces`), then
publishes the handoff projection under the watcher's cycle lock so the note is
recorded at once. It never edits the note, and always exits 0 so a hook failure
cannot block the agent.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

from . import config, handoffs, publication, sources, timefmt, watch, workspaces

PATCH_HEADER = re.compile(r"\*\*\* (?:Add|Update) File: ([^\n\"']+?)(?=\\n|\n|\r|\"|'|$)")
EDIT_TOOLS = ('Write', 'Edit', 'MultiEdit')
COMMAND_FIELDS = ('command', 'input', 'patch', 'script', 'cmd')


def written_paths(payload):
    """Absolute paths a tool call wrote, from an editor's file or patch headers."""
    cwd = payload.get('cwd') if isinstance(payload.get('cwd'), str) else os.getcwd()
    tool_input = payload.get('tool_input')
    if payload.get('tool_name') in EDIT_TOOLS:
        path = tool_input.get('file_path') if isinstance(tool_input, dict) else None
        return [Path(path)] if isinstance(path, str) and path else []
    texts = [tool_input] if isinstance(tool_input, str) else []
    if isinstance(tool_input, dict):
        for field in COMMAND_FIELDS:
            value = tool_input.get(field)
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list):
                texts.append(' '.join(v for v in value if isinstance(v, str)))
    found = []
    for text in texts:
        for match in PATCH_HEADER.finditer(text):
            raw = match.group(1).replace('\\\\', '\\').strip()
            path = Path(raw) if Path(raw).is_absolute() or re.match(r'^[A-Za-z]:[\\/]', raw) else Path(cwd) / raw
            if path not in found:
                found.append(path)
    return found


def handoff_notes(paths):
    folders = {folder.lower() for folder in handoffs.FOLDERS.values()}
    return [p for p in paths if p.name == 'handoff.md' and p.parent.name.lower() in folders]


def record(note, cfg):
    """One line saying whether the note is valid and whether Taskmaster recorded it."""
    now = timefmt.now()
    try:
        handoffs.inspect_note(note, now)
    except (OSError, ValueError, OverflowError) as exc:
        return False, (f'{note} is not a valid handoff note ({exc}), so Taskmaster records it as invalid. '
                       'Keep the headers last_updated: and session_summary:, and the headings "## What Was Done", '
                       '"## Current State", "## Next Up" and "## Gotchas" exactly, each with content and nothing after '
                       'the heading name. Fix the note and save it again.')
    if cfg.watch is None:
        return True, f'{note} is a valid handoff note; Taskmaster has no watcher configured, so nothing was recorded.'
    from .remote_owner import client_settings
    client = client_settings(cfg.watch)
    if client is not None:
        from .remote_owner import forward
        import base64
        raw = handoffs.read_note_bytes(note)
        provider = next(p for p, folder in handoffs.FOLDERS.items() if folder.lower() == note.parent.name.lower())
        result = forward(client, {'action':'handoff', 'workspace':str(note.absolute().parent.parent),
                                    'provider':provider, 'data':base64.b64encode(raw).decode('ascii')})
        return True, f"Host owner accepted handoff for {result['objective']}: {result.get('handoff_status', 'queued')}."
    settings = watch.load_settings(cfg.watch, cfg)
    key = watch.normalized(str(note.absolute()))
    trails = [b['objective'] for b in settings.get('handoffs', []) if watch.normalized(b['path']) == key]
    started = ''
    if not trails:
        try:
            objective, changed, new = workspaces.ensure(cfg.path, note.absolute().parent.parent)
        except (OSError, ValueError, sources.SourceError, publication.PublicationError) as exc:
            return True, (f'{note} is a valid handoff note, but no Taskmaster trail watches this file and starting '
                          f'one failed ({exc}), so it was not recorded. Mention this in the wrap-up.')
        if changed:
            started = (f'Started Taskmaster trail {objective} for this workspace. '
                       if new else f'Bound this workspace\'s notes to Taskmaster trail {objective}. ')
        cfg = config.load(cfg.path)
        settings = watch.load_settings(cfg.watch, cfg)
        trails = [b['objective'] for b in settings.get('handoffs', []) if watch.normalized(b['path']) == key]
        if not trails:
            return True, (f'{note} is a valid handoff note, but trail {objective} does not watch this file, so it '
                          'was not recorded. Mention this in the wrap-up.')
    state = Path(settings['state_dir'])
    state.mkdir(parents=True, exist_ok=True)
    cycle = watch.read_json(state / 'status.json', {}).get('cycle_seconds', 0) or 0
    try:
        with publication.writer_lock(state / 'cycle'):
            result = handoffs.publish(settings, now, cycle)
    except publication.PublicationError:
        return True, (f'{started}{note} is a valid handoff note. The Taskmaster watcher is mid-cycle and will record it '
                      f'within about a minute (trail {", ".join(trails)}).')
    by_trail = {t['objective']: t['status'] for t in result['trails']}
    statuses = ', '.join(f'{t}: Handoff {by_trail.get(t, "unavailable")}' for t in trails)
    return True, f'{started}Recorded {note} in Taskmaster ({statuses}).'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='taskmaster.handoff_hook')
    parser.add_argument('--config', default=str(Path(__file__).resolve().parents[2] / 'taskmaster.toml'))
    args = parser.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.buffer.read(8 * 1024 * 1024) or b'{}')
        notes = handoff_notes(written_paths(payload)) if isinstance(payload, dict) else []
        if not notes:
            return 0
        cfg = config.load(args.config)
        results = [record(note, cfg) for note in notes]
    except Exception as exc:  # A hook must never break the agent's turn.
        print(json.dumps({'systemMessage': f'Taskmaster handoff hook failed: {type(exc).__name__}: {exc}'}))
        return 0
    message = ' '.join(text for _, text in results)
    if all(valid for valid, _ in results):
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': message},
                          'systemMessage': message}))
    else:
        print(json.dumps({'decision': 'block', 'reason': message, 'systemMessage': message}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
