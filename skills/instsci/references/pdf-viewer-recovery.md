# InstSci PDF Viewer Recovery

Use this when the visible CloakBrowser is already on a PDF page or Chrome PDF viewer, but InstSci did not capture the file.

Preserve the browser state. Do not close CloakBrowser while it is serving as the active access broker.

## Recovery Ladder

1. Check whether the InstSci listener is still alive.
   - Look for the `python -m instsci.cli publisher-batch ...` process for that run.
   - If `primary/summary.json` or `attempts.jsonl` already says `pdf_not_captured` and no Python process remains, the listener is gone.
2. If the listener is alive, use UIA to click the PDF viewer localized `Download` button after visual/UIA evidence shows the PDF is loaded.
3. If the listener is gone, start a fresh single-DOI run with the same institution/profile and a new output directory, then click `Download` while it is waiting.
4. Confirm success from `primary/pdfs/*.pdf`, `complete/pdfs/*.pdf`, `attempts.jsonl`, and `primary/summary_partial.json`.
5. Require `status=success`, nonzero size, and preferably `verified_match=true`.

## Fresh Listener Pattern

```powershell
$doiFile = ".\.tmp\one_doi.txt"
$out = ".\runs\browser_runs_resume\elsevier_retry_YYYYMMDD"
$argString = '-m instsci.cli publisher-batch "' + $doiFile + '" --publisher elsevier --institution "Institution Name" --output "' + $out + '" --login-timeout 600 --pdf-timeout 900'
Start-Process -FilePath "python" -ArgumentList $argString -WorkingDirectory (Get-Location) -RedirectStandardOutput "$out\publisher-batch.stdout.log" -RedirectStandardError "$out\publisher-batch.stderr.log" -WindowStyle Hidden
```

Quote institution names inside the argument string; passing `@(..., "Institution Name", ...)` to `Start-Process -ArgumentList` can split the institution into multiple CLI arguments.

## Viewer Download Button

```powershell
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$p = Get-Process chrome | Where-Object { $_.Path -match 'cloakbrowser' -and $_.MainWindowTitle -match 'ScienceDirect|Chromium' } | Select-Object -First 1
$w = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
$all = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$hasPdf = $false
$target = $null
$downloadLabel = -join ([char]0x4e0b, [char]0x8f7d)
$pdfLoadedLabel = "PDF " + (-join ([char]0x5df2, [char]0x52a0, [char]0x8f7d, [char]0x5b8c, [char]0x6bd5))
$pageContentMarker = -join ([char]0x9875, [char]0x5185, [char]0x5bb9)
for ($i = 0; $i -lt $all.Count; $i++) {
  $e = $all.Item($i)
  $n = [string]$e.Current.Name
  if ($n -eq $pdfLoadedLabel -or $n -match 'PDF loaded' -or ($n -match 'PDF' -and $n.Contains($pageContentMarker))) { $hasPdf = $true }
  if (-not $target -and $n -in @($downloadLabel, 'Download') -and $e.Current.IsEnabled) { $target = $e }
}
if ($hasPdf -and $target) {
  $target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
}
```

The `[char]` expressions avoid mojibake when Markdown is read through a non-UTF-8 PowerShell console.

## Final Delivery Update

After recovery, update the final delivery layer, not only the run folder:

- copy the verified PDF into `final_pdfs` using the existing naming convention
- update `reports/final_manifest.csv`
- update `reports/final_manifest.json` without changing its current top-level shape
- update `reports/final_report.md` counts

On Windows, write Markdown with UTF-8 BOM if PowerShell needs to display Chinese paths correctly.

