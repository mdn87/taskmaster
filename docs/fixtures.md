# Fixtures and what they prove

The first release uses fixed contracts and a shared 12-objective export across the agent CLI, the delivered morning report and Dias's independent TypeScript reader. Integration tests execute the publication and report-helper scenarios; fixture files alone are not passing evidence.

## Batch 1: executed and passing

Run the suite from the repository root with `PYTHONPATH="src;." py -3.11 -m unittest discover -s tests -t .` on Windows. On macOS or Linux, use `src:.` and `python3`. It passes on Python 3.11 and 3.14 on Windows. Other platforms haven't been tested.

| Plan gate item | Test module |
|---|---|
| Canonical and hash vectors, strict parsing, identity and display escaping, time format | `test_contracts.py` |
| Entry merge, binding-aware retention, bounds and escaped-frame isolation, freshness, clock skew, combined status codes, reader acceptance, report golden | `test_merge_export.py`, `fixtures/report/golden-report.txt` |
| Source adapter: annotation failures, owner failures and version pin, ambiguous runs, required and optional churn, policy match/mismatch/absent/unavailable, membership conflict, saved snapshots, footprint skips and link rejection | `test_builder.py` (uses `tests/fake_owner.py`) |
| Seven-run membership and outcome evidence reproduced from the real owner, frozen manifest and trail digest, two builds identical, copied tree byte-identical | `test_corpus.py` (skips without the sibling Autowork checkout) |
| CLI exit codes 0, 2, 3, 4, 5 and 8 and stdout/stderr separation | `test_cli.py` |
| Pure-model half of the Batch 2 agreement gate | `test_agreement.py` |
| Cirra source owner (work-view concept, slice 1): the recorded PR check and full baseline, the same recording served over a loopback HTTP stand-in, three attempts collapsing to one item with three observations, `open` is not `new`, the gate copied and never recomputed, a waiver showing its reason, an unreachable route as a gap, a moving store generation refused, a non-loopback address refused as configuration | `test_cirra.py` (uses `fixtures/cirra`) |

`fixtures/cirra/` holds the recorded responses of Cirra's read-only audit route (`cirra serve-audit-reads`) for one real `cirra/audit` PR check (attempt `8c85d341…`, head `82ad873d…`) and the `full` baseline it was compared against (attempt `217f6b28…`, head `4d683db7…`), recorded on 2026-10-20 against Cirra's own test store. `manifest.json` is a `taskmaster.corpus-manifest/v1` manifest that keeps the capture time and the digest of each recorded response; the responses stay byte-identical to what the route returned, each carrying the record, its SHA-256 and the store generation. The full audit's gate was never published, so its gate response is absent and the trail shows that gate as `missing`.

The corpus test expects 7 resolved `has_run` links, of which only `20261010-062149-c1e702e16cda` was delivered. It expects 7 `objective_binding_unrecorded` gaps, 7 `revision_unrecorded` gaps and 50 owner record inputs. The frozen trail digest is `97012a2c14964dc20659fe8ab259e6bc866d60da4b3f237ed8250530b0ce9b19` in saved-snapshot mode, read with Autowork outcome schema 2. Under schema 1 it was `e38a08aa1c0477c23c5e88f7ecdd40959c095493b5380f176b17394cbc460c25`; the recorded `owner_schema_version` is the only difference. No reverse binding is inferred: the owner records carry no `objective_id`, `scope_binding` or `source_commit`, so those connections remain gaps.

## Batch 2: executable integration coverage

The Windows integration suite exercises the real OS lock and sharing behavior. The report suite verifies the final file against a retained render buffer. Dias carries a versioned copy of the agreement fixture and compares every field without importing Python code.

- `fixtures/batch2/agreement/`: a 12-entry export covering fresh, partial (invalid optional, optional churn), failed with prior content, failed with null content, conflict, stale by age, saved with known and unknown capture time, clock skew, unknown capture plus invalid optional, and clock skew plus partial. `show.json` and `report.txt` are the expected outputs at `--now 2026-10-17T12:00:00Z`. `test_agreement.py` proves the Python model; `test_morning_report.py` proves delivered block bytes; Dias's host and route tests prove independent full-model equality.
- `fixtures/batch2/publication/cases.json`: 19 cases for `refresh` publication, the writer lock, sharing violations, invalid-export recovery and whole-file read errors (exit codes 3, 4, 6, 7 and 8).
- `fixtures/batch2/report-helper/cases.json`: 16 cases for the delivered morning-report block, covering unconfigured and empty inbox, bounded status lines, the refresh race, modified, omitted or duplicated blocks, and CRLF translation.

`test_publication.py` and `test_cli.py` cover atomic refresh, actual competing processes, process-death lock release, interrupted replacement, Windows reader sharing conflicts, exact invalid-export preservation, failure isolation, and removal/rebinding. `test_core_regressions.py` adds malformed annotation isolation, bounded directory/output capture, ledger evidence and required/optional stability checks. Lugos's optional report wrapper has separate integration tests in its plugin scripts directory. `test_case_coverage.py` maps every case id in both `cases.json` files to the test methods that execute it and fails the suite when a case has no executing test or a referenced method no longer exists.

The local three-consumer check also called Dias's live HTTP route, passed its evaluation time to the agent CLI, compared the entire model, and verified the delivered report. The execution evidence remains local rather than publishing machine paths or run records.

Regenerate the agreement outputs after an intended model change with `TASKMASTER_REGENERATE=1`, then review the diff.

## Limits of the evidence

- The builder can't currently produce `optional_source_invalid`, because policy bytes are hashed, not parsed. The agreement fixture builds that state directly through the merge function.
- Stores containing private skipped directories are refused before owner projection; see `read-footprint.md`. An explicitly prepared saved corpus is supported, without upgrading its capture time.
- Windows behavior has been executed. POSIX locking has not been executed in this session.
- Browser checks exercise the existing web view. They do not prove physical Surface endurance or a hardware touch mapping.
