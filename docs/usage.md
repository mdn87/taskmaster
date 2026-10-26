# Configure a read-only objective trail

Install Taskmaster in a project-local Python 3.11+ environment. The configured
Autowork command is an argument array for an existing installation; Taskmaster
calls only its version and campaign-outcome readers. It never dispatches work.

Paths in this configuration are relative to its file. Objective identity is
declared independently of annotation readability:

```toml
schema_version = 1
max_age_seconds = 86400

[autowork]
command = ["autowork"]
version = "0.1.0"

[[stores]]
alias = "pilot-runs"
owner = "autowork"
path = "saved-runs"
mode = "saved_snapshot"
manifest = "corpus-manifest.json"

[[objectives]]
namespace = "pilot"
id = "first-trail"
annotation = "objective.json"
run_store = "pilot-runs"
label = "First objective trail"
```

`objective.json` contains `{"objective_id":"first-trail","run_ids":["run-1"]}`.
Only explicit membership is used. A missing run remains a typed gap. A malformed
annotation fails only its configured objective, retaining prior evidence where
the binding matches. To annotate a Dias chip, add an explicit `[[chips]]` table
with `project_id`, `chip_id`, `namespace`, and `objective`; the last two identify
the configured objective. This does not assert the chip exists.

`autowork.outcome_schema_versions` lists the `campaign outcome` schema versions
the operator admits (default `[1]`; this release can read 1 and 2). Autowork
moved to schema 2 without changing its reported version, so admitting a new
schema is a visible configuration change rather than a code decision. Unknown
fields inside an admitted schema are ignored. Output in a schema that is not
listed fails the objective with `required_schema_unsupported`.

The saved-corpus manifest is `taskmaster.corpus-manifest/v1`, containing
`captured_at` (UTC timestamp or null) and `files` (relative paths to SHA-256
digests). Produce it from an explicitly prepared corpus using
`taskmaster.sources.manifest_for`, preserving its original capture time. Never
substitute the copy time or filesystem modification time. See
[read footprint](read-footprint.md) for permitted records and unsafe stores.
Do not point a live adapter at an unbounded private run tree.

## Cirra production audits

An objective may name one `cirra` store so that the runs' recorded commits are
joined to the production audits Cirra ran on them (work-view concept, slice 1):

```toml
[[stores]]
alias = "audits"
owner = "cirra"
mode = "live"
url = "http://127.0.0.1:8797"
repository = "lugos"

[[objectives]]
namespace = "pilot"
id = "first-trail"
annotation = "objective.json"
run_store = "pilot-runs"
audit_store = "audits"
```

`url` must be `http://127.0.0.1` with an optional port: Cirra's audit route is
loopback only, and any other address (including `localhost`) is a configuration
error, not a gap. `repository` is the store id under which a run's `source_commit`
becomes a `git` revision reference; Taskmaster does not open the repository. A
`saved_snapshot` store names `path` (recorded route responses) and `manifest`
instead of `url`; it exists for fixtures. The store is optional: an unreachable
route leaves the entry `partial` with `optional_source_unavailable` on that source.

A revision join reaches `candidate` status, and stays there until an owner record
confirms it (decision D2). The brief, the morning report and Dias all label a
candidate as a revision join that no owner record confirms. To resolve it, the annotation may list
`audit_attempts` (Cirra attempt ids) or `finding_items` (derived item ids, the
SHA-256 of the canonical `[repository_key, finding_key]` pair), and a run's
assignment may cite `finding_items`. Cirra's gate verdict, conclusion and waiver
reason are copied as recorded; Taskmaster never recomputes a gate, and the counts
it derives are observations, never completion percentages.

## Agents and manual publication

```text
taskmaster refresh --config taskmaster.toml --export export.json
taskmaster show --export export.json --all --json
taskmaster show --export export.json --namespace pilot --id first-trail --json
taskmaster verify-export --export export.json
```

### Start-of-session brief

```text
taskmaster brief --export export.json --namespace pilot --id first-trail [--json] [--config taskmaster.toml]
/taskmaster brief [NAMESPACE/ID] [--json]
```

The brief summarizes one objective from the saved export: evidence age and
freshness, the newest runs, open Cirra findings, audit gates, link counts with
candidates named, and gap counts. It has no disposition or recommended next
action (decision D1). The slash form picks the trail whose watched workspace is
the current directory, reads the export named by the watch settings, and always
renders on this host, even when a remote context owner is configured. In text
form it lists the objective's handoff note paths that exist on this host; the
`taskmaster.brief/v1` JSON never carries a host path (decision D3). The brief
prints when its evidence was checked and the export's publication time. On the
lugos-host deployment, runs reach the export through a replica pulled every
five minutes and a watcher that runs every 30 seconds, and the workstation copy
is pulled every 30 seconds, so a run can be up to about six minutes old before
it appears.

Use `--now YYYY-MM-DDTHH:MM:SSZ` for deterministic comparisons. Freshness describes
evidence age and failure state, not completion or permission. Null evidence time
does not mean no successful observation: the read model distinguishes them.

## Morning report

Set `TASKMASTER_EXPORT` to the configured export and, when using Lugos's repository
helper, `TASKMASTER_PYTHON` to the Taskmaster environment's Python executable.
Keep inbox prose in `report-body.md`, with no Taskmaster markers. From Taskmaster's
environment:

```text
python -m taskmaster.morning_report --body-file report-body.md --output report.md
```

The Lugos integration calls the same module from its morning-report skill through
`plugins/lugos-toolset/scripts/taskmaster_report.py`. Configured empty-inbox
reports still include trails; unconfigured reports keep the previous behavior.
Missing or invalid exports become one bounded status line and do not stop inbox
processing. Deliver the verified file verbatim, rather than rewriting its block.
The helper uses one clock and one export read; a concurrent refresh cannot change
the bytes it verifies. CRLF translation or block modification fails verification.

When the watcher publishes `handoffs.json` beside the export, the report also
includes session handoff status for each configured trail in that same verified
block. The helper reads this optional context once using the report's evaluation
time; a later refresh cannot change the delivered bytes. An absent sidecar keeps
the existing report unchanged. An invalid, unreadable, expired or future-dated
sidecar reports handoff status as unavailable while retaining the execution
trail report and inbox body. The execution export and its evidence hashes are
unchanged.

A `saved` handoff means a valid session handoff note was observed within seven
days. It does not assert that anyone resumed or accepted the work. Reports expose
only the objective, provider and handoff metadata, never note prose, private paths
or original session IDs. See [session handoff status](handoff-status.md) for the
configuration and validation contract.

## Dias

Set `DIAS_TASKMASTER_EXPORT` on the existing Dias host to the same saved export.
Alternatively, explicitly provision Dias's `.local/taskmaster/` directory as a
directory link to this export's output directory; the host reads its `export.json`
at startup without requiring inherited environment variables. Restart the host
after configuring it. `DIAS_TASKMASTER_EXPORT=none` disables this default as well.
The optional adapter reads it locally and sends a safe model to the Trails view;
the browser never receives the private path. It runs no Python or owner command.
The first release keeps the existing Node host and React display in the browser
and Windows WebView2 shell. No new host stack is required.

All consumers use the same strict acceptance rules and clock-based fields.
Dias-specific chip existence notes do not alter shared counts or hashes.
Missing, invalid and unreadable files remain explicit; a cached view is visibly
stale. Configuring a host is separate from global installation or remote rollout.

For a persistent Lugos morning-report binding, its repository helper also reads
`lugos/.local/taskmaster.json` when `TASKMASTER_EXPORT` is unset:

```json
{"schema_version":1,"export":"../../taskmaster/.local/active/export.json","python":"../../taskmaster/.venv/Scripts/python.exe"}
```

Paths are relative to that local configuration file; use `.venv/bin/python` on
POSIX. The helper and morning-report workflow include the configured trail even
when the inbox is empty. Neither consumer refreshes the export. Run `refresh`
explicitly after changing source evidence or objective annotations.

Keep the machine's `taskmaster.toml` private (it is ignored). Register only
explicit `{objective_id, run_ids}` annotations with a known run store. An outcome
report mentioning runs, a Dias chip title, and a test fixture are not new objective
annotations. If discovery finds only the historical pilot, activation still covers
only that pilot; it does not establish membership for current work.

When creating annotations from verified run records, check the exact owner
workspace or subject anchor against the intended objective. Keep a private
provenance record with the source path, JSON pointer and raw file digest for each
membership. These are operator annotations, not owner-recorded objective bindings;
the latter remain typed gaps when absent. Similar titles, task hashes or nearby
timestamps alone do not establish membership.

Autowork cross-review runs can establish membership after their subject manifest
and result binding are verified. Their review success is not exported as dispatch
delivery or implementation acceptance. Dispatch outcome coverage remains
unsupported for objectives containing these review runs.

For a private owner store, explicitly import only the allowlisted records into a
saved corpus and retain their original bytes and source digests. Save import time
separately from evidence capture time. An unknown capture time remains null even
after a successful import. A saved corpus tracks only its annotated, captured
runs: discovering new runs, verifying membership and recapturing changed records
are separate operator actions. Refresh republishes that corpus; it does not watch
the original owner store.
