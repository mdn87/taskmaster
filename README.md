# Taskmaster

Taskmaster is a read-only view of the evidence connecting an objective to its executions and related records. It answers: **what work belongs to this objective, what evidence supports each connection, and what is missing?**

It doesn't schedule, admit, approve or run work, and it doesn't become a second record of anything another tool owns. It reads owner records, references them by id and hash, and reports absent connections as typed gaps instead of guessing them.

## Status

Batch 1 of the [implementation plan](docs/implementation-plan.md) (revision 2.2) is implemented. That covers the strict schemas, canonical hashing, the Autowork source adapter, the pure entry-merge function, the read model, the report renderer and the `build`, `show` and `verify-export` commands. The Batch 1 fixtures pass on Python 3.11 and 3.14 on Windows. See [fixtures](docs/fixtures.md) for what each test proves.

Not done yet:

- Batch 2: atomic `refresh` publication with OS locking, the morning-report helper and Dias integration. The first usable release needs agents, the morning report and Dias together.
- Batch 2 fixtures are authored in `fixtures/batch2/` but haven't been run against any consumer.
- Nothing is deployed or scheduled.

## Use

```
pip install -e .
taskmaster build --config taskmaster.toml --now 2026-10-17T12:00:00Z > export.json
taskmaster show --export export.json --all --format report
taskmaster show --export export.json --namespace NS --id ID --json
taskmaster verify-export --export export.json
```

`build` writes the export to stdout and exits 5 if any objective failed. The export is still written in that case. Exit codes are listed in `src/taskmaster/cli.py` and plan section 7.

## Tests

```
PYTHONPATH="src;." py -3.11 -m unittest discover -s tests -t .
```

Use `src:.` outside Windows. `tests/test_corpus.py` runs the real `autowork` against its recorded fixtures from a sibling `lugos/autowork` checkout. Point `TASKMASTER_AUTOWORK` and `TASKMASTER_CORPUS` elsewhere, or let it skip.

## Documents

- [Implementation plan](docs/implementation-plan.md): scope, contracts, batches, acceptance gates and approval boundaries.
- [Read footprint](docs/read-footprint.md): exactly what Batch 1 reads, the pinned owner version and live versus saved sources.
- [Fixtures](docs/fixtures.md): what is executed and passing, and what is authored only.
- [Evidence review](docs/evidence-review.md): source locations checked during planning and remaining uncertainties.

The plan cites review reports and Phase 1 evidence under `reviews/` and `evidence/`. Those are local planning records and are not published in this repository.
