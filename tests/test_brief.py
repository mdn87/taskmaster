"""The start-of-session brief (decisions D1-D3): export facts only, local paths only in text."""

import contextlib
import io
import json

from taskmaster import brief, builder, cli, commands, config, export, watch

from tests.helpers import NOW, Workspace, make_run, write_json
from tests.test_cirra import FIXTURE_AUDIT, FULL_HEAD, PR_ATTEMPT, PR_AUDIT, PR_HEAD


class Brief(Workspace):
    def setUp(self):
        super().setUp()
        make_run(self.store, "20261017-100000-full", source_commit=FULL_HEAD)
        make_run(self.store, "20261017-110000-pr", source_commit=PR_HEAD)
        self.annotate(["20261017-100000-full", "20261017-110000-pr"])
        self.export = self.tmp / "export.json"
        entries = builder.build_entries(config.load(self.config(audit=FIXTURE_AUDIT)), None, NOW)
        self.export.write_bytes(export.assemble(entries, NOW, 3600))
        self.doc = export.load(self.export)[0]

    def test_brief_carries_export_facts_and_no_next_action(self):
        value = brief.build(self.doc, "ops", "obj-1", NOW)
        self.assertEqual(value["schema"], "taskmaster.brief/v1")
        self.assertEqual(value["evidence"]["snapshot_id"], self.doc["snapshot_id"])
        self.assertEqual(value["evidence"]["age_seconds"], 0)
        self.assertEqual([r["run_id"] for r in value["runs"]["recent"]], ["20261017-110000-pr", "20261017-100000-full"])
        self.assertEqual({(f["finding_key"], f["severity"], tuple(f["comparisons"]), f["attempts"], f["membership"])
                          for f in value["open_findings"]},
                         {("C011:app.py", "high", ("open",), 2, "candidate"),
                          ("C012:app.py", "critical", ("new",), 1, "candidate")})
        self.assertEqual([(g["attempt"], g["audit_id"], g["gate"], g["blocking"], g["membership"])
                          for g in value["audit_gates"]], [(PR_ATTEMPT, PR_AUDIT, "block", 1, "candidate")])
        self.assertGreater(value["membership"]["candidate_links"]["has_production_audit"], 0)
        for absent in ("disposition", "next_action", "work_remaining"):
            self.assertNotIn(absent, json.dumps(value))
        self.assertEqual(brief.build(self.doc, "ops", "nope", NOW), None)

    def test_text_names_candidates_and_freshness(self):
        text = brief.render(brief.build(self.doc, "ops", "obj-1", "2026-10-17T12:10:00Z"))
        self.assertIn("Evidence: last checked 2026-10-17T12:00:00Z, 600s ago (max age 3600s)", text)
        self.assertIn(brief.LAG_NOTE, text)
        self.assertIn(brief.CANDIDATE_NOTE, text)
        self.assertIn("critical C012:app.py (new; 1 audit(s)) [candidate]", text)
        self.assertNotIn("Handoff notes", text)

    def test_handoff_paths_appear_only_in_local_text(self):
        note = self.tmp / "project" / ".claude" / "handoff.md"
        note.parent.mkdir(parents=True)
        note.write_text("note", encoding="utf-8")
        settings = write_json(self.tmp / "watch.json", {
            "export": "export.json",
            "rules": [{"namespace": "ops", "id": "obj-1", "workspace": str(note.parent.parent)}],
            "handoffs": [{"objective": "ops/obj-1", "provider": "claude", "path": str(note)},
                         {"objective": "ops/obj-1", "provider": "codex", "path": str(self.tmp / "missing.md")}]})
        cfg_path = self.config(audit=FIXTURE_AUDIT)
        cfg_path.write_text(f'watch = "{settings.name}"\n' + cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
        cfg = config.load(cfg_path)
        text = commands.local_brief(cfg, [], cwd=note.parent.parent)
        self.assertIn(f"- claude: {note}", text)
        self.assertNotIn("missing.md", text)
        as_json = commands.local_brief(cfg, ["ops/obj-1", "--json"])
        self.assertNotIn(str(self.tmp), json.dumps(as_json))
        self.assertNotIn(str(self.tmp), self.export.read_text(encoding="utf-8"))
        with self.assertRaises(watch.WatchError):
            commands.local_brief(cfg, [], cwd=self.tmp)
        buffer = io.BytesIO()
        with contextlib.redirect_stdout(io.TextIOWrapper(buffer, encoding="utf-8")) as wrapper:
            code = cli.main(["brief", "--export", str(self.export), "--namespace", "ops", "--id", "obj-1",
                             "--now", NOW, "--config", str(cfg_path)])
            wrapper.flush()
        self.assertEqual(code, 0)
        self.assertIn(f"- claude: {note}", buffer.getvalue().decode("utf-8"))

    def test_report_names_candidate_links(self):
        from taskmaster import readmodel, report
        block = report.render(readmodel.show(self.doc, NOW)).decode("utf-8")
        self.assertIn("  candidate_links: joined by revision only; no owner record confirms them\n", block)
