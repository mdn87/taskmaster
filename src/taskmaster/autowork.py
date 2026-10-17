"""Owner runner for `autowork campaign outcome` (plan section 3).

Taskmaster only reads Autowork's projection. It never admits, schedules or
runs work, and it passes an argument array with stdin closed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .config import OwnerCommand
from .sources import SourceError
from .strictjson import StrictJSONError, loads

MAX_OWNER_OUTPUT = 16 * 1024 * 1024
_VERSION = re.compile(r"version (\S+)")


def _run(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Drain both pipes concurrently, stopping at a hard per-pipe byte cap."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except OSError:
        raise SourceError("required_source_failed", "owner command did not complete") from None
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    read_failed = threading.Event()

    def stop():
        try:
            proc.kill()
        except OSError:
            pass

    def drain(pipe, target):
        try:
            with pipe:
                while True:
                    raw = pipe.read1(min(65536, MAX_OWNER_OUTPUT - len(target) + 1))
                    if not raw:
                        return
                    if len(target) + len(raw) > MAX_OWNER_OUTPUT:
                        overflow.set()
                        stop()
                        return
                    target.extend(raw)
        except OSError:
            read_failed.set()
            stop()

    deadline = time.monotonic() + timeout
    readers = [threading.Thread(target=drain, args=(pipe, target), daemon=True)
               for pipe, target in zip((proc.stdout, proc.stderr), buffers)]
    for thread in readers:
        thread.start()
    try:
        proc.wait(timeout=max(0, deadline - time.monotonic()))
        for thread in readers:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in readers):
            raise subprocess.TimeoutExpired(argv, timeout)
    except subprocess.TimeoutExpired:
        stop()
        proc.wait()
        raise SourceError("required_source_failed", "owner command did not complete") from None
    if overflow.is_set():
        raise SourceError("required_input_invalid", "owner output over bounds")
    if read_failed.is_set():
        raise SourceError("required_source_failed", "owner output could not be read")
    return subprocess.CompletedProcess(argv, proc.returncode, bytes(buffers[0]), bytes(buffers[1]))


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
