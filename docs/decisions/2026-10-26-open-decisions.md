# Open Taskmaster decisions, 2026-10-26

Status: awaiting a recommendation. The operator asked for a reviewer to
recommend what to do on each item below, because they don't have the context
to choose. Each item states the facts, the options and what each option costs.
A recommendation should pick one option per item, give the reason in a few
sentences, and say what would change the answer.

## Context

Taskmaster is a read-only evidence joiner for the operator's agent work. It
reads owner records (Autowork runs, Cirra audits, handoff notes, Dias chips)
and publishes `export.json`, which three consumers read: the agent CLI
(`taskmaster show`), the morning report and the Dias "Trails" view. It owns
no record and grants no authority: Cirra owns findings and gates, Autowork owns
execution, LIR owns thread resumption.

Status on main as of 2026-10-26 (commit `befed7a2`):

- Plan Batches 1-3 are complete: contracts, the three consumers, edge-case
  fixtures and UI evidence. 196 tests pass. Dias and the CLI agree field for
  field on the live 57-objective export.
- Work-view Slice 1 (a Cirra audit adapter over a loopback route) is built.
  It adds `has_production_audit`, `observes_finding` and `has_audit_gate`
  links. Membership is `candidate` when joined only by revision, and
  `resolved` when an operator annotation or a run's assignment cites it.
- Taskmaster runs on the lugos-host server beside Cirra. A watcher refreshes
  every 30 seconds from a replica of the workstation's Autowork run store
  that is pulled every 5 minutes. Dias reads a copy pulled every 30 seconds.
- The operator works mostly alone, across about 50 workspaces, with several
  agent tools (Claude Code, Codex, Gemini) at once.

## D1. Build work-view Slice 2 now, later, or not at all?

Slice 2 adds two derived files and a command:

- `taskmaster.work/v1`: per objective, a derived disposition (for example
  `ready`, `blocked`, `repair`, `evidence_missing`), the list of work that
  owner records say is unfinished, the evidence gaps, and one recommended next
  action with the authority it would need. Taskmaster still executes nothing.
- `taskmaster.brief/v1` plus `taskmaster brief` and `/taskmaster brief`: a
  short start-of-session summary for an agent: status, open findings, recent
  runs, gaps, next action.
- A next-action line per trail in the morning report, the same data in Dias,
  and `verify-export` extended to both files.

Cost: a new batch about the size of Batch 2 (schemas, builder, three
consumers, agreement fixtures). Risk: a derived "next action" can be read as
an instruction; the concept keeps it advisory and evidence-cited.

Options: (a) build now; (b) build only the brief first, then the work view;
(c) defer until a concrete consumer asks; (d) drop.

## D2. May a revision join alone produce `resolved` membership?

Today an audit attempt on the same repository and head commit as a run is
linked as `candidate`. It becomes `resolved` only when an annotation or a
citing run confirms it. Options: (a) keep `candidate` until confirmed;
(b) let a same-repository, same-head join be `resolved` on its own.
Trade-off: (a) never claims a link the owners didn't record, but most real
audits stay `candidate`; (b) is more useful but can attach an audit of the
same commit made for a different objective.

## D3. May the local agent brief include the handoff note's file path?

The brief would run locally for the agent in that workspace. The export
itself never carries host paths; handoff note contents stay private. Options:
(a) include the absolute path in the local brief only; (b) never include it;
the agent finds `.claude/handoff.md` by convention.

## D4. Should Cirra trigger a Taskmaster refresh?

The watcher already discovers changes every 30 seconds. Options: (a) keep the
30-second discovery; (b) add a webhook or beacon from Cirra so audits appear
within a second or two. (b) adds a write path between two services that are
read-only to each other today.

## D5. Autowork changed its output schema without changing its version

Autowork's `campaign outcome` moved from `schema_version` 1 to 2 (additive
usage fields only) while still reporting version `0.1.0`. Taskmaster pins
the version in configuration and checked the schema in code, so the change
surfaced only as a failing test. Taskmaster now accepts schemas 1 and 2.
Options: (a) ask Autowork to bump its version on every output-schema change,
and keep Taskmaster's version pin; (b) also pin the accepted output schema in
Taskmaster's configuration so an operator, not code, admits a new schema;
(c) leave it as is.

## D6. Move the server's Autowork install to schema 2 now?

The server's Taskmaster venv still has an older Autowork install that emits
schema 1. The server's Autowork checkout is newer. Reinstalling changes every
trail digest once (the recorded schema version only), which consumers show as
new content. Options: (a) reinstall now, while the change is understood;
(b) wait until something needs a schema 2 field.

## Recommendations

From one `claude-fable` review (Autowork run `20261026-043345-cross-review`,
effort high, 13 findings). These are recommendations, not decisions; nothing
below is built yet.

| Item | Recommendation | What would change it |
|---|---|---|
| D1 | (b), but brief v1 carries only facts the export and Slice 1 already hold: status, open findings, recent runs, membership gaps. Disposition and next action wait for the work view. When the work view comes, consider shipping it first without the next-action field. | Choose (a) if a brief without a next action isn't worth opening; (c) if no agent session reads the brief within a couple of weeks. |
| D2 | (a), keep `candidate` until an owner record confirms it, and label candidate links clearly in all three consumers. Better fixes are on the owner side: Autowork citing audit ids in assignments, or Cirra recording the objective. | Cirra or Autowork recording the link, so `resolved` needs no inference. |
| D3 | (a), but the path is added at render time by the local CLI only, never written into `taskmaster.brief/v1` or the export. Codex and Gemini don't share the `.claude/handoff.md` convention. | The brief file being copied to the server or Dias, or every tool using one handoff location. |
| D4 | (a), keep 30-second discovery. The second-or-two benefit was overstated: the runs side is a replica pulled every 5 minutes, and Dias reads its own copy every 30 seconds. A read-only middle option is polling Cirra more often from the Slice 1 adapter. | An in-session agent workflow that needs audit gates faster than 30 seconds. |
| D5 | (b), list the accepted outcome `schema_version` values in configuration, and ask Autowork to bump its version on schema changes. Record the policy: unknown fields inside an accepted schema are ignored, and a new schema version needs a configuration change. | Autowork enforcing version bumps with its own test, which would make (a) enough. |
| D6 | (a), reinstall now, right after a morning report. | Fable also suggested leaving the recorded schema version out of the trail digest. That conflicts with plan section 3, which puts the pinned owner schema in source records on purpose, so it needs its own decision. |

Corrections to this brief that the review raised: D1's option (b) was not
separable as written, because "gaps, next action" are work-view outputs. The
brief's data source and worst-case age (up to about 5 minutes) should be
stated, and the brief should print the export's generation time. D4's latency
claim was overstated.

## Decisions taken

On 2026-10-26 the operator adopted the review's recommendations in full ("do
everything fable said"). What was done:

- D1: `taskmaster brief` and `/taskmaster brief` render `taskmaster.brief/v1`
  from the saved export: evidence age and freshness, newest runs, open findings,
  audit gates, link and gap counts. No disposition or next action. The work view
  is not built; when it is, it should first ship without the next-action field.
- D2: revision joins stay `candidate`. The brief, the morning report block
  (`candidate_links:` line) and Dias's Trails view (mdn87/dias#72) explain a
  candidate as a revision join no owner record confirms.
- D3: handoff note paths appear only in the brief's local text rendering, which
  never runs on the context owner. The JSON form and the export carry no path.
- D4: no change; the watcher keeps 30-second discovery.
- D5: `autowork.outcome_schema_versions` in configuration (default `[1]`) admits
  outcome schemas; unknown fields inside an admitted schema are ignored and a
  fixture covers that. The request for Autowork to bump its version on schema
  changes was filed as mdn87/lugos#648.
- D6: the server's Autowork was reinstalled from its lugos checkout into a new
  release venv, so it emits schema 2, and the server configuration admits
  `[1, 2]`.

Not done: removing the owner schema version from the trail digest (finding
D6-digest-design). Plan section 3 makes the pinned owner schema part of each
source record, and Dias's reader rejects any change to that closed schema, so
this needs its own cross-repository contract change rather than a one-line
edit. The reinstall's one-time digest change was accepted instead, as the
review's D6 recommendation allows.
