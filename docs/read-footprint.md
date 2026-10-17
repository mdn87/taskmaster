# Read footprint, owner pin and source modes

This records what Batch 1 actually reads, which owner version it trusts, and how live and saved sources differ (plan sections 3 and 5, finding F14). The code is in `src/taskmaster/sources.py`, `autowork.py` and `builder.py`.

## What one objective reads

| Input | Owner | How it is read | Required |
|---|---|---|---|
| Annotation file named in config | operator | One bounded read (1 MiB), strict JSON, must name the configured objective id | yes |
| Configured run store root | autowork | Footprint walk, described below | yes |
| `autowork campaign outcome --runs-root ROOT --objective FILE --json` | autowork | Subprocess with an argument array, stdin closed, timeout, bounded stdout | yes, but only called when at least one annotated run exists in the store |
| `autowork --version` | autowork | Subprocess, the output must match the pinned version | yes, same condition |
| Policy file, when configured | policy | Raw bytes hashed (1 MiB cap), never parsed | no |
| Chip mappings | taskmaster config | Config only; chip existence is never checked | not a source |

Taskmaster never writes into any of these locations. The temporary annotation passed to the owner goes in the system temp directory, and the owner and validators leave a copied store byte-identical. `tests/test_corpus.py` checks this against the real owner and `tests/test_builder.py` checks it against the fake one.

## Why the footprint is the whole store root

`autowork campaign outcome` finds each run with `rglob(run_id)` over the entire runs root. For attestation cache hits it also scans every `capability-attestation-usage.json` under the root. That means a write anywhere in the store can change the owner's answer, not just a write inside a selected run. The footprint covers:

- every directory under the root, at any depth, except that `credential-isolation/` and `child-output/` are never entered;
- every file whose basename is one of the 16 owner record names (`RECORD_BASENAMES` in `sources.py`), hashed with SHA-256.

The footprint is taken before and after the owner call. If the two differ, the attempt retries once. A second difference fails the objective with `source_changed_during_read`. This catches ordinary concurrent writes, not an adversarial writer timing its changes around the reads.

Limits: 20,000 entries, 64 MiB total, 4 MiB per record. Exceeding any limit, or finding a symlink or Windows reparse point anywhere in the tree, fails with `required_input_invalid`. An unreadable root or a root that isn't a plain directory fails with `required_source_failed`. A run id that matches more than one directory fails with `ambiguous_run_identity`.

### Gap in the footprint

The owner's `rglob` does not skip `credential-isolation/` or `child-output/`, but Taskmaster does, because it must not read them. A directory named after a run id inside one of those folders would be visible to the owner and invisible to the footprint. No such directory exists in the recorded corpus. This is a known gap, not a solved problem.

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
