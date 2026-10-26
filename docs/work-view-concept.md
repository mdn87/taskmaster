# Taskmaster as the work-facing view

Concept, 2026-10-19. Slice 1 (the Cirra adapter) is built and on main
(section 10); slices 2 and 3 are not implemented. This coalesces a proposal to absorb the
product ideas of Storybloq (storybloq.com/cli) into Lugos by widening
Taskmaster, and checks it against the components as they exist on disk today.
It adds one read adapter and two derived views to Taskmaster. It moves no
authority: Cirra still owns findings and gates, Autowork still owns admission
and execution, LIR still owns resumption, and Taskmaster still owns no record
of anything another tool owns ([README](../README.md)). Nothing here
authorizes implementation; the [implementation plan](implementation-plan.md)
keeps its non-authorization line.

Sources read for this concept: the Storybloq CLI page; Taskmaster's README,
implementation plan (rev 2.2), [continuous tracking](continuous-tracking.md),
[handoff status](handoff-status.md), [read footprint](read-footprint.md) and
[evidence review](evidence-review.md); Cirra's README,
`docs/audit-workflow.md`, `src/cirra/audits.py` and `src/cirra/release.py`;
Autowork's README, `lugos/docs/agent-assignment.md` and
`docs/dispatch-outcome.md`; LIR's `boundaries.md` and protocol v1; OGMI's
contract schemas; Dias's `docs/TASKMASTER.md`. Sibling repositories are
private, so their paths are references, not links.

## 1. The idea

Storybloq gives a coding agent one place to start a session. `/story` in
Claude Code or `$story` in Codex loads the project's status, shows what
changed since the last snapshot, reads the recent handovers, loads rules and
lessons, and suggests the next story. Before stopping, the agent writes a
handover. Everything else, including execution and review, stays with the
coding client and its backends.

Lugos already holds every record such a surface needs, spread across Cirra
(findings, audits, gates), Autowork (assignments, runs, outcomes), the session
handoff notes, OGMI (receipts, authority, threads) and Sulis (memory). It also
already has a read-only evidence view over some of them: Taskmaster. The
concept is to make Taskmaster that surface:

- connect Cirra findings to objectives and to the runs that touched the same
  revisions;
- say, per objective, what is actionable and why, separating missing evidence
  from unfinished work;
- render one work brief that an agent session, LIR pickup, Dias and the
  morning report all consume;
- recommend the next action, with the authority it would need, without ever
  being that authority.

Taskmaster keeps its current boundary. It schedules nothing, admits nothing,
approves nothing, writes no handover, and keeps no backlog. "What is ready"
is computed from authoritative records on every refresh and published as a
derived view, exactly as the export is today.

## 2. Where each Storybloq responsibility lands

| Storybloq bundles | Storybloq's form | Lugos owner | Taskmaster's part |
|---|---|---|---|
| Work tracking: stories, issues, phases, dependencies, blockers | `.story/tickets/`, `.story/issues/`, `roadmap.json` | Objectives: `taskmaster.toml` plus operator annotations. Issues: Cirra findings. Blockers: Autowork launch state, Cirra queue state, missing evidence | Joins them; keeps no ticket store |
| Session continuity: handovers, changes since last snapshot, decisions | `.story/handovers/`, `.story/snapshots/`, `.story/notes/` | Session handoff notes (`.claude/handoff.md`, `.Codex/handoff.md`); Taskmaster export snapshots; decisions in repository docs and OGMI records; LIR for resumption across systems | Observes notes (the existing ledger); diffs two of its own snapshots; writes no handover |
| Rules and lessons | `.story/lessons/`, config | Governing instruction files and OGMI authority manifests; Sulis memory | References them; copies nothing |
| Workflow guidance: plan, independent review, implement, test, complete | `/story auto` | Cormac inside a session; Autowork campaign phases for delegated work; Cirra `prepare-review` and `import-review` for independent review | None |
| Next-story recommendation | Blockers, unfinished work, what a story would unblock | Taskmaster | The actionable-work view |
| Coordination: scoped assignments, gate acknowledgments, work claims | Duet, team mode, bus | Autowork immutable assignments and admission; OGMI review receipts bound to artifact hashes; TORC leases | Shows them; issues none |
| Visibility | CLI, MCP | Dias TRAILS, morning report, `/taskmaster` | One derived view, rendered everywhere |

Three Storybloq rules survive the move unchanged:

1. Evidence records review activity; it does not guarantee correct code.
   Taskmaster already says its counts are never completion percentages.
2. A review acknowledgment references a specific plan or staged code state.
   Every finding, audit and verification Taskmaster shows binds to a revision
   and a record hash.
3. Peer messages are advice, not owner approval. A Taskmaster recommendation
   is never admission, as Orca advice and LIR proposals are never authority
   today.

## 3. Division of responsibility

| Component | Owns | Taskmaster reads | Taskmaster never |
|---|---|---|---|
| Cirra | Finding identity and lifecycle (`new`, `open`, `resolved`, keyed by concept IDs plus path), one audit attempt per revision and tier, the `pr`, `release` and `full` gates, release waivers, review runs | Audit rows, comparisons, gate results, waiver records | Creates, closes, re-rates or waives a finding; queues an audit |
| Autowork | Admission, immutable assignments, runs, outcomes, cross-review | The campaign outcome projection it reads today, plus the assignment and request summary a run binds | Submits, admits or dispatches; infers acceptance |
| Taskmaster | A derived evidence and work view: objective, revision, finding and run joins; dispositions; the work brief | Its own export, handoff ledger and session registry | Owns a backlog, a schedule, a lease or any authoritative record |
| LIR | Thread resumption: manifest, checkpoint, pickup, Thread Brief | Nothing from LIR in the first slices | Mints a thread, moves a cursor, executes `continue` |
| Dias, morning report | Operator display | Taskmaster's published files | Writes authority |
| OGMI, TORC, Sulis | Records and contracts, lineage and authority, memory | Referenced by id and hash | Becomes a second record of any of them |

## 4. The repair loop

```text
Cirra audits revision R                 Taskmaster refresh
  attempt, findings, gate  â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¶  objective â—€â”€ run â—€â”€ revision R â—€â”€ audit
                                         disposition + next action
                                           â”‚
                                           â–¼ proposed repair (a submission, not an assignment)
                                         operator, or LIR continue under a TORC grant
                                           â”‚
                                           â–¼
                                         Autowork admission â”€â–¶ assignment â”€â–¶ run â”€â–¶ revision R'
                                           â”‚
Cirra audits R' â—€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
  finding open or resolved â”€â–¶ Taskmaster refresh â”€â–¶ work brief â”€â–¶ agent session, LIR pickup, Dias, morning report
```

Rules of the loop:

- Handoff is by durable record only. Cirra's audit rows and packets are the
  record; Taskmaster discovers them on its refresh or watcher cycle. A
  notification may trigger a refresh but is never evidence, which is the
  plan's existing rule.
- A successful Autowork run does not resolve a finding. Only a later Cirra
  audit covering the finding's concept does, and only a `full` audit can list
  a finding as `resolved` (`compare()` in `audits.py`).
- A recommendation is not authorization. A proposed repair is a file the
  operator, or LIR `continue` under a TORC grant, submits to Autowork.
  Autowork admission decides, and the assignment Autowork composes is the
  only execution authority.
- Neither process waits on the other. Both run on lugos-host (section 11):
  Cirra as its webhook and worker services, Taskmaster as a service of its
  own. Cirra serves its records over a loopback route; Taskmaster reads them
  on its own cycle. Neither calls into the other's process.

## 5. Cirra to Taskmaster, concretely

What Cirra already records per audit attempt (the `audits` table,
`audit-packets/<attempt_id>.json` and `audit-waivers/<request_id>.json`):

| Cirra record | Fields Taskmaster would use |
|---|---|
| Audit attempt | `attempt_id`, `audit_id` (digest of policy, tier, repository and head), `tier`, `head`, `state`, `created_at`, `completed_at` |
| Report | `findings[]` with `path`, `side`, `start_line`, `end_line`, `severity`, `title` (which carries the concept IDs) and `evidence`; `coverage[]` per concept |
| Comparison | `baseline_attempt`, `new[]`, `open[]`, `resolved[]` as finding keys |
| Gate | `state` (`pass`, `block`, `pending`), `level`, `blocking[]`; the release gate record stored with a release request or waiver adds `passed` or `waived` with `reason` and `waived_at` |

A finding key is the sorted concept IDs plus the path. Cirra already uses it
to match findings across audits, so Taskmaster adopts it unchanged and never
matches by prose.

On the Taskmaster side this fits the existing identity model (plan section 3)
without new machinery:

- A new source owner `cirra` in `live` mode against a read-only loopback
  route that Cirra serves from its own state directory (decided 2026-10-19;
  section 11). Each response carries the record, its SHA-256 and the audit
  store's generation, so the existing size bounds, footprint hashing and
  `source_changed_during_read` refusal apply unchanged. `saved_snapshot` mode
  remains for fixtures only: recorded responses with a
  `taskmaster.corpus-manifest/v1` manifest, which is how the adapter is
  tested without a running Cirra.
- Record kinds `production_audit` (id: `attempt_id`), `finding_observation`
  (id: SHA-256 of the canonical `[attempt_id, finding_key]` pair) and
  `audit_gate` (id: `audit_id`). These are distinct from the existing
  `audit_receipt` kind, which is a Lugos Audit receipt.
- A derived finding item, keyed by repository key plus finding key, groups
  observations. The same defect on three successive commits is one item with
  three observations, which is what Cirra's `open` list already implies.
- Relations `has_production_audit` (revision to attempt, same repository and
  head) and `observes_finding` (attempt to observation). The existing
  `has_revision` relation supplies the join from objective to revision.
- Membership provenance stays as it is. A revision join is `owner_recorded`
  but reaches only `candidate` link status, in the same way first-release
  Lugos Audit receipts are candidate-only today: an objective that ran on
  revision R is not automatically responsible for every finding on R. An
  operator annotation (`objective.json`) that lists finding items or attempt
  ids, or a run whose assignment cites the item, makes the link `resolved`.
- Gap codes are reused: an unreadable Cirra store is
  `optional_source_unavailable`, a run with no recorded revision is
  `audit_subject_incomplete`, a schema change is `owner_version_mismatch`.
  The first slice should add none.

The five-step integration, restated against these records:

1. Cirra records a finding against revision R in an audit attempt. That row
   and packet are the authoritative record.
2. Taskmaster's refresh reads the attempt through the Cirra adapter, joins it
   to objectives through the revision or an annotation, and checks whether
   existing work already cites the finding item.
3. Taskmaster presents a disposition (section 6) in the work view and the
   brief.
4. An eligible repair is proposed as an Autowork submission: task type,
   required authority and requested capability class (the three things
   Autowork accepts from a client), plus the finding item, revision and
   attempt references as evidence. Autowork composes the assignment.
   Taskmaster retains the objective, finding and run association because the
   annotation or assignment cites the item id.
5. A later Cirra audit of R' updates the item: still `open`, or `resolved`.
   The run's success alone never changes the item.

## 6. Dispositions are derived, never recorded

Taskmaster writes no disposition. Each one is a function of records other
tools own, so the decision lives where the finding or the work commitment
lives:

| Disposition | Derived when | Owning record |
|---|---|---|
| `fix_in_active_task` | A run of this objective cites the finding item, its launch is `ran` and no delivered revision is recorded yet | Autowork assignment and outcome |
| `tracked_follow_up` | An objective annotation cites the item and no run cites it | Operator annotation |
| `duplicate` | Another objective's annotation or run already cites the item; the view names that objective | Operator annotation |
| `waiting_on` | The citing run's launch is `not_run` or `unknown`, or the audit of the fix revision is queued or `pending` | Autowork outcome; Cirra queue |
| `awaiting_verification` | A citing run delivered revision R' and no completed audit of R' at a tier covering the concept exists, or the latest audit still lists the key as `open` | Cirra audits; Autowork outcome |
| `resolved` | A completed `full` audit lists the key under `resolved` | Cirra comparison |
| `unassigned` | No revision join and no annotation reaches the item | None; it is an inbox entry |

Deferring a finding therefore means citing it from a different objective's
annotation. That is a record that owns a work commitment, not a Taskmaster
status.

Two rules with teeth:

**Missing evidence is not unfinished work.** The work view carries two lists
per objective. `work_remaining` holds what an owner record says is not done:
an open finding, a run that did not launch, acceptance not recorded.
`evidence_missing` holds records Taskmaster expected and could not read, each
with its typed gap code. The recommended next action for an evidence gap is a
verification request ("implementation reports delivery of R'; no Cirra audit
of R' exists; request a `full` audit", or "run store unavailable; refresh when
it is"), never an implementation task. The brief never renders a gap as a
to-do for code, so gaps cannot become an endless residual backlog.

**Deferral never changes a gate.** Gate state is copied from Cirra: `pass`,
`block` or `pending`, the level, the blocking keys, and for a release
candidate the `passed` or `waived` record with its reason. A
`tracked_follow_up` disposition on a finding at or above the release level
still shows the release gate as `block`. Only two things change that: a fix
confirmed by a new Cirra audit, or a waiver recorded through
`cirra plan-release --waive-audit`. Taskmaster has no waiver of its own and
shows Cirra's reason verbatim, length-bounded.

## 7. The work brief

Storybloq's session start, as a derived read model `taskmaster.brief/v1`
built from the export, the handoffs projection, the work view and the session
registry. One brief per objective, `--all` for the operator. The sections and
their order are fixed:

1. **Status.** Objective label, freshness flags, evidence age, runs with
   dispatch status, open finding items with severity and gate impact, typed
   gaps.
2. **Since you last looked.** The delta against the snapshot the caller names
   with `--since SNAPSHOT_ID`: new or resolved finding observations, new runs,
   a changed handoff note, a changed gate. Snapshot ids and per-trail hashes
   already exist, so this is a diff of two exports.
3. **Handoff.** `saved`, `stale`, `missing` or `invalid` per provider from
   `handoffs.json`. The local agent-facing render may include the note path so
   the agent reads the note itself; the Dias and report renders keep the
   existing no-body, no-path rule.
4. **Rules and lessons.** References only: the workspace's governing
   instruction files, the OGMI authority manifest id when the objective has
   one, a Sulis recall handle.
5. **Next action.** One recommendation with its reason, the records behind it
   and the authority it needs: `verify` (request a Cirra audit), `repair`
   (propose an Autowork submission for named items at a named revision),
   `decide` (operator: duplicate, defer or waive), `resume` (pick up the saved
   handoff), or `none` (evidence current, nothing actionable). `waiting_on`
   edges give the "what this would unblock" line for free.

Consumers:

- `taskmaster brief` and `/taskmaster brief [objective] [--since ID]`: the
  `/story` equivalent for Claude Code and Codex. It reads the published export
  and never refreshes implicitly; `/taskmaster refresh` stays separate.
- LIR `thread.pickup`: the published brief becomes a manifest-registered
  surface with the snapshot id as its cursor, read through an owner adapter as
  the boundary treaty requires, and labelled in the Thread Brief as a derived
  evidence view. In `continue`, a `repair` recommendation may be the staged
  proposal; TORC and Autowork gate it as they gate any proposal.
- Dias: a loopback-only work-view route beside the handoffs and sessions routes,
  and an unassigned-findings inbox beside the unassigned-sessions inbox.
- Morning report: the next-action line per trail inside the existing verified
  block.

## 8. Authority, in one list

- Taskmaster writes only its export, brief, handoff ledger and session
  registry.
- A recommendation is never admission. Autowork admission, the immutable
  assignment and TORC authority are untouched.
- Cirra owns finding lifecycle, gates and waivers. Taskmaster never re-rates
  a severity or recomputes a gate.
- OGMI's review receipt remains the record that an independent review
  happened; Taskmaster may reference one later but never emits one.
- LIR renders and Taskmaster renders; neither is a source of truth, and there
  is no shared write path.

## 9. Non-goals

- No story, ticket, issue, phase or roadmap store. Objectives stay configured.
- No scheduler, priority queue, claim or lease. Readiness is computed.
- No handover writing, no lessons store, no rules store.
- No automatic conversion of a finding or a gap into an assignment.
- No board posts, live migrations, remote mutations or global installs,
  unchanged from the plan.

## 10. Implementation slices

Each slice would be a new batch after the plan's existing five. This document
authorizes none of them.

**Slice 1: Cirra adapter.** Source owner `cirra`; record kinds
`production_audit`, `finding_observation` and `audit_gate`; relations
`has_production_audit`, `observes_finding` and `has_audit_gate`; candidate
membership by
revision, resolved membership by annotation or citing run; bounded reads with
the read-footprint discipline; existing gap codes only. Transport: the loopback
route of section 11, `GET` only, on 127.0.0.1; the adapter refuses any other
address. Fixture: the recorded responses of one real `cirra/audit` PR check
and one `full` baseline, with a manifest that keeps the capture time, served
to the adapter from disk in tests. Tests: three attempts collapse to one item
with three observations; `open` is not `new`; the gate is copied, never
recomputed; a waiver shows its reason; an unreachable route is a gap, not
work; a non-loopback address is a configuration error, not a gap.

Built 2026-10-19: Cirra `serve-audit-reads`, the Taskmaster `cirra` owner and
`fixtures/cirra`. Where the build differs from the description above, and
why:

- A third relation, `has_audit_gate` (attempt to `audit_gate`, keyed by
  `audit_id`), carries the copied gate: `record` (`published` or `waived`),
  `gate`, `level`, `conclusion`, the `blocking` count and `waiver_reason`.
  Taskmaster keeps owner values on links, so the `audit_gate` record kind
  needed a relation to appear in a trail at all. A gate neither published
  nor waived is a `missing` link, as the fixture's `full` baseline shows.
- Two asserted value types were added for it: bounded prose (`text?`, 4 KiB,
  for `finding_key` and `waiver_reason`) and a count (`int?`, for
  `blocking`). The existing token type could hold neither.
- The store is configured with `owner = "cirra"`, `url` (live) or `path`
  plus `manifest` (saved snapshot) and a `repository` name; an objective
  binds it with `audit_store`. With one configured, a run's recorded
  `source_commit` becomes a `git` revision reference under that name and
  `has_revision` reaches `candidate`; before this slice Taskmaster had no
  revision reference and `has_revision` was always `missing`. The
  `repository_identity_unrecorded` gap stays, because the repository name
  is operator configuration, not Autowork evidence.
- Resolved membership uses the annotation keys `audit_attempts` and
  `finding_items` and the assignment key `finding_items`; section 5 named
  the mechanism, not the keys.
- Read failures land on the `cirra` source row with the existing source
  codes (`optional_source_unavailable`, `optional_source_invalid`,
  `over_bounds`, `owner_version_mismatch`, `source_changed_during_read`)
  and make the entry `partial`. `optional_source_unavailable` is a source
  code in Taskmaster's schema, not a content gap. A generation that moves
  between the responses of one read is retried once, then refused.
- A revision with no attempt is a `missing` `has_production_audit` link, not
  a gap: an unaudited commit is a fact. `audit_subject_incomplete` is
  raised only for a run without a recorded revision while an audit store
  is configured.
- The route serves attempt summaries, attempt details (report findings and
  comparison) and gates. Packets and `coverage[]` are not read in this
  slice; no record needed them.

**Slice 2: Work view and brief.** `taskmaster.work/v1` with dispositions,
`work_remaining`, `evidence_missing` and the next action;
`taskmaster.brief/v1`; `taskmaster brief` and `/taskmaster brief`; the
morning report line; the Dias projection; `verify-export` extended to both
files.

**Slice 3: Proposals and LIR.** The Autowork submission packet a `repair`
recommendation produces, written for the operator or LIR `continue` to
submit, and registration of the brief as an LIR surface. This needs an
Autowork intake decision and an LIR round; Taskmaster cannot start it alone.

## 11. Deployment on lugos-host

Decided 2026-10-19: Taskmaster moves from the workstation to lugos-host,
beside Cirra, and slice 1 reads Cirra over loopback. Loopback rather than a
direct read, although both services run as the same account there and
Taskmaster could open `cirra.sqlite3` itself: the route is Cirra's published
read surface, versioned and bounded by Cirra, while a direct read would bind
Taskmaster to Cirra's schema and to a store the worker writes concurrently.
Co-location rather than a corpus shipped to the workstation: the loopback
bind is the whole access control, so the reader has to be on the host.

The host, as inspected that day: Debian 13 with Python 3.13 only (Taskmaster
requires 3.11 or later and has no third-party dependencies); Cirra under
`/srv/lugos/services/cirra/current`, its webhook receiver on 127.0.0.1:8796
and its worker, both running as `matt` under systemd hardening; the OGMI
service on 127.0.0.1:8789 with the same shape. Cirra's own rule that the
receiver binds 127.0.0.1 behind an HTTPS proxy is the discipline the read
route inherits, minus the proxy: it is reachable only from the host.

**Layout, following Cirra.**

| Purpose | Path |
|---|---|
| Release, with its own `.venv` | `/srv/lugos/releases/taskmaster/<commit>` |
| Selected release | `/srv/lugos/services/taskmaster/current` |
| Configuration | `/srv/lugos/services/taskmaster/config/taskmaster.toml` |
| Saved corpora and annotations | `/srv/lugos/services/taskmaster/corpora/` |
| Everything Taskmaster writes | `/srv/lugos/state/taskmaster/` |
| Read-only replica of the workstation run store | `/srv/lugos/state/autowork-replica/4070pc/runs` |
| Replica puller | `/srv/lugos/services/taskmaster/bin/pull-4070pc-runs.py` |

The first release is commit e924053a of `mdn87/taskmaster`, main as of that
evening, installed from GitHub over HTTPS (the host has no GitHub SSH key for
`matt`). The Autowork CLI that projects owner records is installed into the
same venv from the host's lugos checkout (`/srv/lugos/repos/lugos/autowork`,
version 0.1.0, which `[autowork]` pins). Host paths in the configuration are
absolute.

**Process model.** The service units follow `cirra-worker.service`:
`User=matt`, `ProtectSystem=strict`, `ProtectHome=tmpfs`, `UMask=0077` and
`ReadWritePaths` limited to what each unit writes. `taskmaster-watch.service`
runs `taskmaster watch` over the replica described below, with the
workstation's 48 rules and its 30-second interval; its `ReadWritePaths` is
`/srv/lugos/state/taskmaster` alone, so the replica is read-only to
Taskmaster by the sandbox rather than by convention. `max_age_seconds` is
120, as on the workstation, so a stopped watcher shows within two minutes. A
warm watch cycle on the host takes about 4 seconds; the first cycle after a
start takes about a minute, because the owner's `campaign outcome` runs once
per objective before the outcome cache is filled. The first evening's
`taskmaster-refresh.timer` (a ten-minute `taskmaster refresh` over the saved
corpora) is disabled, its unit files kept.

**What moves now.** The workstation configuration holds 49 objectives: one
recorded pilot trail, 42 trails seeded from a saved snapshot of verified local
runs, and 6 workspace trails bound directly to the Windows run store; the
watcher's 48 rules keep every non-pilot trail live from that store. The host
carries the same 49 objectives and 48 rules, translated to host paths: the
pilot and verified-local corpora copied under `corpora/`, the live store
bound to the replica. Its first export matched the workstation's entry for
entry: the same snapshot id, trail digests and content for all 49, the same
memberships (44 objectives, 92 runs) and the same single quarantined run.
Only the binding digests differ, because they hash paths, and the timestamps,
which are each machine's own. After the context migration below, the host owns
both the evidence export and session/handoff sidecars. Dias reads their verified
local mirror; the workstation watcher is retired.

**The live source (decided 2026-10-19, formerly open question 5).** The host
reads a read-only replica of the workstation's Autowork run store.
`taskmaster-replica-4070pc.timer` runs `bin/pull-4070pc-runs.py` every five
minutes as `matt`: it lists the remote store over SSH (size and modification
time per file), fetches the run directories holding new or changed files with
tar over the same connection, removes what vanished, and records the listing
it materialised in `manifest.json`, with a `status.json` report beside it.
Nothing is ever written on the workstation. Two run subtrees are not
replicated, `credential-isolation` and `child-output`: Taskmaster never reads
them (`sources.SKIPPED_DIRS`), and the first holds junctions into the
workstation profile that Windows refuses to traverse. Whole run directories
are named on tar's command line, because bsdtar reading a name list from
stdin wedges behind Windows sshd once both pipes fill. A full pull of the 493
runs (6,446 files, 387 MB) takes about seven seconds, an unchanged pull under
one. The replica lags the workstation by up to five minutes, which the
watcher's 30-second discovery cannot shorten; the export age exposes a
stopped watcher, not a stalled pull, which `status.json` reports instead.

One divergence to know: the watcher compares workspaces case- and
slash-insensitively only on Windows, and seven request-summary records in the
store spell the workspace with forward slashes. On the host those records do
not match the `lugos/workspace` rule. That rule is handoff-only and the
memberships matched the workstation's, so nothing observed changed, but the
proper fix is in Taskmaster's normalisation, not in the replica.

**Consumer route (decided 2026-10-19).** The operator computer runs a
scheduled-task puller every 30 seconds over the existing strictly
host-key-checked SSH route. It accepts the host export only after the
Taskmaster reader verifies it and its publication age is within the export's
freshness window, then atomically replaces a private local copy. Failed pulls
retain the last accepted bytes and publication time with an explicit failure
status. The five host-owned session and handoff sidecars are fetched in the same
bounded SSH response and validated before publication, including matching report
content and source freshness. Their timestamps are preserved; the workstation
trails report is not copied. The existing Dias local-export junction and the Lugos report
binding now point to the pulled copy. No Dias restart was needed.

An isolated Dias host reported the copy current across four refresh samples
with publication ages from 27 to 42 seconds, below the 120-second limit. The
morning report matched the copy's snapshot and publication timestamp, and the
production reader and all three context routes were current after cutover.
The report helper named in `docs/usage.md` was verified through the new binding
on the operator computer.

**Context ownership and retirement (authorized 2026-10-19).** The host now owns
session reconciliation, assignment overrides, the durable session registry and
handoff ledger. A supervised collector requests the workstation's loopback Dias
context and the 14 explicitly bound standard handoff files over the existing
host-key-checked SSH route every 15 seconds. The host validates raw observations;
a snapshot expires after 45 seconds. Missing, invalid and unavailable notes keep
those meanings. The workstation's timezone is applied to legacy note dates, and
its workspace identity hashes are retained when matching sessions on Linux.
No additional network listener or credentials are required.

Slash commands and existing handoff hooks forward to the host over SSH stdin.
Session corrections carry the invoking client's identity. Handoff hooks submit
the exact saved bytes; the owner validates and appends the revision under its
ledger lock, even when the collector is busy. New workspace registration updates
the host bindings and collector scope. Client forwarding does not require local
validation of another operating system's watcher paths.

The locked import preserved all 189 existing session identities and assignment
histories and the complete 29-event handoff ledger prefix. The workstation task
is stopped and disabled, including its recovery trigger; its durable files remain
unchanged as rollback evidence. Production Dias stayed current beyond its former
freshness window. A collector outage made all three context routes unavailable,
and restarting collection restored them. The existing Autowork replica puller
was not changed. The operator computer still supplies live Dias observations and
note files, so its being offline is represented as unavailable input, while the
host retains durable history.

**What is not settled.** The host's own Autowork account (`autowork`, home
mode 0700) has recorded no runs; when it does, that store is a second one to
bind, not a replacement for the replica.

## 12. Open questions

Decided 2026-10-19, formerly question 1: Taskmaster reads Cirra over a
read-only loopback route and runs on lugos-host (section 11).

Decided 2026-10-19, formerly question 5: live Autowork evidence reaches the
host as a read-only replica of the workstation run store, pulled every five
minutes, with the host watcher over it (section 11).

Decided 2026-10-19, formerly question 5 (consumer route): Dias and the Lugos
report binding read the verified host-export copy pulled every 30 seconds.
The host also owns session/handoff context; a bounded SSH collector reads the
workstation inputs and the workstation watcher is retired (section 11).

Decided 2026-10-19, formerly question 4: Cirra serves the read route from a
separate read-only process, `cirra serve-audit-reads` (unit
`cirra-audit-reads.service`), on 127.0.0.1:8797. It loads no service
configuration and no credentials; `serve-webhooks` is unchanged.

1. Whether revision-join membership should stay `candidate` until an
   annotation or citing run confirms it, as proposed, or whether a
   same-repository, same-head join may be `resolved` on its own.
2. Whether the local agent-facing brief may carry the handoff note path.
3. Whether Cirra should trigger a Taskmaster refresh (a webhook or a beacon),
   or whether the watcher's 30-second discovery is enough.
