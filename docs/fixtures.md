# Fixtures and what they prove

Batch 1 has two kinds of fixtures. The first kind has executable tests that pass now. The second kind was authored for Batch 2 consumers and has not been run against any consumer yet.

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

The corpus test expects 7 resolved `has_run` links, of which only `20261010-062149-c1e702e16cda` was delivered. It expects 7 `objective_binding_unrecorded` gaps, 7 `revision_unrecorded` gaps and 50 owner record inputs. The frozen trail digest is `e38a08aa1c0477c23c5e88f7ecdd40959c095493b5380f176b17394cbc460c25` in saved-snapshot mode. No reverse binding is inferred: the owner records carry no `objective_id`, `scope_binding` or `source_commit`, so those connections remain gaps.

## Batch 2: authored, not passing integration tests

These files exist so Batch 2 consumers have fixed expectations to meet. Their existence does not mean any consumer passes them.

- `fixtures/batch2/agreement/`: a 12-entry export covering fresh, partial (invalid optional, optional churn), failed with prior content, failed with null content, conflict, stale by age, saved with known and unknown capture time, clock skew, unknown capture plus invalid optional, and clock skew plus partial. `show.json` and `report.txt` are the expected outputs at `--now 2026-10-17T12:00:00Z`. The agents CLI, the morning-report helper and Dias must each reproduce the shared fields of `show.json` from `export.json`. `test_agreement.py` proves only that the Batch 1 model produces them.
- `fixtures/batch2/publication/cases.json`: 19 cases for `refresh` publication, the writer lock, sharing violations, invalid-export recovery and whole-file read errors (exit codes 3, 4, 6, 7 and 8).
- `fixtures/batch2/report-helper/cases.json`: 16 cases for the delivered morning-report block, covering unconfigured and empty inbox, bounded status lines, the refresh race, modified, omitted or duplicated blocks, and CRLF translation.

Regenerate the agreement outputs after an intended model change with `TASKMASTER_REGENERATE=1`, then review the diff.

## Known limits of the Batch 1 evidence

- The builder can't currently produce `optional_source_invalid`, because policy bytes are hashed, not parsed. The agreement fixture builds that state directly through the merge function.
- The footprint gap for run-id directories inside skipped folders is described in `read-footprint.md`.
- Nothing here exercises OS locking, atomic replace, the report helper or Dias. That is Batch 2.
