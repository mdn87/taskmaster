---
name: taskmaster
description: Inspect Taskmaster's continuous work-trail tracking and view or correct the trail assigned to a session's Dias card. Use for /taskmaster commands or an incorrectly detected session project or work trail.
---

Use Taskmaster's deterministic CLI; do not edit annotations or registrations by
hand. Find the workspace's `taskmaster/taskmaster.toml` (or `taskmaster.toml` when
already in that repository) and its local `.venv` Python. On Windows:

```text
<taskmaster>/.venv/Scripts/python.exe -m taskmaster slash --config <taskmaster>/taskmaster.toml "/taskmaster status"
```

Pass the user's slash-command text as one argument, without shell evaluation.
The supported commands are:

- `/taskmaster status`: watcher heartbeat, evidence failures and quarantined runs.
- `/taskmaster trails`: registered objectives and their exact discovery scopes.
- `/taskmaster handoffs [--format report]`: saved, stale, missing or invalid session handoff notes by trail. A saved note does not prove another session resumed it.
- `/taskmaster sessions`: live Dias session assignments, detection candidates and provenance.
- `/taskmaster sessions --all`: persistent registrations, including inactive sessions and verified provider resume IDs.
- `/taskmaster sessions --trail NAMESPACE/ID [--format report]`: current and historical sessions registered to one trail.
- `/taskmaster use NAMESPACE/ID`: select the current session's trail explicitly.
- `/taskmaster clear`: keep the current session unassigned.
- `/taskmaster auto`: remove the override and restore automatic detection.
- `/taskmaster refresh`: perform one verified capture and consumer refresh now.

`use`, `clear` and `auto` also accept `--session DIAS_RUN_ID` to target the exact
card shown by `sessions`. Without it the CLI uses a provider-supplied session
identity, hashed using Dias's convention. If that identity is absent or is not
known in the registry, list sessions and ask which exact card the user means.
`use` and `clear` accept historical card IDs; `auto` requires live detection.
Never substitute the newest card, a matching title or another chat's identity.
Only return a resume ID recorded in the registry. The hashed Dias card ID is not
a resume ID. Never infer a missing ID or automatically launch a resumed session.

After a correction, show the resulting objective and provenance from the command
result. A `busy` response means the watcher owns the cycle lock: retry once after
the current refresh finishes. Report other errors as returned.

Session context is distinct from execution evidence. Selecting a card's trail
never admits an Autowork run or claims completion. Discovery uses only the
configured exact workspace/subject rules. Ambiguous or conflicting records stay
quarantined. Do not broaden those rules merely to make a card appear assigned.
