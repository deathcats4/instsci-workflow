from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from instsci.public_audit import audit_public_package, doctor_report


class PublicAuditTests(unittest.TestCase):
    def test_audit_passes_clean_public_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source_patched" / "instsci").mkdir(parents=True)
            (root / "source_patched" / "README.md").write_text("clean public package\n", encoding="utf-8")

            payload = audit_public_package(root)

            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["issue_count"], 0)

    def test_audit_flags_cache_and_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "source_patched" / "instsci" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "x.pyc").write_bytes(b"cache")
            (root / "README.md").write_text(r"C:\Users\Example\run", encoding="utf-8")

            payload = audit_public_package(root)

            self.assertEqual(payload["status"], "fail")
            self.assertGreaterEqual(payload["summary"].get("python_cache_dir", 0), 1)
            self.assertGreaterEqual(payload["summary"].get("windows_user_path", 0), 1)

    def test_audit_allows_token_variable_names_and_regex_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source_patched").mkdir()
            (root / "source_patched" / "example.py").write_text(
                "token = context.set(value)\npattern = r\"C:\\\\Users\\\\[^\\\\]+\"\n",
                encoding="utf-8",
            )

            payload = audit_public_package(root)

            self.assertEqual(payload["status"], "pass")

    def test_audit_flags_root_historical_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source_patched" / "tests").mkdir(parents=True)

            payload = audit_public_package(root)

            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["summary"].get("root_historical_tests_included"), 1)

    def test_doctor_report_includes_core_checks(self) -> None:
        payload = doctor_report()
        names = {item["name"] for item in payload["checks"]}

        self.assertIn("runtime_dependencies", names)
        self.assertIn("browser_doctor_support", names)
        self.assertIn("publisher_matrix", names)
        self.assertIn("zotero_handoff", names)


if __name__ == "__main__":
    unittest.main()
