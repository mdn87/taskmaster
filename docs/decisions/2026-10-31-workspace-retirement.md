# Automatic workspace retirement and Tenamar registration

On 2026-10-31 the operator authorized Taskmaster to retire the longest-inactive
trail when a new workspace reaches capacity, preserving active long-running work.
PR [#21](https://github.com/mdn87/taskmaster/pull/21) implements that rule; commit
`1fe57d7080ee157f12a4c9999c8a7c7e3a94db57` fixes the new rollback test's native-path fixture.

## Live result

The host had 64 configured objectives but only 63 watch rules. Registration now
archives `pilot/historical-review-retry-chain`, whose latest observed evidence
modification was `2026-10-17T07:31:08Z`, and adds `tenamar/workspace`. The retirement
occurred at `2026-10-31T21:49:08Z`. No pilot corpus, original annotation or history
was deleted. Recovery inputs, selection evidence and the collector mapping are
retained under `/srv/lugos/state/taskmaster/watch/retired-trails/`.

The first two registration attempts after activation returned `busy` while the
watcher held its cycle lock. Stopping only `taskmaster-watch.service`, registering
through the owner, and restarting in a `finally` block completed the operation.
The context collector stayed running. Both services are active. The owner accepted
the saved Codex note and the published report shows `tenamar/workspace — Handoff
saved`; the missing Claude note is reported separately.

Installed personal skill: `~/.codex/skills/init-trail/SKILL.md`. It records CLI
registration, remote-owner routing, capacity policy, bounded lock recovery and
handoff verification. Its metadata validator passed.

## Verification and rollback

Full suite: 220 passed. Final focused recovery checks: 39 passed. The fixture
portability fix passed 16 local tests, then the staged Linux release passed all
29 retirement, workspace and remote-owner tests. Independent data-integrity review
found collector rollback and retired-namespace reuse gaps; both were fixed and
covered before activation. No test failure remains in this change.

Selected release: `/srv/lugos/releases/taskmaster/1fe57d7080ee157f12a4c9999c8a7c7e3a94db57`.
Its `.venv` points to the unchanged previous release's environment. Source imports
use the existing `current/src` service environment. Archive SHA-256:
`89fcae753e47c1a4020e1226834cbd9705d5edee09ed75962aaf35fd0025ba4c`.

The previous release `8538d5932a29acdedd2e912da5d8943aba5efbc3` remains intact.
Code rollback is restoring `current` to that release and restarting the watcher
and collector. Registration rollback is separate: inspect the retirement archive
and current capacity under the workspace/cycle/registration locks before restoring
configuration; do not blindly overwrite newer registrations. The pre-existing
unbound Fichese review remains an unrelated failed objective.
