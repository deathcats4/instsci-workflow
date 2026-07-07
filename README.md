# InstSci Public Beta

This is a modified public beta / release-candidate build of InstSci for external review and real-user dogfood testing.

InstSci is a research-paper acquisition workflow tool. It looks for Open Access full text first, then helps users use their own legal institutional access through visible-browser workflows when closed-access publisher pages require SSO, CAPTCHA, 2FA, WAF, or institution selection.

## What This Build Emphasizes

- OA-first fast path for papers that do not need browser verification.
- Visible-browser evidence for closed-access publisher results.
- Clear manifest contract: `file_status`, `standard_status`, and `result_evidence`.
- Diagnosable failure states instead of pretending every DOI should succeed.
- Zotero handoff and linked local PDF attachment workflow.
- Clean public package without local profiles, cookies, PDFs, run outputs, or browser binaries.

## Install for Review

```powershell
python -m pip install -e .
```

## Basic Review Commands

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m py_compile (Get-ChildItem .\instsci -Recurse -Filter *.py | ForEach-Object FullName)
python -B -m unittest instsci.tests.test_public_audit instsci.tests.test_status_contract instsci.tests.test_zotero_mcp_handoff instsci.tests.test_contract_fixtures -v
python -B -m instsci.cli public-audit .
python -B -m instsci.cli doctor --full --package-path .
```

## Typical Usage

```powershell
instsci doctor --full
instsci papers .\dois.txt --publisher auto --output .\runs\papers
instsci publisher-doctor --matrix
instsci zotero handoff .\runs\papers --tags project/example
instsci zotero sync .\runs\papers --attachment-mode linked_file
```

## Access and Compliance

This tool is not a publisher bypass tool. It is intended to make legal literature access more batchable, diagnosable, and easier to connect with Zotero. Users must rely on their own Open Access routes, library subscriptions, institutional entitlements, or other lawful access paths.

## More Docs

- `docs/InstSci_user_guide_zh.md`: Chinese user-facing local environment and usage notes.
- `README_REVIEW.md`: review package details and validation commands.
- `NOTICE_MODIFIED.md`: attribution and modified-build notice.
- `skill/`: Codex skill instructions for InstSci workflows.
