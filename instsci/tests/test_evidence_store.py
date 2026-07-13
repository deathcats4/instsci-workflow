import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from instsci.config import Config
from instsci.evidence_store import (
    load_private_index,
    load_public_data_policy,
    register_private_run,
)
from instsci.public_audit import audit_public_package


class EvidenceStoreTests(TestCase):
    def test_public_policy_names_public_and_private_assets(self) -> None:
        policy = load_public_data_policy()
        self.assertIn("webvpn.json", policy["public_assets"])
        self.assertIn("institution-specific browser verification records", policy["private_assets"])
        self.assertIn("reference", policy["private_storage_rule"])

    def test_register_private_run_indexes_manifest_without_copying_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runtime" / "run"
            manifest = run_dir / "complete" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('[{"doi":"10.1000/private"}]', encoding="utf-8")
            pdf = run_dir / "complete" / "pdfs" / "paper.pdf"
            pdf.parent.mkdir()
            pdf.write_bytes(b"%PDF-private-fixture")
            cfg = Config(private_evidence_dir=str(root / "private-index"))

            entry = register_private_run(cfg, run_dir, publisher="Example Publisher")
            index = load_private_index(cfg)

            self.assertEqual(entry["storage"], "reference_only")
            self.assertFalse(entry["artifacts_copied"])
            self.assertEqual(entry["manifest_sha256"], hashlib.sha256(manifest.read_bytes()).hexdigest())
            self.assertEqual(index["runs"][0]["run_path"], str(run_dir.resolve()))
            self.assertFalse((Path(cfg.private_evidence_dir) / "paper.pdf").exists())

    def test_public_audit_rejects_private_evidence_paths_and_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "private-evidence").mkdir()
            (root / "publisher.private.json").write_text(json.dumps({"private": True}), encoding="utf-8")
            report = audit_public_package(root, include_institution_scan=False)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("private_evidence_included", codes)
