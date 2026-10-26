# Taskmaster and Dias deployment cleanup

The remaining deployment work from D1-D6 is complete. Dias serves the candidate
explanation and supplies session context again; Taskmaster's collector now follows
the same selected release as its watcher.

## Changes

- Installed `ops/systemd/taskmaster-context-collect.service.d/current-release.conf`
  on lugos-host. It selects `current` for the working directory, Python executable
  and source import path. The original sandbox and collector configuration remain
  in effect. The old and selected collector code and its direct dependencies were
  byte-identical before the switch.
- Published Dias assets from deployed base `b36c388` plus the already merged
  candidate-label change `3981c907`. The selected directory is
  `.local/mobile/releases/taskmaster-fix-20261026-assets-v2` inside Dias.
  Forced TypeScript compilation was necessary because copying the source patch
  preserved older timestamps and the incremental build initially reused old output.
  Served asset bytes, including the explanation, were checked against the final build.
- Published the native host in `.local/mobile/releases/taskmaster-fix-20261026-host`
  from the same deployed base plus the session-exit fix merged in Dias PR #74
  (`1ecf4c33`). A mobile change had inverted the version check: the reader rejected
  schema 1 even though the writer produces schema 1. After restart this caused
  `/api/v1/trails/context` to return `503 session-exits-unavailable`.
  The correction restores the existing contract; it requires no migration.
- Used the existing primary-shell launcher for activation. The companion process
  remained running. Preserved the shared Dias checkout's unrelated edits.

## Verification

- Taskmaster: 202 tests, five skipped.
- Dias: 1,070 JavaScript tests passed, two skipped; typecheck, build and all 43
  plan checks passed. Candidate-only deployment source: all 12 Trails tests passed.
- The new restart regression failed before the native correction and passed
  afterward. All 205 native host checks and 81 HTTP/WebSocket checks passed.
- Live control/display health returned 200. The context route recovered all 365
  existing ended-session records. Collector transport and Dias context were current,
  with the original note scope preserved. Refresh returned zero failed objectives,
  current session/handoff projections, and the unchanged 57-objective snapshot
  `001b9c5ea665330f5036cf4638ff498b9d996810dbb2dd161d4f6424be1d9273`.
- Independent review found no issues. Lugos audit quick passed for the native fix.
  Private logs, receipts, source/build artifacts and screenshots are retained in
  Taskmaster's `.local/cleanup-20261026/`.

## Cleanup and rollback

The abandoned `dias-wt-candidate` directory was not a registered worktree and
contained source/test files as well as dependencies. After checking that no process
used it, it was moved intact to
`.local/cleanup-20261026/archived-dias-wt-candidate/` inside Taskmaster.
Six identified Taskmaster/Dias TEMP probes were moved to `previous-probes/` there.
Unattributed temporary files were left alone.

The local `taskmaster.toml.before-schema-pin` and
`watch/settings.json.before-brief-export` backups now live in `config-backups/`
under that archive. The server's `taskmaster.toml.before-20261026-schema-pin`
remains at its documented rollback location. Older deployed releases are retained.

For Dias rollback, restore `launcher-before.ps1` to
`.local/mobile/start-mobile-finishing.ps1` and `shell-before.json` to the installed
shell's `shell.settings.json`, then run the launcher with `-Restart`.
Those backups are in `.local/cleanup-20261026/` inside Taskmaster. This restores
the previous host and asset paths; it also restores the session-context bug.

For collector rollback, move its `current-release.conf` drop-in out of the systemd
drop-in directory, run `systemctl daemon-reload`, and restart
`taskmaster-context-collect`. Its original unit still names `context-20261020`.
The pre-change unit text is retained on lugos-host at
`/srv/lugos/state/taskmaster/cleanup-20261026/collector-unit-before.txt`.

## Boundaries

The owner-schema digest remains unchanged under the accepted D6 decision.
Owner-side audit citations, `revision_matched`, and faster polling remain optional
backlog ideas. A `busy` refresh response still means another writer holds the
publication lock; it is not a failed objective. No additional production feature,
credential change or storage-schema change was introduced.
