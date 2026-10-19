# Session handoff status

Taskmaster observes explicitly configured `.Codex/handoff.md` and
`.claude/handoff.md` files at verified objective workspaces. Saving a valid note
records a hash-linked event in the local handoff ledger. The note remains owned
by the existing session handoff process; Taskmaster never rewrites it.

`saved` means a structurally valid handoff note was observed and is no more than
seven days old. It does not mean that another session accepted or resumed the
work, that an objective completed, or that TORC authority transferred.

## Publication contract

The watcher publishes `handoffs.json` next to `export.json` and a separate
`handoff-report.md`. CLI, morning-report and Dias consumers use this context
projection; execution evidence and its hashes are unchanged.

```json
{
  "schema": "taskmaster.handoffs/v1",
  "evaluated_at": "2026-10-17T12:00:00Z",
  "max_age_seconds": 70,
  "status": "current",
  "trails": [{
    "objective": "example/work",
    "status": "saved",
    "sources": [{
      "provider": "codex",
      "status": "saved",
      "note_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "note_updated_at": "2026-10-17T11:00:00Z",
      "event_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    }]
  }]
}
```

Top-level status is `current`, `unavailable`, or `unconfigured`. An expired or
future-dated projection must be rendered as unavailable, never as a current saved
handoff. Trail status is `saved`, `stale`, `missing`, `invalid`, `unavailable`, or
`unconfigured`. Source status has the same vocabulary except `unconfigured`.
Providers are `codex` or `claude`, unique within each trail. Saved/stale sources
have all three metadata fields populated; other source states use null fields.
For multiple sources, trail status prefers saved, then stale, invalid,
unavailable, and missing; each source's own state remains visible. No sources
means unconfigured. At most 64 unique trails and two sources per trail, 256 KiB
per projection. Timestamps are UTC RFC 3339 with seconds and literal Z.

No note body, summary, private filename, original session ID or filesystem path
appears in the projection. Dias exposes it on its loopback trail route only.

## Configuration and note validation

Watch settings may include `handoffs`, an array of objects with exactly
`objective`, `provider`, and `path`. Paths are relative to the settings file or
absolute. The path must be the provider's standard handoff file immediately under
the exact configured workspace (or its already configured workspace hash).
Unrelated workspace notes never establish membership. No recursive scanning or
history-folder traversal takes place.

The supported session note has nonempty `last_updated` and `session_summary`
headers and nonempty `What Was Done`, `Current State`, `Next Up`, and `Gotchas`
sections. The existing `YYYY-MM-DD HH:MM` timestamp uses this machine's local
timezone; explicit timezone-aware ISO timestamps are also supported and preferred
for portable notes. Future dates, duplicate required headers/sections, invalid
UTF-8, oversized files and changed-during-read files cannot produce a saved flag.
Each note is limited to 64 KiB. Missing or invalid notes preserve ledger history
but clear their current saved flag. The ledger never implies pickup acceptance.

The local ledger retains up to 4,096 events and 4 MiB. It records each observed
note revision, original note timestamp, observation time, objective, provider,
source-binding hash and note digest. Repeated observations do not duplicate
events. Ledger corruption or capacity exhaustion retains the old ledger and
publishes an unavailable handoff status.

## Recording a note when it is written

`python -m taskmaster.handoff_hook` is an agent `PostToolUse` hook. It reads the
hook payload on stdin and finds a written `.claude/handoff.md` or
`.Codex/handoff.md`, either a Claude Code editor tool's file or a Codex patch
header resolved against the payload's `cwd`. It validates the note with the
rules above. An invalid note is returned to the agent as a blocking reason so
the agent fixes it. A valid note at a configured source is published at once
under the watcher's cycle lock, adding its ledger event. When the watcher holds
that lock, the hook reports that the watcher will record the note on its
current cycle and writes nothing. The hook never edits notes and always exits 0.
`--config` defaults to the repository's `taskmaster.toml`.

## Starting a workspace trail

When a valid note is saved where no trail watches, the hook starts a workspace
trail for the note's folder (the parent of `.claude` or `.Codex`) and then
records the note. `/taskmaster workspace add PATH` does the same by hand.

- If one trail's rule is already keyed to that exact folder, the folder's
  missing `.Codex` and `.claude` notes are bound to it and nothing else changes.
  Several matching trails are an error, never a guess.
- Otherwise it adds objective `<folder-name>/workspace` (suffixed `-2`, `-3`, …
  on a name clash) using the store existing workspace trails use, an empty
  annotation under the watch settings' `annotations/` folder, a watch rule keyed
  to the exact folder with `"handoff_only": true`, and both handoff sources. A
  handoff-only rule records notes and binds sessions but never claims runs in
  discovery: a project root can hold more runs than one trail's 64 KiB content
  bound allows, and those runs belong to narrower trails. An annotation with no
  runs is a valid, empty trail.
- Files are written annotation first, then `taskmaster.toml`, then the watch
  settings, so every intermediate state loads. Existing entries are only
  extended. If the result does not validate, both files are restored.
- The 64-trail limit still applies; at the limit the note is reported, not
  recorded, and nothing is written.
