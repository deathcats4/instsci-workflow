# InstSci Public Review Package

This package is prepared for external review before public beta release.

Contents:

- `instsci_skill/`: Codex skill instructions, references, and helper scripts.
- `source_patched/`: runnable InstSci source package without local run outputs, build trees, browser binaries, root historical tests, or Python caches.
- `docs/InstSci_user_guide_zh.md`: public Chinese user-facing usage notes.

Notes:

- This package does not include local browser profiles, cookies, PDFs, run outputs, or bundled CloakBrowser binaries.
- Closed-access publisher verification still requires a visible browser and the user's own legal institutional access.
- SSO, CAPTCHA, 2FA, and WAF challenges must be completed by the user. InstSci does not bypass access controls.
- Review commands should disable bytecode writes so generated Python cache folders do not affect `public-audit`.

Recommended review setup:

```powershell
cd .\source_patched
python -m pip install -e .
```

Recommended review commands from the package root:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = '.\source_patched'
python -B -m py_compile (Get-ChildItem .\source_patched\instsci -Recurse -Filter *.py | ForEach-Object FullName)
python -B -m unittest instsci.tests.test_public_audit instsci.tests.test_status_contract instsci.tests.test_zotero_mcp_handoff instsci.tests.test_contract_fixtures -v
python -B -m instsci.cli public-audit .
python -B -m instsci.cli doctor --full --package-path .
```

