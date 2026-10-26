"""Shared test scaffolding: synthetic stores, configs and the fake owner."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAKE_OWNER = Path(__file__).resolve().parent / "fake_owner.py"
FIXTURES = REPO / "fixtures"
NOW = "2026-10-17T12:00:00Z"
TASK = "a" * 64


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1), encoding="ascii")
    return path


def make_run(store: Path, run_id: str, *, assignment=True, policy_sha=None, objective_id=None,
             source_commit=None, status="completed", subdir="") -> Path:
    run = store / subdir / run_id if subdir else store / run_id
    run.mkdir(parents=True, exist_ok=True)
    result = {"run_id": run_id, "status": status, "task_sha256": TASK}
    if objective_id is not None:
        result["objective_id"] = objective_id
    write_json(run / "result.json", result)
    state = {"run_id": run_id, "status": status}
    if source_commit is not None:
        state["source_commit"] = source_commit
    write_json(run / "state.json", state)
    write_json(run / "request-summary.json", {"task_sha256": TASK})
    if assignment:
        record = {"assignment_id": f"assignment-{run_id}", "task_class": "review", "route": "claude:sonnet-medium",
                  "model": "sonnet", "sandbox": "read-only", "task_sha256": TASK}
        if policy_sha is not None:
            record["scope_binding"] = {"scope_policy_sha256": policy_sha}
        write_json(run / "agent-assignment.json", record)
    return run


def tree_digest(root: Path) -> dict[str, str]:
    """Every file and directory under root, hashed, for byte-identity checks."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        out[rel_dir + "/"] = "dir"
        for name in filenames:
            path = Path(dirpath) / name
            out[(Path(rel_dir) / name).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class Workspace(unittest.TestCase):
    """A temp dir with a synthetic store, annotation, behavior file and config."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="taskmaster-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = self.tmp / "store"
        self.store.mkdir()
        self.behavior = write_json(self.tmp / "behavior.json", {"mode": "normal"})

    def set_behavior(self, **values):
        write_json(self.behavior, {"mode": "normal", **values})

    def annotate(self, run_ids, objective_id="obj-1", name="annotation.json", **extra) -> Path:
        return write_json(self.tmp / name, {"objective_id": objective_id, "run_ids": run_ids, **extra})

    def owner_calls(self) -> list[str]:
        calls = self.behavior.with_suffix(".calls")
        return calls.read_text(encoding="ascii").splitlines() if calls.exists() else []

    def config(self, *, annotation="annotation.json", policy=None, chips=(), mode="live", manifest=None,
               version="0.1.0", label=None, audit=None, schemas=None) -> Path:
        """`audit` adds a cirra store "audits" (its TOML keys besides alias, owner and repository) to the objective."""
        command = json.dumps([sys.executable, str(FAKE_OWNER), str(self.behavior)])
        lines = [
            "schema_version = 1",
            "max_age_seconds = 3600",
            "[autowork]",
            f"command = {command}",
            f'version = "{version}"',
            *([f"outcome_schema_versions = {list(schemas)}"] if schemas is not None else []),
            "[[stores]]",
            'alias = "runs"',
            'owner = "autowork"',
            'path = "store"',
            f'mode = "{mode}"',
        ]
        if manifest:
            lines.append(f'manifest = "{manifest}"')
        if audit is not None:
            lines += ["[[stores]]", 'alias = "audits"', 'owner = "cirra"']
            lines += [f"{key} = {json.dumps(str(value))}" for key, value in {"repository": "repo", **audit}.items()]
        if policy is not None:
            lines += ["[[policies]]", 'alias = "scope"', f'path = "{policy}"']
        lines += ["[[objectives]]", 'namespace = "ops"', 'id = "obj-1"', f'annotation = "{annotation}"',
                  'run_store = "runs"']
        if policy is not None:
            lines.append('policy = "scope"')
        if label is not None:
            lines.append(f'label = "{label}"')
        if audit is not None:
            lines.append('audit_store = "audits"')
        for project, chip in chips:
            lines += ["[[chips]]", f'project_id = "{project}"', f'chip_id = "{chip}"', 'namespace = "ops"',
                      'objective = "obj-1"']
        path = self.tmp / "taskmaster.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
