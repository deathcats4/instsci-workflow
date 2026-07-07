from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from instsci.cli import _normalize_manifest_row, _write_papers_manifest
from instsci.publisher_batch import PublisherBatchDownloader
from instsci.publisher_matrix import manifest_next_action


CONTRACT_FIXTURES = [
    {
        "case": "oa_direct_success",
        "input": {
            "doi": "10.0000/oa-direct",
            "status": "success",
            "standard_status": "success",
            "result_evidence": "oa_direct",
            "verified_match": True,
        },
        "expected": {
            "file_status": "success",
            "standard_status": "success",
            "result_evidence": "oa_direct",
            "next_action": "none",
            "verified_match": True,
        },
    },
    {
        "case": "publisher_open_pdf_success",
        "input": {
            "doi": "10.0000/publisher-open",
            "status": "success",
            "standard_status": "success",
            "result_evidence": "publisher_open_pdf",
        },
        "expected": {
            "file_status": "success",
            "standard_status": "success",
            "result_evidence": "publisher_open_pdf",
            "next_action": "none",
            "verified_match": True,
        },
    },
    {
        "case": "browser_verified_success",
        "input": {
            "doi": "10.0000/browser",
            "status": "success",
            "standard_status": "success",
            "result_evidence": "browser_verified",
        },
        "expected": {
            "file_status": "success",
            "standard_status": "success",
            "result_evidence": "browser_verified",
            "next_action": "none",
            "verified_match": True,
        },
    },
    {
        "case": "auth_required",
        "input": {
            "doi": "10.0000/auth",
            "status": "missing",
            "standard_status": "auth_required",
            "result_evidence": "not_verified",
        },
        "expected": {
            "file_status": "missing",
            "standard_status": "auth_required",
            "result_evidence": "not_verified",
            "next_action": "complete_institution_login_in_visible_browser_then_retry",
            "verified_match": False,
        },
    },
    {
        "case": "human_verification_required",
        "input": {
            "doi": "10.0000/human",
            "status": "missing",
            "standard_status": "human_verification_required",
            "result_evidence": "not_verified",
        },
        "expected": {
            "file_status": "missing",
            "standard_status": "human_verification_required",
            "result_evidence": "not_verified",
            "next_action": "complete_visible_human_verification_then_retry",
            "verified_match": False,
        },
    },
    {
        "case": "waf_blocked",
        "input": {
            "doi": "10.0000/waf",
            "status": "missing",
            "standard_status": "waf_blocked",
            "result_evidence": "not_verified",
        },
        "expected": {
            "file_status": "missing",
            "standard_status": "waf_blocked",
            "result_evidence": "not_verified",
            "next_action": "stop_batch_and_retry_later_or_use_manual_browser",
            "verified_match": False,
        },
    },
    {
        "case": "access_unavailable",
        "input": {
            "doi": "10.0000/no-access",
            "status": "missing",
            "standard_status": "access_unavailable",
            "result_evidence": "browser_verified",
        },
        "expected": {
            "file_status": "missing",
            "standard_status": "access_unavailable",
            "result_evidence": "browser_verified",
            "next_action": "check_access_in_regular_browser_or_library_subscription",
            "verified_match": False,
        },
    },
    {
        "case": "publisher_error",
        "input": {
            "doi": "10.0000/publisher-error",
            "status": "missing",
            "standard_status": "publisher_error",
            "result_evidence": "browser_verified",
        },
        "expected": {
            "file_status": "missing",
            "standard_status": "publisher_error",
            "result_evidence": "browser_verified",
            "next_action": "retry_later_or_test_another_doi",
            "verified_match": False,
        },
    },
    {
        "case": "pdf_candidate_conflict",
        "input": {
            "doi": "10.0000/conflict",
            "status": "unverified",
            "standard_status": "pdf_candidate_conflict",
            "result_evidence": "http_preflight",
        },
        "expected": {
            "file_status": "unverified",
            "standard_status": "pdf_candidate_conflict",
            "result_evidence": "http_preflight",
            "next_action": "rerun_in_diagnose_mode_and_inspect_pdf_candidates",
            "verified_match": False,
        },
    },
    {
        "case": "capture_failed",
        "input": {
            "doi": "10.0000/capture-failed",
            "status": "missing",
            "standard_status": "capture_failed",
            "result_evidence": "browser_verified",
        },
        "expected": {
            "file_status": "missing",
            "standard_status": "capture_failed",
            "result_evidence": "browser_verified",
            "next_action": "rerun_with_mode_diagnose",
            "verified_match": False,
        },
    },
    {
        "case": "unsupported_publisher",
        "input": {
            "doi": "10.0000/unsupported",
            "status": "missing",
            "standard_status": "unsupported_publisher",
            "result_evidence": "not_verified",
        },
        "expected": {
            "file_status": "missing",
            "standard_status": "unsupported_publisher",
            "result_evidence": "not_verified",
            "next_action": "add_or_update_publisher_profile_before_retry",
            "verified_match": False,
        },
    },
]


class ContractFixtureTests(unittest.TestCase):
    def test_cli_manifest_contract_across_representative_fixtures(self) -> None:
        for fixture in CONTRACT_FIXTURES:
            with self.subTest(case=fixture["case"]):
                normalized = _normalize_manifest_row(dict(fixture["input"]))
                expected = fixture["expected"]
                self.assertEqual(normalized["status"], expected["file_status"])
                self.assertEqual(normalized["file_status"], expected["file_status"])
                self.assertEqual(normalized["standard_status"], expected["standard_status"])
                self.assertEqual(normalized["result_evidence"], expected["result_evidence"])
                self.assertEqual(normalized["verified_match"], expected["verified_match"])
                self.assertEqual(normalized["next_action"], expected["next_action"])
                self.assertIsInstance(normalized["suggested_paths"], list)
                self.assertGreaterEqual(len(normalized["suggested_paths"]), 1)

    def test_publisher_batch_manifest_contract_across_representative_fixtures(self) -> None:
        for fixture in CONTRACT_FIXTURES:
            with self.subTest(case=fixture["case"]):
                normalized = PublisherBatchDownloader._normalize_manifest_item(dict(fixture["input"]))
                expected = fixture["expected"]
                self.assertEqual(normalized["status"], expected["file_status"])
                self.assertEqual(normalized["file_status"], expected["file_status"])
                self.assertEqual(normalized["standard_status"], expected["standard_status"])
                self.assertEqual(normalized["result_evidence"], expected["result_evidence"])
                self.assertEqual(normalized["verified_match"], expected["verified_match"])
                self.assertEqual(normalized["next_action"], expected["next_action"])
                self.assertIsInstance(normalized["suggested_paths"], list)
                self.assertGreaterEqual(len(normalized["suggested_paths"]), 1)

    def test_papers_manifest_summary_counts_cover_status_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            summary = _write_papers_manifest(
                run_dir,
                [dict(fixture["input"]) for fixture in CONTRACT_FIXTURES],
            )
            manifest = json.loads((run_dir / "complete" / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(summary["count"], len(CONTRACT_FIXTURES))
            self.assertEqual(summary["success"], 3)
            self.assertEqual(summary["missing"], 7)
            self.assertEqual(summary["unverified"], 1)
            self.assertEqual(summary["verified_match"], 3)
            self.assertEqual(summary["standard_status_counts"]["success"], 3)
            self.assertIn("suggested_paths", manifest[0])
            self.assertEqual(summary["result_evidence_counts"]["browser_verified"], 4)
            self.assertEqual(summary["result_evidence_counts"]["not_verified"], 4)
            self.assertEqual(summary["result_evidence_counts"]["oa_direct"], 1)
            self.assertEqual(summary["result_evidence_counts"]["publisher_open_pdf"], 1)
            self.assertEqual(summary["result_evidence_counts"]["http_preflight"], 1)
            self.assertEqual({row["doi"] for row in manifest}, {fixture["input"]["doi"] for fixture in CONTRACT_FIXTURES})

    def test_next_action_table_covers_every_fixture_status(self) -> None:
        for fixture in CONTRACT_FIXTURES:
            expected = fixture["expected"]
            with self.subTest(case=fixture["case"]):
                self.assertEqual(
                    manifest_next_action(expected["standard_status"]),
                    expected["next_action"],
                )


if __name__ == "__main__":
    unittest.main()
