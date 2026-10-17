"""Batch 2 consumer-agreement fixture: the pure-model half (plan section 8).

Builds the multi-state export from merge_entry, then checks the frozen export,
the taskmaster.show/v1 read model and the report block at a fixed clock.
Batch 2 consumers must reproduce the shared fields of show.json from
export.json; passing here proves only the Batch 1 model, not any consumer.

Regenerate with TASKMASTER_REGENERATE=1 after an intended model change, then
review the diff before committing.
"""

import json
import os
import unittest

from taskmaster import export, readmodel, report, schema
from taskmaster.merge import AttemptResult, SourceResult, merge_entry

from tests.helpers import FIXTURES

DIR = FIXTURES / "batch2" / "agreement"
CLOCK = "2026-10-17T12:00:00Z"      # fixed evaluation clock
PUBLISHED = "2026-10-17T12:10:00Z"  # later than the clock, so 12:10 attempts show clock_skew
MAX_AGE = 3600
NS = "agree"
BINDING = "d" * 64
RAW = "e" * 64
EARLIER = "2026-10-17T11:30:00Z"


def ref(owner, store, kind, ref_id):
    return {"owner": owner, "store_id": store, "kind": kind, "id": ref_id}


def content(oid, *, conflict=False):
    obj = ref("operator", NS, "objective", oid)
    run = ref("autowork", "runs", "run", f"{oid}-run")
    status = "conflict" if conflict else "resolved"
    link = {
        "relation": "has_run", "from": obj, "to": run, "status": status, "provenance": "operator_annotated",
        "asserted": {"dispatch_status": "completed", "launch": "ran", "last_proven_stage": "completion",
                     "delivered": True, "acceptance_outcome": None, "task_sha256": None},
        "evidence": [{"source_ref": {"owner": "autowork", "store_id": "runs", "record_type": "result",
                                     "record_id": run["id"]}, "json_pointer": "", "raw_sha256": RAW}],
    }
    gaps = [{"code": "membership_conflict" if conflict else "revision_unrecorded", "affected_ref": run}]
    refs = sorted([obj, run], key=lambda r: schema.sort_key(schema.ref_tuple(r)))
    return {"schema_version": 1, "objective": {"namespace": NS, "id": oid, "label": None}, "sources": [],
            "refs": refs, "links": [link], "gaps": gaps,
            "coverage": [{"adapter": a, "status": "unsupported"} for a in sorted(schema.COVERAGE_ADAPTERS)]}


def required(at, mode="live", health="ok", checked=None):
    # The annotation is always a live read at the attempt time; only the run
    # store carries a saved-corpus capture time (or None when unknown).
    codes = () if health == "ok" else ("required_source_failed",)
    return (SourceResult("operator", NS, True, "live", "ok", (), at),
            SourceResult("autowork", "runs", True, mode, health, codes, checked if health == "ok" else None))


def optional(health, code):
    return SourceResult("policy", "scope", False, "live", health, (code,))


def attempt(oid, at, *, status="ok", mode="live", checked="attempt", policy=None, conflict=False):
    checked = at if checked == "attempt" else checked
    if status == "failed":
        return AttemptResult("failed", None, required(at, mode, "failed"))
    rows = required(at, mode, "ok", checked) + ((policy,) if policy else ())
    return AttemptResult(status, content(oid, conflict=conflict), rows)


def entries():
    def one(oid, at, prior=None, **kw):
        return merge_entry(NS, oid, BINDING, prior, attempt(oid, at, **kw), at)

    prior = one("failed-with-prior-content", EARLIER)
    return [
        one("fresh-resolved", "2026-10-17T11:50:00Z"),
        one("partial-invalid-optional", "2026-10-17T11:50:00Z", status="partial",
            policy=optional("invalid", "optional_source_invalid")),
        one("partial-optional-churn", "2026-10-17T11:50:00Z", status="partial",
            policy=optional("changed", "source_changed_during_read")),
        one("failed-with-prior-content", "2026-10-17T11:55:00Z", prior=prior, status="failed"),
        one("failed-null-content", "2026-10-17T11:55:00Z", status="failed"),
        one("conflict", "2026-10-17T11:50:00Z", conflict=True),
        one("stale-by-age", "2026-10-17T10:00:00Z"),
        one("saved-known-capture", "2026-10-17T11:50:00Z", mode="saved_snapshot", checked="2026-10-17T11:40:00Z"),
        one("saved-unknown-capture", "2026-10-17T11:50:00Z", mode="saved_snapshot", checked=None),
        one("clock-skew", PUBLISHED),
        one("unknown-capture-plus-invalid-optional", "2026-10-17T11:50:00Z", status="partial", mode="saved_snapshot",
            checked=None, policy=optional("invalid", "optional_source_invalid")),
        one("clock-skew-plus-partial", PUBLISHED, status="partial",
            policy=optional("unavailable", "optional_source_unavailable")),
    ]


def render():
    data = export.assemble(entries(), PUBLISHED, MAX_AGE)
    view = readmodel.show(export.verify(data), CLOCK)
    shown = (json.dumps(view, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")
    return {"export.json": data, "show.json": shown, "report.txt": report.render(view)}


class Agreement(unittest.TestCase):
    def test_frozen_outputs(self):
        outputs = render()
        if os.environ.get("TASKMASTER_REGENERATE"):
            DIR.mkdir(parents=True, exist_ok=True)
            for name, data in outputs.items():
                (DIR / name).write_bytes(data)
        for name, data in outputs.items():
            with self.subTest(name):
                self.assertEqual(data, (DIR / name).read_bytes())

    def test_expected_flags(self):
        view = json.loads(render()["show.json"])
        ev = {o["id"]: o["evaluation"] for o in view["objectives"]}
        flags = {k: (e["stale"], e["partial"], e["never_successful"], e["clock_skew"]) for k, e in ev.items()}
        self.assertEqual(flags, {
            "fresh-resolved": (False, False, False, False),
            "partial-invalid-optional": (False, True, False, False),
            "partial-optional-churn": (False, True, False, False),
            "failed-with-prior-content": (True, False, False, False),  # latest attempt failed
            "failed-null-content": (True, False, True, False),
            "conflict": (False, False, False, False),
            "stale-by-age": (True, False, False, False),
            "saved-known-capture": (False, False, False, False),
            "saved-unknown-capture": (True, False, False, False),
            "clock-skew": (True, False, False, True),
            "unknown-capture-plus-invalid-optional": (True, True, False, False),
            "clock-skew-plus-partial": (True, True, False, True),
        })
        codes = {k: e["status_codes"] for k, e in ev.items()}
        self.assertEqual(codes["unknown-capture-plus-invalid-optional"],
                         ["evidence_time_unknown", "optional_source_invalid"])
        self.assertEqual(codes["clock-skew-plus-partial"], ["clock_skew", "optional_source_unavailable"])
        self.assertEqual(codes["failed-with-prior-content"], ["required_source_failed"])


if __name__ == "__main__":
    unittest.main()
