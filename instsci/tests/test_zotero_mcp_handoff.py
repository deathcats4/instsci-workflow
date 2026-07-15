from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from instsci.zotero_mcp import (
    build_zotero_mcp_handoff,
    doi_to_url,
    execute_zotero_sync,
    load_manifest_rows,
    resolve_manifest_path,
    write_zotero_mcp_handoff,
)


class FakeZoteroBackend:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, object]] = []
        self.attach_calls: list[dict[str, object]] = []

    def add_by_url(
        self,
        url: str,
        *,
        tags: list[str],
        collections: list[str],
        attach_mode: str,
    ) -> str:
        self.add_calls.append(
            {
                "url": url,
                "tags": tags,
                "collections": collections,
                "attach_mode": attach_mode,
            }
        )
        return "ITEM0001"

    def attach_linked_file(
        self,
        item_key: str,
        pdf_path: Path,
        *,
        title: str,
        tags: list[str],
    ) -> str:
        self.attach_calls.append(
            {
                "item_key": item_key,
                "pdf_path": str(pdf_path),
                "title": title,
                "tags": tags,
            }
        )
        return "ATTACH01"


class ZoteroMcpHandoffTests(unittest.TestCase):
    def test_doi_to_url_normalizes_plain_doi(self) -> None:
        self.assertEqual(doi_to_url("10.0000/example"), "https://doi.org/10.0000/example")
        self.assertEqual(doi_to_url("https://doi.org/10.0000/example"), "https://doi.org/10.0000/example")

    def test_resolve_manifest_path_accepts_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            complete = run_dir / "complete"
            complete.mkdir()
            manifest = complete / "manifest.json"
            manifest.write_text("[]", encoding="utf-8")
            self.assertEqual(resolve_manifest_path(run_dir), manifest)

    def test_load_manifest_rows_prefers_json_for_csv_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_json = Path(tmp) / "manifest.json"
            manifest_csv = Path(tmp) / "manifest.csv"
            manifest_json.write_text('[{"doi":"10.0000/json"}]', encoding="utf-8")
            manifest_csv.write_text("doi\n10.0000/csv\n", encoding="utf-8")
            path, rows = load_manifest_rows(manifest_csv)
            self.assertEqual(path, manifest_json)
            self.assertEqual(rows[0]["doi"], "10.0000/json")

    def test_build_handoff_uses_success_rows_only_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "doi": "10.0000/success",
                            "title": "Success Paper",
                            "publisher": "acs",
                            "file_status": "success",
                            "standard_status": "success",
                            "result_evidence": "browser_verified",
                            "pdf_path": "paper.pdf",
                        },
                        {
                            "doi": "10.0000/missing",
                            "file_status": "missing",
                            "standard_status": "auth_required",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            payload = build_zotero_mcp_handoff(
                manifest,
                tags=["project/test"],
                collections=["Collection Name"],
            )

            self.assertEqual(payload["summary"]["rows"], 2)
            self.assertEqual(payload["summary"]["metadata_imports"], 1)
            self.assertEqual(payload["summary"]["skipped"], 1)
            self.assertEqual(len(payload["actions"]), 1)
            add_action = payload["actions"][0]
            self.assertEqual(add_action["tool"], "zotero_add_by_url")
            self.assertEqual(add_action["params"]["url"], "https://doi.org/10.0000/success")
            self.assertIn("project/test", add_action["params"]["tags"])
            self.assertEqual(add_action["params"]["collections"], ["Collection Name"])

    def test_required_attach_mode_skips_rows_without_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "doi": "10.0000/no-pdf",
                            "file_status": "success",
                            "standard_status": "success",
                            "pdf_path": "missing.pdf",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            payload = build_zotero_mcp_handoff(manifest, attach_mode="required")

            self.assertEqual(payload["summary"]["metadata_imports"], 0)
            self.assertEqual(payload["summary"]["skipped"], 1)
            self.assertEqual(payload["skipped"][0]["reason"], "required_pdf_missing")

    def test_required_attach_mode_accepts_existing_relative_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "doi": "10.0000/with-pdf",
                            "file_status": "success",
                            "standard_status": "success",
                            "pdf_path": "paper.pdf",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            payload = build_zotero_mcp_handoff(manifest, attach_mode="required")

            self.assertEqual(payload["summary"]["metadata_imports"], 1)
            self.assertEqual(payload["actions"][0]["source"]["pdf_path"], "paper.pdf")

    def test_build_handoff_uses_attachment_only_for_no_doi_existing_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "cnki.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "title": "中文文献",
                            "publisher": "CNKI",
                            "file_status": "success",
                            "standard_status": "success",
                            "result_evidence": "browser_verified",
                            "pdf_path": "cnki.pdf",
                            "zotero_item_key": "JGN5J75A",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            payload = build_zotero_mcp_handoff(manifest, attach_mode="required")

            self.assertEqual(payload["summary"]["metadata_imports"], 0)
            self.assertEqual(payload["summary"]["attachment_only"], 1)
            self.assertEqual(payload["actions"][0]["kind"], "attachment_only")
            self.assertEqual(payload["actions"][0]["zotero_item_key"], "JGN5J75A")
            self.assertEqual(payload["actions"][0]["source"]["pdf_path"], "cnki.pdf")

    def test_execute_sync_attachment_only_uses_existing_zotero_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "cnki.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "title": "中文文献",
                            "publisher": "CNKI",
                            "file_status": "success",
                            "standard_status": "success",
                            "result_evidence": "browser_verified",
                            "pdf_path": "cnki.pdf",
                            "zotero_item_key": "JGN5J75A",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            backend = FakeZoteroBackend()

            report = execute_zotero_sync(manifest, backend=backend, attach_mode="required")

            self.assertEqual(report["summary"]["success"], 1)
            self.assertEqual(backend.add_calls, [])
            self.assertEqual(backend.attach_calls[0]["item_key"], "JGN5J75A")
            updated = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(updated[0]["zotero_item_key"], "JGN5J75A")
            self.assertEqual(updated[0]["zotero_attachment_key"], "ATTACH01")
    def test_write_handoff_round_trips_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "handoff.json"
            payload = {"schema": "instsci.zotero_mcp_handoff.v1", "actions": []}
            written = write_zotero_mcp_handoff(payload, output)
            self.assertEqual(written, output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), payload)

    def test_execute_sync_links_pdf_and_writes_manifest_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            manifest = Path(tmp) / "manifest.json"
            report_path = Path(tmp) / "zotero_sync_report.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "doi": "10.0000/with-pdf",
                            "title": "Paper With PDF",
                            "publisher": "acs",
                            "file_status": "success",
                            "standard_status": "success",
                            "result_evidence": "browser_verified",
                            "pdf_path": "paper.pdf",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            backend = FakeZoteroBackend()

            report = execute_zotero_sync(
                manifest,
                backend=backend,
                tags=["project/test"],
                collections=["Collection Name"],
                report_path=report_path,
            )

            self.assertEqual(report["summary"]["success"], 1)
            self.assertEqual(report["summary"]["errors"], 0)
            self.assertEqual(backend.add_calls[0]["url"], "https://doi.org/10.0000/with-pdf")
            self.assertEqual(backend.add_calls[0]["attach_mode"], "none")
            self.assertEqual(backend.attach_calls[0]["item_key"], "ITEM0001")
            updated = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(updated[0]["zotero_status"], "success")
            self.assertEqual(updated[0]["zotero_item_key"], "ITEM0001")
            self.assertEqual(updated[0]["zotero_attachment_key"], "ATTACH01")
            self.assertEqual(updated[0]["zotero_attachment_mode"], "linked_file")
            self.assertEqual(updated[0]["zotero_pdf_path"], str(pdf))
            self.assertNotIn("zotero_note_key", updated[0])
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["schema"], "instsci.zotero_sync_report.v1")

    def test_execute_sync_dry_run_does_not_write_or_call_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            manifest = Path(tmp) / "manifest.json"
            original_rows = [
                {
                    "doi": "10.0000/dry-run",
                    "file_status": "success",
                    "standard_status": "success",
                    "pdf_path": "paper.pdf",
                }
            ]
            manifest.write_text(json.dumps(original_rows), encoding="utf-8")
            backend = FakeZoteroBackend()

            report = execute_zotero_sync(manifest, backend=backend, dry_run=True)

            self.assertEqual(report["summary"]["dry_run"], 1)
            self.assertEqual(backend.add_calls, [])
            self.assertEqual(backend.attach_calls, [])
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8")), original_rows)

    def test_execute_sync_skips_missing_pdf_without_creating_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "doi": "10.0000/missing-pdf",
                            "file_status": "success",
                            "standard_status": "success",
                            "pdf_path": "missing.pdf",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            backend = FakeZoteroBackend()

            report = execute_zotero_sync(manifest, backend=backend, attach_mode="none")

            self.assertEqual(report["summary"]["skipped"], 1)
            self.assertEqual(report["results"][0]["reason"], "pdf_missing")
            self.assertEqual(backend.add_calls, [])
            updated = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(updated[0]["zotero_status"], "skipped")
            self.assertEqual(updated[0]["zotero_sync_error"], "pdf_missing")

    def test_execute_sync_rejects_unsupported_attachment_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text("[]", encoding="utf-8")

            with self.assertRaises(ValueError):
                execute_zotero_sync(manifest, attachment_mode="imported_file", dry_run=True)


if __name__ == "__main__":
    unittest.main()
