# Continuous tracking and session registration

Taskmaster can watch an explicitly configured local Autowork store. It scans
direct run directories every 30 seconds by default, reads only allowlisted owner
records, and never enters private child-output or credential-isolation folders.
It neither dispatches work nor reads agent transcripts.

## Verified discovery

Set `watch = ".local/watch/settings.json"` at the top level of `taskmaster.toml`.
The separate settings file contains:

```json
{
  "schema_version": 1,
  "source": "ABSOLUTE_OWNER_RUN_STORE",
  "state_dir": ".",
  "export": "../active/export.json",
  "interval_seconds": 30,
  "dias_url": "http://127.0.0.1:4175",
  "rules": [{
    "namespace": "lugos", "id": "lip-016",
    "workspace": "EXACT_OWNER_WORKSPACE",
    "record": "request-summary.json", "anchors": [],
    "project_ids": ["lugos"]
  }]
}
```

Paths are relative to the settings file. Each rule names an existing configured
objective. Its original annotation supplies the verified seed membership. A
dispatch rule requires an exact workspace match, a recorded run identity and
consistent task hashes. A review rule uses `subject-manifest.json`, exact
`anchors`, and a verified result-to-subject digest. On Windows workspace comparison
normalizes slash direction and case; it never uses prefix or title matching.
Two matching objective rules, conflicting identities, incomplete review bindings
and changing source records are quarantined. A lost established member fails the
observation and retains the last valid trail rather than silently removing it.

Captures are immutable and contain raw owner bytes and membership provenance.
Unchanged captures are reused; changed generations remain under `state_dir/captures`
for recovery. No automatic deletion occurs. Existing source and export size bounds
remain enforced; exceeding them reports failure instead of truncating membership.
The watcher checks only the configured direct run store; nested stores need their
own explicit capture scope and are not implicitly traversed.

Unchanged nonmatching discovery headers may be skipped using file metadata.
Admitted runs are always reread and verified twice. The daemon can reuse an owner
projection only for identical verified footprint bytes, objective identity, owner
command and pinned version; it rechecks the version once per cycle. The in-memory
projection cache is bounded to 32 entries and 16 MiB. A changed record forces a
new owner projection. The first cold refresh can take longer than the poll interval;
cycles never overlap, and status includes the current phase and measured duration.

Each successful direct source observation has a known observation time. Watched
objectives therefore use `live` evidence mode, while untouched saved pilots keep
their unknown original capture time. Freshness measures the latest verified read,
not when the underlying work was performed. Keep `max_age_seconds` short enough
to expose a stopped watcher (the installed configuration uses 120 seconds).

```text
taskmaster watch --config taskmaster.toml
taskmaster watch --config taskmaster.toml --once
```

With `watch` configured, ordinary `refresh` uses this same pipeline and requires
the configured shared export. It cannot accidentally republish the old seed
corpus. A single daemon lock prevents duplicate loops, a cycle lock serializes
captures, a separate lock protects registration edits, and the export lock/atomic replace
protect all readers. Failures retry on the next cycle. A stopped watcher is
visible through `/taskmaster status` and reader freshness.

The shared export refreshes agents and the persistent Lugos report integration.
`trails-report.md` beside it is also regenerated automatically from the exact
published model. It contains only trail data, not a claim about the inbox or a
sent morning report. Live Dias polls the export every 15 seconds while open.

## Inspecting and correcting a session's trail

`/taskmaster sessions` shows the exact Dias card IDs and their registrations.
Automatic session assignment uses the hook-reported working directory and touched
file directories, hashed locally, against configured `workspace_keys` (SHA-256 of
the normalized absolute repository/worktree path). Paths use forward slashes, no
trailing slash and lowercase on Windows. The hashes do not enter trail evidence.
If no exact workspace matches, detection falls back to Dias's reported project IDs
and the configured `project_ids`. A unique match is labelled `detected`. Multiple matches
stay `ambiguous`; none stays `unassigned`. This display context does not authorize
or prove execution membership.

```text
/taskmaster use lugos/lip-016
/taskmaster clear
/taskmaster auto
```

Use `--session DIAS_RUN_ID` when targeting another specific card. Otherwise the CLI
uses `DIAS_RUN_ID`, or hashes the provider's `CODEX_THREAD_ID` / `CLAUDE_SESSION_ID`.
It refuses a session absent from the live Dias feed. `use` persists an operator
override; `clear` persists an explicit unassigned choice; `auto` removes it.
Overrides survive watcher and host restarts and outrank later detection.
Local registration history records changes. The sidecar `sessions.json` contains
only safe IDs, candidates, provenance and freshness; private matching paths stay
in watcher configuration. Dias shows it only on the loopback display.

Invoke the deterministic parser directly when a client has not loaded the skill:

```text
taskmaster slash --config taskmaster.toml "/taskmaster sessions"
```

The reusable skill is `skills/taskmaster/SKILL.md`. The installed workspace
copies provide Codex skill discovery and Claude's `/taskmaster` command. A client
may need to reload its skill/command list after installation.
