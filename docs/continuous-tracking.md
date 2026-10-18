# Continuous tracking and session registration

Taskmaster can watch an explicitly configured local Autowork store. It scans
direct run directories every 30 seconds by default, reads only allowlisted owner
records, and never enters private child-output or credential-isolation folders.
It neither dispatches work nor reads agent transcripts.

The watcher can also observe the existing Codex and Claude session handoff notes
at explicitly configured objective workspaces. A valid save records a ledger
event and updates a Handoff saved flag; stale, missing and invalid notes remain
visible. See [session handoff status](handoff-status.md) for configuration,
validation, retention and the distinction between saving a note and accepting a
handoff. Inspect these flags with `/taskmaster handoffs --format report`.

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
projection cache is bounded to one entry per configured objective (64) and 16 MiB. A changed record forces a
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
It refuses unknown sessions. `use` and `clear` also accept a historical registered
card; `auto` requires a live session to obtain current detection. `use` persists an operator
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

## Resumable session registry

Each trail retains the sessions registered to it, including sessions no longer
present in Dias. The lifecycle adapter supplies the provider and original
session ID through the loopback-only context route. Both Dias and Taskmaster
verify that hashing the original ID produces the exact Dias card ID. A card hash
is never presented as a resume ID. Older cards without that metadata explicitly
show an unavailable ID until a new lifecycle event supplies it.

Dias's selected trail lists current and historical registrations and offers a
Copy session ID button for verified IDs. Copying does not start a session. Use
the copied ID with the appropriate provider's resume workflow. A resumed session
with the same identity updates its existing registry entry.

```text
/taskmaster sessions --all
/taskmaster sessions --trail cirra/coordination-layer
/taskmaster sessions --trail cirra/coordination-layer --format report
```

The watch state retains `session-registry.json`, and publishes `registry.json`
and `session-registry-report.md` beside the shared export. The registry is a
separate context projection: neither it nor its resume IDs enter the hashed
execution evidence export or the existing morning-report evidence block. The
companion report exposes the same local session history as Dias and the CLI.
Raw resume IDs are available only on the loopback Dias listener, never the
Surface listener. Continuous tracking does not inspect transcripts. Historical
metadata imports require the separate, explicit command below.

Registration history records provider, resume ID, first/last observation and
each objective/provenance assignment period. Moving or clearing a session closes
the prior period without erasing it. Unavailable feeds retain history and report
their unavailable status; a session absent from a successful feed is inactive.
An inactive session can still be resumed and can keep its current registration.

Retention is explicit: there is no automatic eviction. The registry permits
4,096 sessions, 128 assignment periods per session and 4 MiB of serialized data.
Reaching a limit or detecting corrupt history fails the update and preserves the
previous registry. Watcher status/last-error and registry freshness expose the
failure. No database migration or schema change to the execution export is needed.

## Backfilling historical session IDs

Use `backfill-sessions` to preview verified original IDs from explicitly named
local provider stores. Repeat `--claude-project` for each relevant directory;
both provider options are optional, but at least one source is required.

```text
taskmaster backfill-sessions --config taskmaster.toml --claude-project PATH_TO_CLAUDE_PROJECT --codex-db PATH_TO_CODEX_STATE_DB
```

Add `--apply` to import the verified candidates and refresh Dias, the CLI registry,
and the companion session report. The command opens the Codex database read-only
and corroborates each selected thread ID and working directory against its local
rollout header. For Claude, it reads top-level `sessionId`, `cwd`, and `timestamp`
metadata from direct JSONL files and requires the session ID to match the filename.
These files contain transcripts; the importer parses bounded records but neither
uses message text for matching nor retains it in reports or receipts.

Assignment requires an exact configured workspace path or its configured hash.
Titles, file modification times, project-name similarities, and transcript text
never establish membership. Multiple matching objectives or providers are rejected.
The resulting assignment is user-requested session context, labelled `operator`;
it does not add Autowork runs or make claims about execution evidence.

An import preserves existing assignments, moves, clears, and live activity status.
New historical entries start inactive. Repeating the import does not duplicate
registrations. Each applied import saves a prior-registry backup when one exists,
metadata-only provenance with source/record hashes, and a result under the watch
state's `backfills/` directory. The result records the durable import and consumer
refresh separately. If refresh fails after import, the watcher or an ordinary
refresh republishes the retained history; the error identifies the receipt.

Missing or ambiguous IDs remain unavailable. Provider timestamps describe observed
session metadata, not evidence freshness or proof that a particular run executed
inside that chat. Having a local session record does not test the provider's resume
command; copying an ID never starts work automatically.
