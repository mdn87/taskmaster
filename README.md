# Taskmaster

Taskmaster is a read-only view of the evidence connecting an objective to its executions and related records. It answers: **what work belongs to this objective, what evidence supports each connection, and what is missing?**

It doesn't schedule, admit, approve or run work, and it doesn't become a second record of anything another tool owns. It reads owner records, references them by id and hash, and reports absent connections as typed gaps instead of guessing them.

## Status

The [implementation plan](docs/implementation-plan.md) (revision 2.2) defines one first release for agents, the morning report and Dias together. The Python core includes strict schemas, hashing, the Autowork source adapter, per-objective retention, the read model and report renderer. Atomic `refresh` and verified morning-report delivery extend the original Batch 1 work. See [fixtures](docs/fixtures.md) for executed checks and remaining platform limits.

Taskmaster does not deploy or schedule itself. Source configuration and the export path are explicit. A saved corpus keeps its original capture time; unknown age remains visibly stale.

## Use

```
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\taskmaster refresh --config taskmaster.toml --export export.json
taskmaster show --export export.json --all --format report
taskmaster show --export export.json --namespace NS --id ID --json
taskmaster verify-export --export export.json
```

The `show` and `verify-export` commands use the same environment's `taskmaster` executable (activate the environment or use its full relative path). On POSIX use `python3 -m venv .venv` and `.venv/bin/`.

`refresh` holds an OS writer lock, reads the prior export, and atomically replaces it after validation. A failed objective retains its prior evidence when configuration still matches; other objectives can advance. Exit 5 means the export was published with at least one failed objective. Exit 6 means another writer owns the lock; exit 7 means publication failed. Invalid prior data stops refresh with exit 4; explicit `--recover-invalid-export` preserves a verified exact copy before rebuilding. Unsupported newer schemas are never overwritten. Detailed failures stay beside the export in `.taskmaster-diagnostics/`.

`build` remains an in-memory diagnostic command that writes the export to stdout; use `refresh` for publication. `show` never refreshes owner evidence. See [configuration and consumers](docs/usage.md) for setup and the optional report/Dias integrations.

## Tests

```
PYTHONPATH="src;." py -3.11 -m unittest discover -s tests -t .
```

Use `src:.` outside Windows. `tests/test_corpus.py` runs the real `autowork` against its recorded fixtures from a sibling `lugos/autowork` checkout. Point `TASKMASTER_AUTOWORK` and `TASKMASTER_CORPUS` elsewhere, or let it skip.

## Documents

- [Implementation plan](docs/implementation-plan.md): scope, contracts, batches, acceptance gates and approval boundaries.
- [Read footprint](docs/read-footprint.md): permitted inputs, the pinned owner version and live versus saved sources.
- [Fixtures](docs/fixtures.md): executed checks and platform limits.
- [Evidence review](docs/evidence-review.md): source locations checked during planning and remaining uncertainties.

The plan cites review reports and Phase 1 evidence under `reviews/` and `evidence/`. Those are local planning records and are not published in this repository.
