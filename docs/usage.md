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

The saved-corpus manifest is `taskmaster.corpus-manifest/v1`, containing
`captured_at` (UTC timestamp or null) and `files` (relative paths to SHA-256
digests). Produce it from an explicitly prepared corpus using
`taskmaster.sources.manifest_for`, preserving its original capture time. Never
substitute the copy time or filesystem modification time. See
[read footprint](read-footprint.md) for permitted records and unsafe stores.
Do not point a live adapter at an unbounded private run tree.

## Agents and manual publication

```text
taskmaster refresh --config taskmaster.toml --export export.json
taskmaster show --export export.json --all --json
taskmaster show --export export.json --namespace pilot --id first-trail --json
taskmaster verify-export --export export.json
```

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

## Dias

Set `DIAS_TASKMASTER_EXPORT` on the existing Dias host to the same saved export.
The optional adapter reads it locally and sends a safe model to the Trails view;
the browser never receives the private path. It runs no Python or owner command.
The first release keeps the existing Node host and React display in the browser
and Windows WebView2 shell. No new host stack is required.

All consumers use the same strict acceptance rules and clock-based fields.
Dias-specific chip existence notes do not alter shared counts or hashes.
Missing, invalid and unreadable files remain explicit; a cached view is visibly
stale. Configuring a host is separate from global installation or remote rollout.
