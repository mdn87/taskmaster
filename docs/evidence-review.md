# Source review and corrections

Reviewed on 2026-10-17. This is a targeted local source review, not a fresh execution probe or fleet audit. Phase 1 outputs are preserved unchanged under `evidence/phase1/`.

Source baselines: Lugos `fc6c8281cfc19ce2236c413dd903954a18484913`, Dias `6cdc181732dfc3989bca4f99ff4dda03b45be097`, Torc `0bfce27ccac45aaac805ab5f2dc7bfb4229bc339`. Existing unrelated Lugos/submodule changes were present and left untouched. Autowork and Agent Mail are paths inside the Lugos repository in this checkout, not separate Git repositories.

Source paths below are given as `lugos/...` and `dias/...`, relative to the sibling checkouts of those repositories in the MyDocs workspace. Both repositories are private, so the paths are references rather than links.

| Evidence | Source inspected | Planning consequence |
|---|---|---|
| Objective outcome is a read-only owner projection. Task hashes find candidates only. Live reads are not transactional. | Autowork outcome contract (`lugos/autowork/docs/dispatch-outcome.md`), implementation (`lugos/autowork/src/autowork/campaign/dispatch_outcome.py`) | Reuse owner semantics; record annotation provenance; detect concurrent source changes. |
| WorkContext already carries optional `objective_id`, run, assignment, correlation, and a canonical digest. It is strict and versioned. | BoundWorkContext (`lugos/autowork/src/autowork/campaign/dispatch_contract.py`), class at line 656 | Do not invent a competing sealed identity contract or extend its frozen fields silently. Historical records need explicit compatibility. |
| Dispatch has populated `source_commit` paths for both local isolated writes and remote implementations. | Dispatcher (`lugos/autowork/src/autowork/campaign/dispatcher.py`), source handling near lines 1337–1387 | “Always null” is a finding about the sampled historical records, not all current Autowork runs. Expand provenance only where missing; distinguish input and output subjects. |
| Root-envelope parent is currently null in scope compilation. | Scope compiler (`lugos/autowork/src/autowork/campaign/scope_governance.py`), `compile_scope_binding` | A future root reference must be supplied and validated; do not derive authority from a hash alone. |
| Audit subject includes repository/revision and may include a working-tree digest; receipt status distinguishes invalid, stale, overridden, and valid. | Audit receipt schema (`lugos/config/cormac/audit-receipt.schema.json`) | Repository and commit are candidate joins, not sufficient proof of exact audited state or successful checks. |
| LIR has exact subject resolution, immutable outcome receipt ancestry, and distinct owner responsibilities. OGMI relationships can already connect runs, receipts, threads, and repositories. | LIR v1 (`lugos/docs/lir/lir-protocol-v1.md`), LIR v0 (`lugos/docs/lir/lir-protocol-v0.md`), relationship schema (`lugos/ogmi/src/ogmi/contracts/plir-relationship.schema.json`) | Taskmaster is a derived evidence view, not another continuity graph authority. Existing relationships may supply links; they are not automatically objective membership. |
| Torc consumes assignment/outcome references and keeps lineage authority distinct from task assignment. | Integration boundaries (`lugos/torc/docs/integration-boundaries.md`) | Use documented read adapters; no direct display access to Torc stores and no authority inferred from a notification. |
| Dias linked run IDs identify its live registry; current architecture keeps private targets on the host and display projections bounded. | Chip board plan (`dias/docs/plans/CHIP-BOARD-PHASE-1D.md`), concept (`dias/CONCEPT.md`), architecture (`dias/docs/ARCHITECTURE.md`), README (`dias/README.md`) | Preserve `linkedRunIds`; introduce explicit objective mapping and safe display projection. All three consumers are first-release requirements per the operator. |
| Morning report reads a filesystem inbox and currently stops when it is empty. It also has action/archival behavior beyond reading. | Report source (`lugos/plugins/lugos-toolset/skills/morning-report/SKILL.md`), inbox contract (`lugos/plugins/lugos-toolset/docs/inbox-contract.md`) | Add a separate read-only trail summary before the empty-inbox return; do not invoke its action workflow during planning. No board schema change is necessary for this consumer. |
| Board roots have no structured references. Archival is based on the global ten-root window and affects thread reads/replies. | Board implementation (`lugos/agent-mail/src/agent_mail/board.py`), `_archived`, `list_current`, `thread`, `reply`, `history` | Filter semantics must preserve archival and access behavior. Migration is not just four optional response fields. |
| HUD reads autosync board posts and classifies their emoji-prefixed bodies. | HUD reader (`lugos/lugos-hud/src/loadAutosyncStatus.mjs`) | Metadata filters improve selection; they do not themselves replace the free-text status classifier. This reader concerns autosync, not proof of crawler-consumer behavior. |
| The Task Broker is documented as disabled after a failed rollout. | Agent Mail README (`lugos/agent-mail/README.md`), Executable Task Broker | Keep it out of scope; Taskmaster does not reactivate execution transport. |

## Corrections to the earlier design

1. Exclude observation timestamps from the trail content hash; otherwise identical evidence creates a new trail every time. Track freshness separately and never refresh it on failure.
2. Preserve historical Phase 1 findings as sample results. “No owner writes objective_id” is too broad given WorkContext; the observed missing ordinary-run reverse binding is the useful claim.
3. Namespace objective/run references across sources and hosts. A bare `run_id` is not a universal collision-proof relationship key.
4. Require explicit relationship evidence, not merely a bag of referenced IDs and hashes. A matching hash proves integrity of those bytes, not authority, acceptance, or causality.
5. Do not promise duplicate suppression without producer keys, or compatibility without testing old clients and board archival rules.
6. Keep the original historical policy match labelled as a prior manual check. This planning pass did not re-run it or verify current remote Torc state.

## Open evidence, not planning blockers

- The seven-run probe covers one historical objective. Cross-review uses different records and needs a manifest-based adapter.
- The exact active Dias host implementation and deployment path must be rechecked when its implementation batch begins; Phase 1H is a staged platform transition.
- No remote Torc store, board traffic, production migration, or automated board consumer was inspected in this planning pass.
- The original scratchpad contains references to sibling repositories that are no longer relative to its preserved location. The table above supplies current workspace links; original evidence was not rewritten.
- No claim is made that the proposed schema, CLI, adapters, or consumer UI already exist or pass runtime tests.
