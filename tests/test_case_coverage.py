"""Every Batch 2 fixture case names the test methods that execute it.

``fixtures/batch2/publication/cases.json`` and
``fixtures/batch2/report-helper/cases.json`` describe scenarios; on their own
they prove nothing. The tables below name, for each case id, the test methods
whose bodies set the scenario up and assert its expectation. The suite fails
when a case has no executing test, when a mapped id is absent from its
fixture, or when a referenced method no longer exists, so this mapping cannot
drift from the tests the way a hand-written table can.

Ids and references are plain strings so the tables read as documentation.
A case that no test exercises belongs in the matching ``*_UNCOVERED`` table
with a one-line reason, never in a mapping with a borrowed reference.
"""

import importlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "taskmaster.batch2-cases/v1"
PUBLICATION_FIXTURE = ROOT / "fixtures/batch2/publication/cases.json"
REPORT_HELPER_FIXTURE = ROOT / "fixtures/batch2/report-helper/cases.json"
BATCH3_SCHEMA = "taskmaster.batch3-cases/v1"
BATCH3_FIXTURE = ROOT / "fixtures/batch3/cases.json"

# Refresh publication, the writer lock, sharing violations, invalid-export
# recovery and whole-file reads. The CLI tests start a new interpreter per
# command; the lock and death tests use separate operating-system processes.
# The reader-sharing tests hold real CreateFileW sharing modes and are skipped
# off Windows, where unittest reports the skip rather than a pass.
PUBLICATION = {
    "pub-first-refresh": [
        "tests.test_cli.Cli.test_refresh_and_read_restarts_preserve_content_identity",
    ],
    "pub-unchanged-content": [
        "tests.test_cli.Cli.test_refresh_and_read_restarts_preserve_content_identity",
        "tests.test_publication.Publication.test_first_and_unchanged_refresh",
    ],
    "pub-writer-busy": [
        "tests.test_publication.Publication.test_writer_contention_and_process_death_release",
    ],
    "pub-lock-file-without-holder": [
        "tests.test_publication.Publication.test_writer_contention_and_process_death_release",
    ],
    "pub-process-death-releases-lock": [
        "tests.test_publication.Publication.test_writer_contention_and_process_death_release",
    ],
    "pub-interrupted-before-replace": [
        "tests.test_publication.Publication.test_interrupted_before_replace_preserves_live_file_and_temp",
    ],
    "pub-replace-sharing-violation-transient": [
        "tests.test_publication.Publication.test_windows_conflicting_reader_persistent_and_transient",
    ],
    "pub-replace-sharing-violation-persistent": [
        "tests.test_publication.Publication.test_windows_conflicting_reader_persistent_and_transient",
        "tests.test_publication.Publication.test_failed_replace_keeps_prior_and_records_diagnostic",
    ],
    "pub-existing-export-invalid": [
        "tests.test_cli.Cli.test_refresh_hash_invalid_recovery_requires_explicit_flag",
    ],
    "pub-recover-invalid-export": [
        "tests.test_cli.Cli.test_refresh_hash_invalid_recovery_requires_explicit_flag",
        "tests.test_publication.Publication.test_invalid_recovery_preserves_exact_bytes_before_build",
    ],
    "pub-recover-preservation-fails": [
        "tests.test_publication.Publication.test_preservation_failure_and_unsupported_schema_do_not_build",
    ],
    "pub-objective-removed": [
        "tests.test_cli.Cli.test_refresh_objective_removal_and_rebinding",
    ],
    "pub-objective-rebound-then-fails": [
        "tests.test_cli.Cli.test_refresh_objective_removal_and_rebinding",
    ],
    "pub-over-bounds-isolated": [
        "tests.test_cli.Cli.test_refresh_escaped_entry_overflow_isolates_other_objective",
    ],
    "read-missing": [
        "tests.test_cli.Cli.test_exit_codes",
    ],
    "read-invalid": [
        "tests.test_cli.Cli.test_exit_codes",
    ],
    "read-unsupported": [
        "tests.test_cli.Cli.test_exit_codes",
    ],
    "read-unreadable-sharing": [
        "tests.test_publication.Publication.test_windows_conflicting_reader_persistent_and_transient",
    ],
    "read-restart": [
        "tests.test_cli.Cli.test_refresh_and_read_restarts_preserve_content_identity",
    ],
}
PUBLICATION_UNCOVERED = {}

# The delivered morning-report block: splicing, verification and status lines.
# The helper never reads inbox items and treats the report body as opaque
# bytes, so the empty-inbox body in test_morning_report also stands in for a
# nonempty inbox. The export cases run the real renderer against an absent,
# malformed, schema-v2 and unreadable export (a directory path provokes exit
# 8; no report-helper test holds the file without read sharing), and the
# bounded-failure test delivers each renderer exit code as one status line.
REPORT_HELPER = {
    "rh-unconfigured": [
        "tests.test_morning_report.MorningReport.test_unconfigured_is_identical_and_does_not_launch",
    ],
    "rh-configured-empty-inbox": [
        "tests.test_morning_report.MorningReport.test_configured_empty_inbox_preserves_exact_golden_and_digest",
        "tests.test_morning_report.MorningReport.test_cli_binary_report",
    ],
    "rh-happy-path": [
        "tests.test_morning_report.MorningReport.test_configured_empty_inbox_preserves_exact_golden_and_digest",
    ],
    "rh-export-missing": [
        "tests.test_morning_report.MorningReport.test_missing_invalid_unsupported_and_unreadable_exports",
        "tests.test_morning_report.MorningReport.test_renderer_failures_are_bounded_and_inbox_body_survives",
    ],
    "rh-export-invalid": [
        "tests.test_morning_report.MorningReport.test_missing_invalid_unsupported_and_unreadable_exports",
        "tests.test_morning_report.MorningReport.test_renderer_failures_are_bounded_and_inbox_body_survives",
    ],
    "rh-export-unsupported": [
        "tests.test_morning_report.MorningReport.test_missing_invalid_unsupported_and_unreadable_exports",
        "tests.test_morning_report.MorningReport.test_renderer_failures_are_bounded_and_inbox_body_survives",
    ],
    "rh-export-unreadable": [
        "tests.test_morning_report.MorningReport.test_missing_invalid_unsupported_and_unreadable_exports",
        "tests.test_morning_report.MorningReport.test_renderer_failures_are_bounded_and_inbox_body_survives",
    ],
    "rh-renderer-launch-fails": [
        "tests.test_morning_report.MorningReport.test_launch_and_timeout_failures_are_status_lines",
    ],
    "rh-renderer-nonzero": [
        "tests.test_morning_report.MorningReport.test_renderer_failures_are_bounded_and_inbox_body_survives",
    ],
    "rh-refresh-race": [
        "tests.test_morning_report.MorningReport.test_refresh_race_never_rerenders_or_rereads_export",
    ],
    # One corrupted-delivery test walks the four bad block shapes and CRLF.
    "rh-block-modified": [
        "tests.test_morning_report.MorningReport.test_bad_blocks_fail_and_final_artifact_has_only_typed_line",
    ],
    "rh-block-omitted": [
        "tests.test_morning_report.MorningReport.test_bad_blocks_fail_and_final_artifact_has_only_typed_line",
    ],
    "rh-block-duplicated": [
        "tests.test_morning_report.MorningReport.test_bad_blocks_fail_and_final_artifact_has_only_typed_line",
    ],
    "rh-block-malformed-markers": [
        "tests.test_morning_report.MorningReport.test_bad_blocks_fail_and_final_artifact_has_only_typed_line",
    ],
    "rh-crlf-translation": [
        "tests.test_morning_report.MorningReport.test_bad_blocks_fail_and_final_artifact_has_only_typed_line",
    ],
    "rh-final-newline": [
        "tests.test_morning_report.MorningReport.test_configured_empty_inbox_preserves_exact_golden_and_digest",
    ],
}
REPORT_HELPER_UNCOVERED = {}

# Batch 3 edge cases. Several were already executed by Batch 1 and 2 tests;
# the rest are in test_batch3_edges.
BATCH3 = {
    "b3-reworded-retries": [
        "tests.test_batch3_edges.Membership.test_reworded_retries_resolve_only_by_explicit_membership",
        "tests.test_corpus.RecordedCorpus.test_trail",
    ],
    "b3-duplicate-id-across-stores": [
        "tests.test_batch3_edges.Membership.test_duplicate_ids_across_stores_never_cross",
    ],
    "b3-duplicate-id-within-store": [
        "tests.test_builder.Failures.test_ambiguous_run",
    ],
    "b3-malformed-record": [
        "tests.test_batch3_edges.RunRecords.test_malformed_record_fails_the_objective",
    ],
    "b3-duplicate-key-record": [
        "tests.test_batch3_edges.RunRecords.test_duplicate_key_record_fails_instead_of_hiding_a_field",
    ],
    "b3-oversized-record": [
        "tests.test_batch3_edges.RunRecords.test_oversized_record_fails_before_parsing",
    ],
    "b3-run-id-case": [
        "tests.test_batch3_edges.Identity.test_run_id_case_is_exact",
    ],
    "b3-unicode-run-id": [
        "tests.test_batch3_edges.Identity.test_unicode_run_id_is_not_normalized",
    ],
    "b3-unicode-label": [
        "tests.test_batch3_edges.Identity.test_unicode_label_is_published_verbatim",
        "tests.test_contracts.Identity.test_display_escapes_invisible_and_separators",
    ],
    "b3-required-source-changes": [
        "tests.test_builder.Failures.test_required_churn_retries_once_then_fails",
        "tests.test_core_regressions.ObjectiveIsolation.test_annotation_churn_is_retried_then_retains",
    ],
    "b3-optional-source-changes": [
        "tests.test_builder.Policy.test_policy_churn_is_partial",
        "tests.test_core_regressions.ObjectiveIsolation.test_single_policy_change_retries_to_a_stable_observation",
    ],
    "b3-no-previous-content-failure": [
        "tests.test_builder.Failures.test_owner_failures",
        "tests.test_merge_export.Freshness.test_never_successful",
    ],
    "b3-privacy-filtering": [
        "tests.test_batch3_edges.Privacy.test_unallowlisted_values_and_paths_never_reach_the_export",
        "tests.test_builder.Footprint.test_skipped_dirs_and_unlisted_files",
        "tests.test_core_regressions.ObjectiveIsolation.test_private_tree_refused_before_owner_projection",
    ],
    "b3-clock-boundary": [
        "tests.test_merge_export.Freshness.test_age_and_max",
    ],
    "b3-clock-skew": [
        "tests.test_merge_export.Freshness.test_clock_skew",
    ],
    "b3-unchanged-refresh": [
        "tests.test_builder.HappyPath.test_deterministic_and_store_untouched",
        "tests.test_merge_export.Merge.test_unchanged_trail_keeps_generation_time",
        "tests.test_publication.Publication.test_first_and_unchanged_refresh",
    ],
}
BATCH3_UNCOVERED = {}


def load_cases(path):
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(reference):
    """Return the test method a dotted module.Class.test_method reference names."""
    module_name, class_name, method_name = reference.rsplit(".", 2)
    case = getattr(importlib.import_module(module_name), class_name)
    if not (isinstance(case, type) and issubclass(case, unittest.TestCase)):
        raise TypeError(f"{module_name}.{class_name} is not a unittest.TestCase")
    if not method_name.startswith("test_"):
        raise ValueError(f"{method_name} is not a test method name")
    method = getattr(case, method_name)
    if not callable(method):
        raise TypeError(f"{reference} is not callable")
    return method


class CaseCoverage(unittest.TestCase):
    def assert_cases_executed(self, document, mapping, uncovered, label, schema=SCHEMA):
        self.assertEqual(document.get("schema"), schema, label)
        ids = [case["id"] for case in document["cases"]]
        self.assertEqual(len(ids), len(set(ids)), f"{label}: duplicate case ids")
        fixture_ids, mapped, skipped = set(ids), set(mapping), set(uncovered)
        problems = [f"{label}: {case_id} is both mapped and uncovered"
                    for case_id in sorted(mapped & skipped)]
        problems += [f"{label}: {case_id} has no executing test and is not listed as uncovered"
                     for case_id in sorted(fixture_ids - mapped - skipped)]
        problems += [f"{label}: {case_id} is listed but absent from the fixture"
                     for case_id in sorted((mapped | skipped) - fixture_ids)]
        for case_id, references in mapping.items():
            if not references:
                problems.append(f"{label}: {case_id} lists no executing test")
            for reference in references:
                try:
                    resolve(reference)
                except (ImportError, AttributeError, TypeError, ValueError) as exc:
                    problems.append(f"{label}: {case_id} references missing test {reference} "
                                    f"({type(exc).__name__}: {exc})")
        for case_id, reason in uncovered.items():
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f"{label}: {case_id} is uncovered without a reason")
        if problems:
            self.fail("\n".join(problems))

    def test_publication_cases_name_their_executing_tests(self):
        self.assert_cases_executed(load_cases(PUBLICATION_FIXTURE), PUBLICATION,
                                   PUBLICATION_UNCOVERED, "publication")

    def test_report_helper_cases_name_their_executing_tests(self):
        self.assert_cases_executed(load_cases(REPORT_HELPER_FIXTURE), REPORT_HELPER,
                                   REPORT_HELPER_UNCOVERED, "report-helper")

    def test_batch3_cases_name_their_executing_tests(self):
        self.assert_cases_executed(load_cases(BATCH3_FIXTURE), BATCH3, BATCH3_UNCOVERED, "batch3",
                                   schema=BATCH3_SCHEMA)

    def test_gaps_and_missing_references_fail_by_case_id(self):
        document = {"schema": SCHEMA,
                    "cases": [{"id": "x-mapped"}, {"id": "x-unmapped"}, {"id": "x-skipped"}]}
        mapping = {"x-mapped": ["tests.test_case_coverage.CaseCoverage.test_no_such_method"],
                   "x-stale": []}
        uncovered = {"x-skipped": "exercised nowhere", "x-stale": ""}
        with self.assertRaises(AssertionError) as failure:
            self.assert_cases_executed(document, mapping, uncovered, "probe")
        message = str(failure.exception)
        for expected in ("x-unmapped has no executing test",
                         "x-stale is both mapped and uncovered",
                         "x-stale is listed but absent from the fixture",
                         "x-stale lists no executing test",
                         "x-stale is uncovered without a reason",
                         "x-mapped references missing test "
                         "tests.test_case_coverage.CaseCoverage.test_no_such_method"):
            self.assertIn(expected, message)
        self.assertNotIn("x-skipped", message)
        with self.assertRaises(AssertionError):
            self.assert_cases_executed({"schema": "other/v9", "cases": []}, {}, {}, "probe")
