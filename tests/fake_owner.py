"""Stand-in for `autowork` driven by a behavior file (argv[1]).

Modes: normal, fail, schema2, schema3, extra-fields, garbage, mutate-store, mutate-policy.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    behavior_path = Path(sys.argv[1])
    behavior = json.loads(behavior_path.read_text(encoding="utf-8"))
    args = sys.argv[2:]
    calls = behavior_path.with_suffix(".calls")
    with open(calls, "a", encoding="ascii") as handle:
        handle.write(" ".join(args[:2]) + "\n")
    if args == ["--version"]:
        print(f"autowork, version {behavior.get('version', '0.1.0')}")
        return 0
    mode = behavior.get("mode", "normal")
    if mode == "fail":
        print("owner failure", file=sys.stderr)
        return 1
    if mode == "garbage":
        sys.stdout.write("{not json")
        return 0
    root = Path(args[args.index("--runs-root") + 1])
    annotation = json.loads(Path(args[args.index("--objective") + 1]).read_text(encoding="ascii"))
    if mode == "mutate-store":
        count = len(calls.read_text(encoding="ascii").splitlines())
        target = root / f"churn-{count}"
        target.mkdir(exist_ok=True)
        (target / "state.json").write_text(json.dumps({"run_id": f"churn-{count}"}), encoding="ascii")
    if mode == "mutate-policy":
        policy = Path(behavior["policy_path"])
        policy.write_bytes(policy.read_bytes() + b"\n")
    attempts = []
    for run_id in annotation["run_ids"]:
        result_path = next(p for p in root.rglob(run_id) if p.is_dir()) / "result.json"
        result = json.loads(result_path.read_text(encoding="ascii")) if result_path.exists() else {}
        attempts.append({
            "run_id": run_id,
            "task_sha256": result.get("task_sha256"),
            "status": result.get("status", "completed"),
            "launch": "ran",
            "last_proven_stage": "completion",
            "delivered": True,
            "acceptance": {"outcome": None},
            "probe": {"status": "cache_reference" if run_id in behavior.get("linked", {}) else "unrecorded",
                      "linked_run_ids": behavior.get("linked", {}).get(run_id, [])},
        })
    if mode == "extra-fields":
        for attempt in attempts:
            attempt["unknown_attempt_field"] = {"nested": [1, 2]}
    doc = {"schema_version": {"schema2": 2, "schema3": 3}.get(mode, 1), "objective_id": annotation["objective_id"],
           "run_ids": annotation["run_ids"], "attempts": attempts, "interventions": [], "summary": {}, "usage": {}}
    if mode == "extra-fields":
        doc["unknown_top_level_field"] = "ignored"
    sys.stdout.write(json.dumps(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
