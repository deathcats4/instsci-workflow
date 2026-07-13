# InstSci Diagnostic Guide

Use this guide when a run fails, stalls, opens a verification page, or behaves differently from a regular browser.

## Diagnostic Mode

Run the same DOI with `--mode diagnose`:

```powershell
instsci papers doi_one.txt --publisher auto --mode diagnose --output .\runs\diagnose
```

Diagnostic mode keeps the workflow user-safe while collecting more evidence:

- browser watcher prints meaningful blocker changes
- after-run doctor runs with screenshot evidence
- manifest records normalized status and `next_action`
- output remains local instead of being pasted into chat

## Browser Doctor

Manual doctor command:

```powershell
instsci browser-doctor --publisher publisher-name --output .\runs\inspect_publisher
```

Current screenshot-backed browser doctor support is Windows visible-desktop only. On macOS/Linux, treat screenshot inspection as unavailable and rely on saved run artifacts or manual inspection until a platform implementation exists.

JSON output for branching:

```powershell
instsci browser-doctor --publisher publisher-name --output .\runs\inspect_publisher --json
```

Fallback script if the CLI command is missing:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.codex\skills\instsci\scripts\inspect_cloakbrowser.ps1" -Publisher publisher-name -OutputDir ".\runs\inspect_publisher"
```

## Normalized Browser States

- `no_window`: no CloakBrowser window is open; safe to start a single run.
- `blank`: startup/profile contention or stalled launch; stop before retrying.
- `pdf_or_authorized`: page appears authorized or PDF-related; verify manifest/PDF text.
- `auth_required`: institution selection or login is needed.
- `human_verification_required`: visible user-action challenge, such as CAPTCHA, Turnstile, robot checkbox, or manual verification. The user completes it promptly, then the run may continue.
- `waf_blocked`: WAF, Cloudflare, or security loop with no reliable user action available; stop batch runs.
- `access_unavailable`: route visibly lacks entitlement; check a regular browser.
- `publisher_error`: publisher-side content error; retry another DOI or later before judging support.
- `unknown_visible`: inspect screenshot before deciding.

## Troubleshooting Order

1. Check regular browser access under the intended network route.
2. Confirm the same browser profile is not already open unless using the broker.
3. Run one DOI prewarm with the intended profile.
4. If verification appears, complete it quickly and leave CloakBrowser open.
5. If a visible verification control is available, mark `human_verification_required`, let the user complete it, and continue only after rechecking browser state.
6. If WAF loops with no user action available, stop the batch and mark `waf_blocked`.
7. If regular browser also lacks access, mark `access_unavailable`.
8. If multiple PDFs appear, rerun with `--mode diagnose` and inspect candidate ranking.

## VPN and Routing Notes

Different tools may route traffic differently from the regular browser. If a publisher works in a regular browser but not in CloakBrowser:

- verify whether the VPN/proxy applies to the CloakBrowser process
- check whether rule mode excludes publisher domains from the wrong proxy route
- try campus network or institution-supported route first
- avoid changing global network policy blindly during a batch

Document only the general routing lesson in public notes. Keep institution-specific VPN details private.

## Human Verification Timing

Some sites show a short-lived verification prompt. If it is not completed quickly, the page may become a generic problem page. InstSci should pause or notify on `human_verification_required`; the user completes the prompt, then the run continues after a fresh browser-state check.

Use `--mode diagnose` or explicit focus mode for hard cases:

```powershell
instsci papers doi_one.txt --publisher publisher-name --mode diagnose --watch-browser focus
```

