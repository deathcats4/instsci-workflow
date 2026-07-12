import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class SkillDistributionTests(unittest.TestCase):
    def test_skill_uses_standard_repository_path(self) -> None:
        self.assertTrue((ROOT / "skills" / "instsci" / "SKILL.md").is_file())
        self.assertFalse((ROOT / "skill").exists())

    def test_public_package_manifest_includes_skill_and_installer(self) -> None:
        payload = json.loads((ROOT / "package_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("skills/instsci", payload["includes"])
        self.assertIn("scripts/Install-InstSci.ps1", payload["includes"])

    def test_installer_dry_run_has_no_side_effects(self) -> None:
        fake_home = ROOT / ".installer-test-codex-home"
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "Install-InstSci.ps1"),
            "-CodexHome",
            str(fake_home),
            "-SkipCli",
            "-DryRun",
        ]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dry run complete", result.stdout)
        self.assertFalse(fake_home.exists())

    def test_installer_copies_skill_into_isolated_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / ".codex"
            command = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "scripts" / "Install-InstSci.ps1"),
                "-CodexHome",
                str(codex_home),
                "-SkipCli",
            ]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((codex_home / "skills" / "instsci" / "SKILL.md").is_file())

    def test_readme_documents_safe_install_choices(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("scripts\\Install-InstSci.ps1", text)
        self.assertIn("uv tool install", text)
        self.assertIn("pipx install", text)
        self.assertNotIn("Invoke-Expression", text)


if __name__ == "__main__":
    unittest.main()
