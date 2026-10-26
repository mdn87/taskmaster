"""The cirra source owner: slice 1 of the work-view concept (section 10 tests)."""

import hashlib
import http.server
import json
import socket
import threading
import urllib.parse
import unittest
from pathlib import Path

from taskmaster import builder, cirra, cli, config, export, sources

from tests.helpers import FIXTURES, NOW, Workspace, make_run, write_json
from tests.test_builder import build, gaps, links, only

LATER = "2026-10-17T13:00:00Z"
GENERATION = "e" * 64
REPOSITORY_KEY = "beb68b09ff5625785bc923beb92957fa3f92049e15135873ccd89bd5bd1ade0f"
# The recorded fixture: one cirra/audit PR check (attempt 8c85…) against one full baseline (attempt 217f…).
PR_HEAD = "82ad873d0edf72cfdba6fc2d67bd446a417b794c"
PR_ATTEMPT = "8c85d34160b3447b999aeab9d977c271"
PR_AUDIT = "134c4143182af801bd2e0bc157c78180b5aab9cdf1a90059cb8b8a72a8125ca4"
FULL_HEAD = "4d683db7924120cba5a69e545778fbc361df387c"
FULL_ATTEMPT = "217f6b2898ed4dcaae005aa16fc31d95"
FIXTURE_AUDIT = {"mode": "saved_snapshot", "path": FIXTURES / "cirra" / "recorded",
                 "manifest": FIXTURES / "cirra" / "manifest.json", "repository": "cirra-fixture"}
H1, H2, H3 = "1" * 40, "2" * 40, "3" * 40
C011 = ("[C011] Fixture gap", "app.py", "high")
C012 = ("[C012] Fixture gap", "app.py", "critical")


def envelope(record, generation=GENERATION, schema_version=cirra.SCHEMA_VERSION) -> bytes:
    """A route response as Cirra frames it: the record, its canonical SHA-256 and the store generation."""
    body = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii")
    return json.dumps({"protocol": cirra.PROTOCOL, "schema_version": schema_version, "generation": generation,
                       "record": record, "sha256": hashlib.sha256(body).hexdigest()}, sort_keys=True).encode()


def attempt(attempt_id, head, findings=(), *, tier="pr", audit_id=None, comparison=None, state="complete"):
    return {"attempt_id": attempt_id, "audit_id": audit_id, "head": head, "repository_key": REPOSITORY_KEY,
            "tier": tier, "state": state, "scope": "change:1", "comparison": comparison,
            "report": {"findings": [{"title": title, "path": path, "severity": severity, "side": "new",
                                     "start_line": 1, "end_line": 2, "evidence": "Fixture evidence."}
                                    for title, path, severity in findings]}}


def gate(audit_id, publication=None, waivers=()):
    return {"audit_id": audit_id, "publication": publication, "waivers": list(waivers)}


def record_store(root: Path, attempts, gates=(), *, empty_heads=(), captured_at="2026-10-18T00:00:00Z",
                 generation=GENERATION, schema_version=cirra.SCHEMA_VERSION) -> dict:
    """Recorded responses plus their manifest, as the fixture is laid out; returns the store's config keys."""
    files = {"healthz.json": envelope({"status": "ok", "version": "0.1.0", "audits": len(attempts)},
                                      generation, schema_version)}
    by_head = {head: [] for head in empty_heads}
    for a in attempts:
        by_head.setdefault(a["head"], []).append({"attempt_id": a["attempt_id"], "head": a["head"], "tier": a["tier"]})
    for head, rows in by_head.items():
        files[f"audits-by-head/{head}.json"] = envelope({"audits": rows, "next": None}, generation, schema_version)
    for a in attempts:
        files[f"audits/{a['attempt_id']}.json"] = envelope(a, generation, schema_version)
    for g in gates:
        files[f"gates/{g['audit_id']}.json"] = envelope(g, generation, schema_version)
    recorded = root / "recorded"
    for name, data in files.items():
        (recorded / name).parent.mkdir(parents=True, exist_ok=True)
        (recorded / name).write_bytes(data)
    write_json(root / "manifest.json", {"schema": sources.MANIFEST_SCHEMA, "captured_at": captured_at,
                                        "files": {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}})
    return {"mode": "saved_snapshot", "path": recorded, "manifest": root / "manifest.json"}


class _Route(http.server.BaseHTTPRequestHandler):
    """A stand-in for Cirra's loopback route that serves recorded responses."""

    root: Path

    def log_message(self, *args):
        pass

    def body(self, name):
        path = self.root / name if name else None
        if path is None or not path.is_file():
            return 404, b'{"error":"not found"}'
        return 200, path.read_bytes()

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/healthz":
            name = "healthz.json"
        elif url.path == "/audits":
            name = f"audits-by-head/{urllib.parse.parse_qs(url.query).get('head', [''])[0]}.json"
        elif url.path.startswith("/audits/"):
            name = f"audits/{url.path[8:]}.json"
        elif url.path.startswith("/gates/"):
            name = f"gates/{url.path[7:]}.json"
        else:
            name = None
        status, data = self.body(name)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _MovingRoute(_Route):
    """Every response carries a new generation, as a store written between two reads would."""

    counter = 0

    def body(self, name):
        status, data = super().body(name)
        if status == 200:
            doc = json.loads(data)
            type(self).counter += 1
            doc["generation"] = hashlib.sha256(str(self.counter).encode()).hexdigest()
            data = json.dumps(doc).encode()
        return status, data


def serve(case: unittest.TestCase, root: Path, handler=_Route) -> str:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (handler,), {"root": root}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    case.addCleanup(server.server_close)
    case.addCleanup(server.shutdown)
    return f"http://127.0.0.1:{server.server_port}"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def source_row(entry, owner):
    return next(s for s in entry["observation"]["sources"] if s["owner"] == owner)


def content_source(content, owner):
    return next(s for s in content["sources"] if s["owner"] == owner)


def by_to(content, relation):
    return {l["to"]["id"] if l["to"] else None: l for l in links(content, relation)}


def observations(content, attempt_id):
    return {l["asserted"]["finding_key"]: l for l in links(content, "observes_finding")
            if l["from"]["id"] == attempt_id}


class RecordedFixture(Workspace):
    """The recorded responses of one real cirra/audit PR check and one full baseline."""

    def setUp(self):
        super().setUp()
        make_run(self.store, "r-full", source_commit=FULL_HEAD)
        make_run(self.store, "r-pr", source_commit=PR_HEAD)
        self.annotate(["r-full", "r-pr"])

    def test_snapshot_trail(self):
        entry, c = only(build(self.config(audit=FIXTURE_AUDIT)))
        self.assertEqual(entry["observation"]["attempt_status"], "ok")
        self.assertEqual((source_row(entry, "cirra")["health"], source_row(entry, "cirra")["codes"]), ("ok", []))
        revisions = by_to(c, "has_revision")
        self.assertEqual({k: (v["status"], v["to"]["store_id"]) for k, v in revisions.items()},
                         {FULL_HEAD: ("candidate", "cirra-fixture"), PR_HEAD: ("candidate", "cirra-fixture")})
        audits = by_to(c, "has_production_audit")
        self.assertEqual({k: (v["from"]["id"], v["status"], v["provenance"]) for k, v in audits.items()},
                         {FULL_ATTEMPT: (FULL_HEAD, "candidate", "owner_recorded"),
                          PR_ATTEMPT: (PR_HEAD, "candidate", "owner_recorded")})
        self.assertEqual(audits[PR_ATTEMPT]["asserted"], {"tier": "pr", "state": "complete", "audit_id": PR_AUDIT,
                                                          "baseline_attempt": FULL_ATTEMPT})
        self.assertEqual(audits[FULL_ATTEMPT]["asserted"]["tier"], "full")
        self.assertIsNone(audits[FULL_ATTEMPT]["asserted"]["baseline_attempt"])
        pr, full = observations(c, PR_ATTEMPT), observations(c, FULL_ATTEMPT)
        self.assertEqual({k: (v["asserted"]["severity"], v["asserted"]["comparison"]) for k, v in pr.items()},
                         {"C011:app.py": ("high", "open"), "C012:app.py": ("critical", "new")})
        self.assertEqual({k: (v["asserted"]["severity"], v["asserted"]["comparison"]) for k, v in full.items()},
                         {"C011:app.py": ("high", None)})
        # The same defect on both commits is one item with two observations.
        self.assertEqual(pr["C011:app.py"]["asserted"]["item"], full["C011:app.py"]["asserted"]["item"])
        self.assertEqual(pr["C011:app.py"]["asserted"]["item"], cirra.item_id(REPOSITORY_KEY, "C011:app.py"))
        self.assertNotEqual(pr["C011:app.py"]["to"]["id"], full["C011:app.py"]["to"]["id"])
        self.assertEqual(pr["C012:app.py"]["evidence"][0]["source_ref"]["record_type"], "audit")
        gates = {l["from"]["id"]: l for l in links(c, "has_audit_gate")}
        self.assertEqual(gates[PR_ATTEMPT]["asserted"], {"record": "published", "gate": "block", "level": None,
                                                         "conclusion": "failure", "blocking": 1,
                                                         "waiver_reason": None})
        self.assertEqual((gates[PR_ATTEMPT]["status"], gates[PR_ATTEMPT]["to"]["id"]), ("resolved", PR_AUDIT))
        self.assertEqual((gates[FULL_ATTEMPT]["status"], gates[FULL_ATTEMPT]["to"]), ("missing", None))
        for run_id in ("r-full", "r-pr"):
            self.assertIn(("repository_identity_unrecorded", run_id), gaps(c))
            self.assertNotIn(("audit_subject_incomplete", run_id), gaps(c))
        row = content_source(c, "cirra")
        self.assertEqual((row["source_mode"], row["captured_at"], row["owner_version"], row["owner_schema_version"]),
                         ("saved_snapshot", "2026-10-20T01:59:38Z", "0.1.0", cirra.SCHEMA_VERSION))
        self.assertEqual(sorted(i["record_type"] for i in row["inputs"]),
                         ["audit", "audit", "audits-by-head", "audits-by-head", "gate", "healthz"])
        self.assertEqual(entry["trail_sha256"], only(build(self.config(audit=FIXTURE_AUDIT), now=LATER))[0]["trail_sha256"])

    def test_live_route_matches_the_snapshot(self):
        snapshot = only(build(self.config(audit=FIXTURE_AUDIT)))[1]
        url = serve(self, FIXTURES / "cirra" / "recorded")
        entry, live = only(build(self.config(audit={"mode": "live", "url": url, "repository": "cirra-fixture"})))
        self.assertEqual(entry["observation"]["attempt_status"], "ok")
        for part in ("refs", "links", "gaps"):
            self.assertEqual(live[part], snapshot[part])
        row, recorded = content_source(live, "cirra"), content_source(snapshot, "cirra")
        self.assertEqual((row["source_mode"], row["captured_at"]), ("live", None))
        self.assertEqual(row["inputs"], recorded["inputs"])

    def test_verify_export_accepts_the_export(self):
        cfg = config.load(self.config(audit=FIXTURE_AUDIT))
        path = self.tmp / "export.json"
        path.write_bytes(export.assemble(builder.build_entries(cfg, None, NOW), NOW, cfg.max_age_seconds))
        self.assertEqual(cli.main(["verify-export", "--export", str(path)]), 0)


class SynthesizedStore(Workspace):
    def trail(self, attempts, gates=(), runs=(("r1", H1),), **extra):
        for run_id, head in runs:
            make_run(self.store, run_id, source_commit=head)
        self.annotate([run_id for run_id, _ in runs], **extra.pop("annotation", {}))
        return only(build(self.config(audit=record_store(self.tmp / "audits", attempts, gates, **extra))))

    def test_three_attempts_collapse_to_one_item_with_three_observations(self):
        attempts = [attempt("a1", H1, [C011], tier="full"),
                    attempt("a2", H2, [C011], comparison={"open": ["C011:app.py"], "baseline_attempt": "a1"}),
                    attempt("a3", H3, [C011], comparison={"open": ["C011:app.py"], "baseline_attempt": "a1"})]
        entry, c = self.trail(attempts, runs=(("r1", H1), ("r2", H2), ("r3", H3)))
        self.assertEqual(entry["observation"]["attempt_status"], "ok")
        observed = links(c, "observes_finding")
        self.assertEqual(len(observed), 3)
        self.assertEqual({l["asserted"]["item"] for l in observed}, {cirra.item_id(REPOSITORY_KEY, "C011:app.py")})
        self.assertEqual(len({l["to"]["id"] for l in observed}), 3)
        self.assertEqual({k: v["status"] for k, v in by_to(c, "has_production_audit").items()},
                         {"a1": "candidate", "a2": "candidate", "a3": "candidate"})

    def test_open_is_not_new(self):
        comparison = {"new": ["C012:app.py"], "open": ["C011:app.py"], "resolved": ["C010:app.py"],
                      "baseline_attempt": "a0"}
        _, c = self.trail([attempt("a1", H1, [C011, C012], comparison=comparison)])
        observed = observations(c, "a1")
        self.assertEqual({k: v["asserted"]["comparison"] for k, v in observed.items()},
                         {"C011:app.py": "open", "C012:app.py": "new", "C010:app.py": "resolved"})
        self.assertIsNone(observed["C010:app.py"]["asserted"]["severity"])
        self.assertEqual(observed["C010:app.py"]["evidence"][0]["json_pointer"], "/comparison/resolved/0")
        self.assertEqual(by_to(c, "has_production_audit")["a1"]["asserted"]["baseline_attempt"], "a0")

    def test_gate_is_copied_never_recomputed(self):
        audit_id = "a" * 64
        published = {"state": "published", "audit_id": audit_id, "attempt_id": "a1", "gate": "pass",
                     "conclusion": "success", "blocking": 0, "native_id": "7", "existing": False}
        _, c = self.trail([attempt("a1", H1, [C012], audit_id=audit_id)], [gate(audit_id, published)])
        link = links(c, "has_audit_gate")[0]
        self.assertEqual(link["asserted"], {"record": "published", "gate": "pass", "level": None,
                                            "conclusion": "success", "blocking": 0, "waiver_reason": None})
        self.assertEqual((link["status"], link["to"]["id"], link["evidence"][0]["json_pointer"]),
                         ("resolved", audit_id, "/publication"))

    def test_waiver_shows_its_reason(self):
        audit_id = "b" * 64
        published = {"state": "published", "audit_id": audit_id, "attempt_id": "a1", "gate": "block",
                     "conclusion": "failure", "blocking": 1, "native_id": "8", "existing": False}
        waivers = [{"request_id": "w2", "audit_id": audit_id, "attempt_id": "a1", "level": "critical",
                    "gate": "block", "blocking": ["C012:app.py"], "state": "waived",
                    "reason": "Accepted: the path is fixture-only", "waived_at": "2026-10-18T02:00:00+00:00"},
                   {"request_id": "w1", "audit_id": audit_id, "attempt_id": "a1", "level": "critical",
                    "gate": "block", "blocking": ["C012:app.py"], "state": "waived",
                    "reason": "Earlier waiver", "waived_at": "2026-10-18T01:00:00+00:00"}]
        _, c = self.trail([attempt("a1", H1, [C012], audit_id=audit_id)], [gate(audit_id, published, waivers)])
        link = links(c, "has_audit_gate")[0]
        self.assertEqual(link["asserted"], {"record": "waived", "gate": "block", "level": "critical",
                                            "conclusion": None, "blocking": 1,
                                            "waiver_reason": "Accepted: the path is fixture-only"})
        self.assertEqual(link["evidence"][0]["json_pointer"], "/waivers/0")

    def test_annotation_citing_an_attempt_resolves_membership(self):
        _, c = self.trail([attempt("a1", H1, [C011])], annotation={"audit_attempts": ["a1"]})
        link = by_to(c, "has_production_audit")["a1"]
        self.assertEqual((link["status"], link["provenance"]), ("resolved", "operator_annotated"))
        self.assertEqual([(e["source_ref"]["record_type"], e["json_pointer"]) for e in link["evidence"]],
                         [("audit", "/head"), ("objective-annotation", "/audit_attempts/0")])

    def test_annotation_citing_a_finding_item_resolves_membership(self):
        item = cirra.item_id(REPOSITORY_KEY, "C011:app.py")
        _, c = self.trail([attempt("a1", H1, [C011])], annotation={"finding_items": [item]})
        link = by_to(c, "has_production_audit")["a1"]
        self.assertEqual((link["status"], link["provenance"]), ("resolved", "operator_annotated"))
        self.assertEqual(link["evidence"][1]["json_pointer"], "/finding_items/0")

    def test_run_citing_a_finding_item_resolves_membership(self):
        item = cirra.item_id(REPOSITORY_KEY, "C011:app.py")
        make_run(self.store, "r1", source_commit=H1)
        assignment = next(p for p in self.store.rglob("agent-assignment.json") if p.parent.name == "r1")
        write_json(assignment, {**json.loads(assignment.read_text()), "finding_items": [item]})
        self.annotate(["r1"])
        _, c = only(build(self.config(audit=record_store(self.tmp / "audits", [attempt("a1", H1, [C011])]))))
        link = by_to(c, "has_production_audit")["a1"]
        self.assertEqual((link["status"], link["provenance"]), ("resolved", "owner_recorded"))
        self.assertIn(("agent-assignment", "r1", "/finding_items/0"),
                      [(e["source_ref"]["record_type"], e["source_ref"]["record_id"], e["json_pointer"])
                       for e in link["evidence"]])

    def test_commit_without_attempts_is_missing_not_a_gap(self):
        entry, c = self.trail([], empty_heads=(H1,))
        self.assertEqual(entry["observation"]["attempt_status"], "ok")
        self.assertEqual([(l["from"]["id"], l["to"], l["status"]) for l in links(c, "has_production_audit")],
                         [(H1, None, "missing")])
        self.assertNotIn(("audit_subject_incomplete", "r1"), gaps(c))

    def test_run_without_revision_is_audit_subject_incomplete(self):
        make_run(self.store, "r-none")
        self.annotate(["r-none"])
        _, c = only(build(self.config(audit=record_store(self.tmp / "audits", []))))
        self.assertIn(("audit_subject_incomplete", "r-none"), gaps(c))
        self.assertIn(("revision_unrecorded", "r-none"), gaps(c))
        self.assertEqual(links(c, "has_production_audit"), [])

    def test_schema_change_is_owner_version_mismatch(self):
        entry, c = self.trail([attempt("a1", H1, [C011])], schema_version=cirra.SCHEMA_VERSION + 1)
        self.assertEqual(entry["observation"]["attempt_status"], "partial")
        row = source_row(entry, "cirra")
        self.assertEqual((row["health"], row["codes"]), ("unsupported", ["owner_version_mismatch"]))
        self.assertEqual(links(c, "has_production_audit"), [])
        self.assertEqual(content_source(c, "cirra")["inputs"], [])


class LiveRoute(Workspace):
    def setUp(self):
        super().setUp()
        make_run(self.store, "r1", source_commit=H1)
        self.annotate(["r1"])
        self.recorded = record_store(self.tmp / "audits", [attempt("a1", H1, [C011])])["path"]

    def test_unreachable_route_is_a_gap_not_work(self):
        entry, c = only(build(self.config(audit={"mode": "live", "url": f"http://127.0.0.1:{free_port()}"})))
        self.assertEqual(entry["observation"]["attempt_status"], "partial")
        row = source_row(entry, "cirra")
        self.assertEqual((row["health"], row["codes"]), ("unavailable", ["optional_source_unavailable"]))
        self.assertEqual([l["status"] for l in links(c, "has_revision")], ["candidate"])
        self.assertEqual(links(c, "has_production_audit"), [])
        self.assertEqual(content_source(c, "cirra")["inputs"], [])
        self.assertEqual(source_row(entry, "autowork")["health"], "ok")

    def test_generation_moving_during_the_read_is_refused(self):
        url = serve(self, self.recorded, _MovingRoute)
        entry, _ = only(build(self.config(audit={"mode": "live", "url": url})))
        self.assertEqual(entry["observation"]["attempt_status"], "partial")
        row = source_row(entry, "cirra")
        self.assertEqual((row["health"], row["codes"]), ("changed", ["source_changed_during_read"]))

    def test_non_loopback_address_is_a_configuration_error(self):
        for url in ("http://localhost:8797", "http://10.0.0.5:8797", "http://[::1]:8797", "https://127.0.0.1:8797",
                    "http://127.0.0.1:8797/audits", "http://127.0.0.1:70000", "http://127.0.0.1.evil:8797"):
            with self.subTest(url=url), self.assertRaises(config.ConfigError):
                config.load(self.config(audit={"mode": "live", "url": url}))
        loaded = config.load(self.config(audit={"mode": "live", "url": "http://127.0.0.1:8797"}))
        store = loaded.objectives[0].audit_store
        self.assertEqual((store.owner, store.mode, store.url, store.repository), ("cirra", "live", "http://127.0.0.1:8797", "repo"))
        self.assertTrue(loaded.objectives[0].binding_sha256)

    def test_store_shape_is_checked(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.config(audit={"mode": "live", "url": "http://127.0.0.1:8797", "path": "x"}))
        with self.assertRaises(config.ConfigError):
            config.load(self.config(audit={"mode": "saved_snapshot", "path": "x"}))
        with self.assertRaises(config.ConfigError):
            config.load(self.config(audit={"mode": "saved_snapshot", "path": "x", "manifest": "m",
                                           "url": "http://127.0.0.1:8797"}))


if __name__ == "__main__":
    unittest.main()
