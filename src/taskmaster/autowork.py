"""Owner runner for `autowork campaign outcome` (plan section 3).

Taskmaster only reads Autowork's projection. It never admits, schedules or
runs work, and it passes an argument array with stdin closed.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from .config import OwnerCommand
from .sources import SourceError
from .strictjson import StrictJSONError, loads

MAX_OWNER_OUTPUT = 16 * 1024 * 1024
_VERSION = re.compile(r"version (\S+)")


def _run(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SourceError("required_source_failed", "owner command did not complete") from None


def check_version(owner: OwnerCommand) -> str:
    proc = _run([*owner.argv, "--version"], owner.timeout_seconds)
    if proc.returncode != 0:
        raise SourceError("required_source_failed", "owner --version failed")
    match = _VERSION.search(proc.stdout.decode("utf-8", "replace"))
    if match is None:
        raise SourceError("owner_version_mismatch", "owner version is not reported")
    if match.group(1) != owner.version:
        raise SourceError("owner_version_mismatch", "owner version differs from the pinned version")
    return match.group(1)


def outcome(owner: OwnerCommand, runs_root: Path, objective_id: str, run_ids: list[str]) -> dict:
    """Run the owner projection over exactly `run_ids` and validate its shape."""
    with tempfile.TemporaryDirectory(prefix="taskmaster-") as tmp:
        annotation = Path(tmp) / "objective.json"
        annotation.write_text(json.dumps({"objective_id": objective_id, "run_ids": run_ids}), encoding="ascii")
        proc = _run([*owner.argv, "campaign", "outcome", "--runs-root", str(runs_root),
                     "--objective", str(annotation), "--json"], owner.timeout_seconds)
    if proc.returncode != 0:
        raise SourceError("required_source_failed", f"owner exited {proc.returncode}")
    try:
        doc = loads(proc.stdout, max_bytes=MAX_OWNER_OUTPUT, allow_floats=True)
    except StrictJSONError as exc:
        raise SourceError("required_input_invalid", f"owner output: {exc.reason}") from None
    if not isinstance(doc, dict):
        raise SourceError("required_input_invalid", "owner output is not an object")
    if doc.get("schema_version") != 1 or isinstance(doc.get("schema_version"), bool):
        raise SourceError("required_schema_unsupported", "owner output schema_version is not 1")
    attempts = doc.get("attempts")
    if doc.get("objective_id") != objective_id or not isinstance(attempts, list) \
            or not all(isinstance(a, dict) and isinstance(a.get("run_id"), str) for a in attempts):
        raise SourceError("required_input_invalid", "owner output is malformed")
    if sorted(a["run_id"] for a in attempts) != sorted(run_ids):
        raise SourceError("required_input_invalid", "owner output covers different runs")
    return doc
