"""End-to-end against the recorded Autowork corpus and the real owner command.

The corpus is copied from the sibling lugos checkout into a temp dir at runtime
and is never committed here. Set TASKMASTER_AUTOWORK to the owner executable
and TASKMASTER_CORPUS to the `dispatch-outcome` fixture directory to override
the sibling-checkout defaults. The test skips when either is unavailable.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from taskmaster import builder, config, export

from tests.helpers import FIXTURES, NOW, REPO, tree_digest

SIBLING = REPO.parent / "lugos" / "autowork"
OWNER = Path(os.environ.get("TASKMASTER_AUTOWORK") or (
    SIBLING / ".venv" / ("Scripts/autowork.exe" if os.name == "nt" else "bin/autowork")))
CORPUS = Path(os.environ.get("TASKMASTER_CORPUS") or SIBLING / "tests" / "fixtures" / "dispatch-outcome")
MANIFEST = FIXTURES / "corpus" / "retry-chain" / "manifest.json"

# Frozen digest of the retry-chain trail under the config written below.
EXPECTED_TRAIL = "e38a08aa1c0477c23c5e88f7ecdd40959c095493b5380f176b17394cbc460c25"

RUN_IDS = ["20261010-031756-32e020cb6504", "20261010-032042-f327d19ae853", "20261010-054207-d2bfe3a2f237",
           "20261010-055627-1973939c5267", "20261010-055823-20ca47f586d4", "20261010-061255-7c0ee08e5229",
           "20261010-062149-c1e702e16cda"]


@unittest.skipUnless(OWNER.is_file() and (CORPUS / "recorded").is_dir(), "autowork owner or recorded corpus unavailable")
class RecordedCorpus(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="taskmaster-corpus-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(CORPUS / "recorded", self.tmp / "recorded")
        shutil.copyfile(CORPUS / "retry-chain.objective.json", self.tmp / "retry-chain.objective.json")
        shutil.copyfile(MANIFEST, self.tmp / "manifest.json")
        cfg = "\n".join([
            "schema_version = 1",
            "max_age_seconds = 86400",
            "[autowork]",
            f"command = {json.dumps([str(OWNER)])}",
            'version = "0.1.0"',
            "[[stores]]",
            'alias = "fixture"',
            'owner = "autowork"',
            'path = "recorded"',
            'mode = "saved_snapshot"',
            'manifest = "manifest.json"',
            "[[objectives]]",
            'namespace = "lugos"',
            'id = "historical-review-retry-chain"',
            'annotation = "retry-chain.objective.json"',
            'run_store = "fixture"',
            'label = "Historical review retry chain"',
        ]) + "\n"
        (self.tmp / "taskmaster.toml").write_text(cfg, encoding="utf-8")

    def build(self):
        cfg = config.load(self.tmp / "taskmaster.toml")
        return export.verify(export.assemble(builder.build_entries(cfg, None, NOW), NOW, cfg.max_age_seconds))

    def test_trail(self):
        before = tree_digest(self.tmp / "recorded")
        doc = self.build()
        again = self.build()
        self.assertEqual(tree_digest(self.tmp / "recorded"), before)

        entry = doc["entries"][0]
        obs = entry["observation"]
        self.assertEqual((obs["attempt_status"], obs["error_codes"], obs["last_success_at"]),
                         ("ok", ["evidence_time_unknown"], None))
        self.assertEqual(entry["trail_sha256"], again["entries"][0]["trail_sha256"])
        if EXPECTED_TRAIL is not None:
            self.assertEqual(entry["trail_sha256"], EXPECTED_TRAIL)

        content = json.loads(entry["content_json"])
        runs = [l for l in content["links"] if l["relation"] == "has_run"]
        self.assertEqual(sorted(l["to"]["id"] for l in runs), RUN_IDS)
        self.assertEqual({l["status"] for l in runs}, {"resolved"})
        delivered = [l["to"]["id"] for l in runs if l["asserted"]["delivered"]]
        self.assertEqual(delivered, ["20261010-062149-c1e702e16cda"])

        codes = sorted(g["code"] for g in content["gaps"])
        self.assertEqual(codes.count("objective_binding_unrecorded"), 7)
        self.assertEqual(codes.count("revision_unrecorded"), 7)

        autowork = [s for s in content["sources"] if s["owner"] == "autowork"][0]
        self.assertEqual(len(autowork["inputs"]), 50)
        self.assertEqual((autowork["owner_version"], autowork["owner_schema_version"]), ("0.1.0", 1))


if __name__ == "__main__":
    unittest.main()
