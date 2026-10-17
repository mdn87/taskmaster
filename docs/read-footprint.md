# Read footprint, owner pin and source modes

This records what Batch 1 actually reads, which owner version it trusts, and how live and saved sources differ (plan sections 3 and 5, finding F14). The code is in `src/taskmaster/sources.py`, `autowork.py` and `builder.py`.

## What one objective reads

| Input | Owner | How it is read | Required |
|---|---|---|---|
| Annotation file named in config | operator | Bounded reads before and after projection (1 MiB each), strict JSON, must name the configured objective id | yes |
| Configured run store root | autowork | Footprint walk, described below | yes |
| `autowork campaign outcome --runs-root ROOT --objective FILE --json` | autowork | Subprocess with an argument array, stdin closed, timeout, concurrent stdout/stderr readers with a 16 MiB cap per stream | yes, but only called when at least one annotated run exists in the store |
| `autowork --version` | autowork | Subprocess, the output must match the pinned version | yes, same condition |
| Policy file, when configured | policy | Raw bytes hashed (1 MiB cap), never parsed | no |
| Chip mappings | taskmaster config | Config only; chip existence is never checked | not a source |

Taskmaster never writes into any of these locations. The temporary annotation passed to the owner goes in the system temp directory, and the owner and validators leave a copied store byte-identical. `tests/test_corpus.py` checks this against the real owner and `tests/test_builder.py` checks it against the fake one.

## Why the footprint is the whole store root

`autowork campaign outcome` finds each run with `rglob(run_id)` over the entire runs root. For attestation cache hits it also scans every `capability-attestation-usage.json` under the root. That means a write anywhere in the store can change the owner's answer, not just a write inside a selected run. The footprint covers:

- every directory under the root, at any depth, except that `credential-isolation/` and `child-output/` are recorded as excluded directories and never entered;
- every file whose basename is one of the 16 owner record names (`RECORD_BASENAMES` in `sources.py`), hashed with SHA-256;
- each direct `*.reservation.json`, `*.result.json`, and `*.outcome.json` file in an `owner-attempts/` ledger. The owner reads these records and uses their statuses to determine launch. They participate in the footprint, saved manifests, source input digests, and each selected run's link evidence. Their exported record types are `owner-attempt-reservation`, `owner-attempt-result`, and `owner-attempt-outcome`; record identity is SHA-256 of the canonical `[run_id, run_relative_record_name]` tuple, keeping repeated slot names distinct without exposing paths.

The footprint, annotation bytes, and saved manifest values are read before and after the owner call. If required inputs differ, the attempt retries once. A second difference fails the objective with `source_changed_during_read`, retaining its prior content under the same configuration binding. Optional policy changes also retry once; continuing optional churn publishes partial coverage when required sources are stable. This catches ordinary concurrent writes, not an adversarial writer timing changes around the reads.

Limits: 20,000 discovered directory entries (including unlisted filenames), 64 MiB total record bytes, 4 MiB per record. Directory enumeration and record reads are bounded before allocation; growing record files are checked again after reading. Exceeding any limit, or finding a symlink or Windows reparse point anywhere in the enumerated tree, fails with `required_input_invalid`. An unreadable root or a root that isn't a plain directory fails with `required_source_failed`. A run id that matches more than one directory fails with `ambiguous_run_identity`. Malformed annotation IDs and per-objective content bounds fail only that objective; other configured objectives may still publish.

### Refusing stores with private subtrees

The owner's `rglob` does not skip `credential-isolation/` or `child-output/`, while Taskmaster must never enter them. If either directory is present anywhere in the configured store, Taskmaster refuses the owner projection with `required_input_invalid` before invoking the owner. Directory presence is sufficient: Taskmaster does not inspect private contents to decide whether they are safe. It makes no stable-live-snapshot claim for that store. A private directory appearing during an observation invalidates the attempt with `source_changed_during_read`.

For such stores the pilot must explicitly configure a sanitized saved corpus that contains only declared owner records and excludes private subtrees. Its manifest must retain the original capture time, or null if unknown; copying never makes evidence current. Taskmaster does not silently copy a live store, convert source mode, or inspect the excluded folders. The existing recorded seven-run corpus satisfies these restrictions. A saved corpus containing excluded directories is refused as well.

## Owner pin

| Field | Pinned value | On mismatch |
|---|---|---|
| `autowork --version` | `autowork, version 0.1.0` (configured as `version = "0.1.0"`) | `owner_version_mismatch` |
| Output `schema_version` | `1` | `required_schema_unsupported` |
| Output `objective_id` and attempt `run_id` set | must equal the annotation | `required_input_invalid` |

Only allowlisted owner fields enter trail content: `status` (as `dispatch_status`), `launch`, `last_proven_stage`, `delivered`, `acceptance.outcome` and `task_sha256`, plus the run ids named by `probe.linked_run_ids`. Their records count as inputs.

## Live and saved sources

| Mode | Evidence time for the run store | Extra check |
|---|---|---|
| `live` | The time of the successful read | none |
| `saved_snapshot` | `captured_at` from the corpus manifest, or null when unknown | The footprint's file hashes must equal the manifest exactly, otherwise `required_input_invalid` |

An unknown capture time stays unknown. The entry gets `evidence_time_unknown`, `last_success_at` is null, and every reader reports it as stale. Taskmaster never substitutes file modification times.

The frozen pilot corpus is `fixtures/corpus/retry-chain/manifest.json`, with 140 record files and `captured_at: null`. It was generated with `sources.manifest_for` from the Autowork repository's `tests/fixtures/dispatch-outcome/recorded/` tree. The corpus itself is not copied into this repository. `tests/test_corpus.py` copies it from a sibling checkout at test time and skips when that checkout is missing.
